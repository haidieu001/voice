"""HSW context pool - giai hCaptcha bang nhieu context Camoufox song song.

Kien truc mo phong ban v4.1:

* Mot browser Camoufox duy nhat, ben trong la N context doc lap
  (``HSW_CONTEXTS``). Moi context giu mot page rieng.
* Page khong tai trang that cua ElevenLabs. Thay vao do context chan toan bo
  request va ``fulfill()`` mot trang loader sieu nhe ngay tai origin
  ``elevenlabs.io`` - chi traffic cua hCaptcha duoc di tiep. Nho vay moi lan
  solve chi ton vai chuc KB thay vi tai ca trang /text-to-speech.
* Context duoc recycle theo so lan solve hoac tuoi doi; mot task quet nen
  phat hien slot treo (zombie) va mo circuit breaker khi treo qua nhieu.
* Ca browser duoc restart sau mot nguong solve hoac khi gap chuoi 401, de lay
  fingerprint moi.

Module nay khong giu token - viec do van thuoc ve ``core.captcha_solver``.
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

from . import engine_config as cfg

log = logging.getLogger(__name__)

HSW_LOADER_URL = "https://elevenlabs.io/hsw"

# Trang loader: widget hCaptcha vo hinh, tu render + execute khi tai xong.
# Them ?warm=1 de chi tai api.js ma khong ton mot luot solve.
HSW_LOADER_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Loader</title><meta name="viewport" content="width=device-width,initial-scale=1">
</head><body><div id="cap"></div><script>
window.__hcapResult = null;
window.__hcapStage = 'boot';
window.__hcapWid = null;
function __ok(t){ window.__hcapResult = {ok:true, token:t}; }
function __err(e){ window.__hcapResult = {ok:false, error:'error-callback: ' + String(e)}; }
function __exp(){ window.__hcapResult = {ok:false, error:'expired'}; }
function __cexp(){ window.__hcapResult = {ok:false, error:'challenge-expired'}; }
function __close(){ window.__hcapResult = {ok:false, error:'closed'}; }
function __hcapReady(){
  window.__hcapStage = 'api-loaded';
  if (location.search.indexOf('warm') !== -1) { window.__hcapStage = 'warmed'; return; }
  try {
    var wid = hcaptcha.render('cap', {
      sitekey: '__SITEKEY__',
      size: 'invisible',
      callback: __ok,
      'error-callback': __err,
      'expired-callback': __exp,
      'challenge-expired-callback': __cexp,
      'close-callback': __close
    });
    window.__hcapWid = wid;
    window.__hcapStage = 'rendered';
    var p = hcaptcha.execute(wid, {async: true});
    window.__hcapStage = 'executed';
    if (p && p.then) {
      p.then(function(r){ if (r && r.response) __ok(r.response); }).catch(__err);
    }
  } catch (e) { __err(e); }
}
</script>
<script src="https://hcaptcha.com/1/api.js?onload=__hcapReady&render=explicit&recaptchacompat=off"
        async defer></script></body></html>"""

# Hook window.hsw de ghi lai tham so proof-of-work. Chi phuc vu chan doan khi
# solve that bai; khong gui gi ra ngoai.
HSW_HOOK_SCRIPT = """(function(){
  try {
    if (window.__hsw_hook_installed) return;
    window.__hsw_hook_installed = true;
    window.__hsw_captures = [];
    var _real;
    Object.defineProperty(window, 'hsw', {
      configurable: true,
      get: function(){ return _real; },
      set: function(fn){
        if (typeof fn !== 'function') { _real = fn; return; }
        _real = function(){
          try {
            var args = Array.from(arguments);
            var mode = args.length === 1 ? 'pow' : args[0];
            var arg = args.length === 1 ? args[0] : args[1];
            var preview;
            try { preview = typeof arg === 'string' ? arg : JSON.stringify(arg); }
            catch (_) { preview = '<unstringifiable>'; }
            window.__hsw_captures.push({
              t: Date.now(), mode: mode, argType: typeof arg,
              argLen: preview ? preview.length : 0,
              frame: (location && location.href) || 'unknown'
            });
            if (window.__hsw_captures.length > 20) window.__hsw_captures.shift();
          } catch (_) {}
          return fn.apply(this, arguments);
        };
      }
    });
  } catch (e) {
    try { console.log('__HSW_HOOK_INSTALL_ERR__ ' + (e && e.message)); } catch (_) {}
  }
})();"""


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_hcaptcha_host(url: str) -> bool:
    host = _host_of(url)
    return host == "hcaptcha.com" or host.endswith(".hcaptcha.com")


def _is_el_api_host(url: str) -> bool:
    """API cua ElevenLabs - de nguyen cho ``browser_synthesize`` goi TTS."""
    return _host_of(url) == "api.elevenlabs.io"


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Chi nhan UUID nam ngay canh chu "sitekey". Trang /text-to-speech co hang
# chuc UUID la ten file mau giong noi - bat bua UUID dau tien se lay nham va
# hCaptcha tra ve "network-error".
_SITEKEY_RE = re.compile(
    r"(?:HCAPTCHA_SITEKEY[A-Z_]*|sitekey)[\"'\s:=]{1,8}(" + _UUID + r")", re.I
)


def discover_sitekey(timeout: float = 10.0) -> str:
    """Doc sitekey hien hanh tu trang ElevenLabs; khong chac chan thi giu hang so."""
    override = (os.environ.get("VOICE_HCAPTCHA_SITEKEY") or "").strip()
    if re.fullmatch(_UUID, override or ""):
        log.info("[HSW] Sitekey lay tu VOICE_HCAPTCHA_SITEKEY")
        return override
    try:
        import httpx

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(
                "https://elevenlabs.io/text-to-speech",
                headers={"User-Agent": cfg.build_el_headers()["User-Agent"]},
            )
        if resp.status_code == 200:
            m = _SITEKEY_RE.search(resp.text)
            if m and m.group(1) != cfg.HCAPTCHA_SITEKEY:
                log.info("[HSW] Sitekey moi phat hien: %s", m.group(1))
                return m.group(1)
    except Exception as e:
        log.debug("[HSW] discover_sitekey bo qua: %s", str(e)[:80])
    return cfg.HCAPTCHA_SITEKEY


# ── Cach ly profile Playwright ──────────────────────────────────────────

_PW_PROFILE_KEYS = ("playwright", "camoufox", "mozrunner", "rust_mozprofile")


def _temp_root() -> str:
    return tempfile.gettempdir()


def _list_profile_dirs() -> set:
    try:
        return {
            n for n in os.listdir(_temp_root())
            if any(k in n.lower() for k in _PW_PROFILE_KEYS)
        }
    except Exception:
        return set()


def _snapshot_pw_profiles() -> set:
    """Chup danh sach thu muc profile tam truoc khi mo browser."""
    return _list_profile_dirs()


def _detect_new_pw_profile(before: set) -> str:
    """Tim thu muc profile vua duoc tao ra boi lan launch gan nhat."""
    fresh = _list_profile_dirs() - before
    if not fresh:
        return ""
    paths = [os.path.join(_temp_root(), n) for n in fresh]
    paths.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    return paths[-1]


def purge_stale_pw_profiles(max_age_sec: float = 3600.0) -> int:
    """Xoa profile tam cu con sot lai tu cac lan chay truoc."""
    root = _temp_root()
    removed = 0
    cutoff = time.time() - max_age_sec
    try:
        names = os.listdir(root)
    except Exception:
        return 0
    for name in names:
        if not any(k in name.lower() for k in _PW_PROFILE_KEYS):
            continue
        path = os.path.join(root, name)
        try:
            if not os.path.isdir(path) or os.path.getmtime(path) > cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        except Exception:
            continue
    if removed:
        log.info("[ISO] Da don %d profile tam cu", removed)
    return removed


def fresh_identity_prefs() -> dict:
    """firefox_user_prefs: tat dong bo/telemetry, moi lan chay la mot may moi."""
    return {
        "browser.cache.disk.enable": False,
        "browser.sessionstore.resume_from_crash": False,
        "browser.shell.checkDefaultBrowser": False,
        "browser.startup.homepage_override.mstone": "ignore",
        "datareporting.healthreport.uploadEnabled": False,
        "datareporting.policy.dataSubmissionEnabled": False,
        "toolkit.telemetry.enabled": False,
        "toolkit.telemetry.unified": False,
        "extensions.getAddons.cache.enabled": False,
        "app.update.auto": False,
        "network.captive-portal-service.enabled": False,
        "network.connectivity-service.enabled": False,
        "dom.push.enabled": False,
        "media.peerconnection.enabled": False,
    }


def isolated_browser_env() -> dict:
    """Bien moi truong cho tien trinh browser - chan ghi de tu shell nguoi dung."""
    env = {
        "MOZ_DISABLE_AUTO_SAFE_MODE": "1",
        "MOZ_CRASHREPORTER_DISABLE": "1",
    }
    lang = os.environ.get("LANG")
    if lang:
        env["LANG"] = lang
    return env


# ── Slot & pool ─────────────────────────────────────────────────────────


@dataclass
class HswSlot:
    index: int
    ctx: object = None
    page: object = None
    busy: bool = False
    busy_since: float = 0.0
    solve_count: int = 0
    last_solve_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    needs_recycle: bool = False

    def age(self) -> float:
        return time.time() - self.created_at

    def is_stale(self) -> bool:
        return (
            self.needs_recycle
            or self.solve_count >= cfg.HSW_RECYCLE_AFTER_SOLVES
            or self.age() >= cfg.HSW_RECYCLE_AFTER_AGE_SEC
        )


def build_launch_kwargs(
    headless: bool = True,
    proxy_url: Optional[str] = None,
    block_images: bool = True,
) -> dict:
    """Tham so mo Camoufox, dung chung cho ca che do pool lan che do thu cong.

    ``block_images=False`` cho che do thu cong: nguoi dung phai nhin thay anh
    cua visual challenge.
    """
    kwargs = {
        "headless": headless,
        "i_know_what_im_doing": True,
        "firefox_user_prefs": fresh_identity_prefs(),
        "env": isolated_browser_env(),
        "block_images": block_images,
        "enable_cache": True,
    }

    exe = cfg.resolve_camoufox_exe()
    if exe:
        kwargs["executable_path"] = exe
        ff = cfg.resolve_camoufox_ff_version(exe)
        if ff:
            # Khong truyen thi Camoufox van di tra ban cai trong cache cua no
            # va bao "official/stable is not installed" du binary da co san.
            kwargs["ff_version"] = ff
        log.info("[Camoufox] Binary: %s (ff=%s)", exe, ff or "?")
    else:
        log.info("[Camoufox] Khong thay binary trong runtime/ - dung ban cua package")

    ubo = cfg.resolve_ubo_dir()
    if ubo:
        kwargs["addons"] = [ubo]
        log.info("[Camoufox] uBO: %s", ubo)
    else:
        log.info("[Camoufox] Khong thay uBO hop le - chay khong addon")

    # Luon loai addon mac dinh: hoac ta tu cung cap duong dan, hoac chay khong
    # addon. Nho vay cache hong cua Camoufox khong con lam chet app voi loi
    # "manifest.json is missing".
    try:
        from camoufox.addons import DefaultAddons

        kwargs["exclude_addons"] = list(DefaultAddons)
    except Exception:
        pass

    os_name = (os.environ.get("VOICE_HSW_BROWSER_OS") or "").strip().lower()
    if os_name in ("windows", "macos", "linux"):
        kwargs["os"] = os_name

    if proxy_url:
        kwargs["proxy"] = {"server": proxy_url}
    return kwargs


class HswPool:
    """Pool context giai hCaptcha. Cac ham async deu chay trong mot event loop."""

    def __init__(
        self,
        headless: bool = True,
        proxy_url: Optional[str] = None,
        on_status: Optional[Callable] = None,
        num_contexts: int = 0,
    ):
        self._headless = headless
        self._proxy_url = proxy_url
        self._on_status = on_status or (lambda s: None)
        self._num_contexts = num_contexts or cfg.HSW_CONTEXTS

        self._pw = None
        self._browser = None
        self._slots: List[HswSlot] = []
        self._sitekey = cfg.HCAPTCHA_SITEKEY
        self._loader_html = HSW_LOADER_HTML.replace("__SITEKEY__", cfg.HCAPTCHA_SITEKEY)
        self._profile_dir = ""

        self._slot_free = None       # asyncio.Condition, tao trong start()
        self._recycle_sem = None     # gioi han so context recycle dong thoi
        self._restart_lock = None
        self._gate_lock = None

        self._sweeper_task = None
        self._build_task = None
        self._running = False
        self._last_solve_ts = 0.0
        self._total_solves = 0
        self._last_restart_at = 0.0
        self._restart_in_progress = False
        self._zombie_events: List[float] = []
        self._401_events: List[float] = []
        self._last_slot_idx = -1

    # ── vong doi ────────────────────────────────────────────────

    async def start(self, pw) -> None:
        self._pw = pw
        self._slot_free = asyncio.Condition()
        self._recycle_sem = asyncio.Semaphore(cfg.HSW_MAX_CONCURRENT_RECYCLE)
        self._restart_lock = asyncio.Lock()
        self._gate_lock = asyncio.Lock()

        purge_stale_pw_profiles()
        loop = asyncio.get_event_loop()
        self._sitekey = await loop.run_in_executor(None, discover_sitekey)
        self._loader_html = HSW_LOADER_HTML.replace("__SITEKEY__", self._sitekey)

        log.info(
            "[HSW] Khoi tao solver (%d context, uBO=%s)...",
            self._num_contexts,
            "co" if cfg.resolve_ubo_dir() else "khong",
        )
        await self._launch_browser_and_contexts()
        self._running = True
        self._sweeper_task = asyncio.create_task(self._zombie_sweeper())
        log.info(
            "[HSW] San sang: %d/%d context | sweep=%ds recycle=%d solves/%ds",
            len(self._slots),
            self._num_contexts,
            cfg.HSW_ZOMBIE_SWEEP_INTERVAL_SEC,
            cfg.HSW_RECYCLE_AFTER_SOLVES,
            cfg.HSW_RECYCLE_AFTER_AGE_SEC,
        )

    def _launch_kwargs(self) -> dict:
        return build_launch_kwargs(
            headless=self._headless,
            proxy_url=self._proxy_url,
            block_images=True,
        )

    async def _launch_browser_and_contexts(self) -> None:
        from camoufox.async_api import AsyncNewBrowser

        before = _snapshot_pw_profiles()
        self._browser = await AsyncNewBrowser(self._pw, **self._launch_kwargs())
        self._profile_dir = _detect_new_pw_profile(before)
        if self._profile_dir:
            log.debug("[ISO] Profile tam: %s", self._profile_dir)

        # Chi cho context dau tien; du de solve ngay. Nhung context con lai
        # dung nen de token dau tien khong phai doi ca pool khoi tao xong.
        self._slots = []
        first = HswSlot(index=0)
        await self._attach_context(first)
        self._slots.append(first)
        log.info("[HSW] Context [0] san sang - bat dau nhan viec")

        if self._num_contexts > 1:
            self._build_task = asyncio.create_task(self._build_remaining_contexts())

    async def _build_remaining_contexts(self) -> None:
        """Dung not cac context con lai o nen."""
        try:
            for i in range(1, self._num_contexts):
                if self._browser is None:
                    return
                slot = HswSlot(index=i)
                try:
                    await self._attach_context(slot)
                except Exception as e:
                    log.warning(
                        "[HSW] Context [%d] tao that bai: %s", i, str(e)[:100]
                    )
                    continue
                self._slots.append(slot)
                log.info("[HSW] Context [%d] san sang (%d/%d)",
                         i, len(self._slots), self._num_contexts)
                async with self._slot_free:
                    self._slot_free.notify()
        except asyncio.CancelledError:
            return
        finally:
            self._build_task = None

    async def _attach_context(self, slot: HswSlot) -> None:
        ctx = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            color_scheme="dark",
            reduced_motion="no-preference",
            forced_colors="none",
        )
        await ctx.add_init_script(HSW_HOOK_SCRIPT)
        await ctx.route("**/*", self._route)
        page = await ctx.new_page()
        try:
            await page.set_extra_http_headers({
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            })
        except Exception as e:
            log.debug("[HSW] set_extra_http_headers bo qua: %s", str(e)[:60])

        slot.ctx = ctx
        slot.page = page
        slot.created_at = time.time()
        slot.solve_count = 0
        slot.needs_recycle = False
        await self._prewarm(page)

    async def _prewarm(self, page) -> None:
        """Tai truoc api.js cua hCaptcha ma khong ton mot luot solve."""
        try:
            await page.goto(HSW_LOADER_URL + "?warm=1", wait_until="commit", timeout=20000)
            await page.wait_for_function(
                "() => window.__hcapStage === 'warmed'", timeout=15000
            )
        except Exception as e:
            log.debug("[HSW] pre-warm bo qua: %s", str(e)[:70])

    async def _route(self, route, request):
        url = request.url
        try:
            if url.startswith(HSW_LOADER_URL):
                await route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=self._loader_html,
                )
                return
            if _is_hcaptcha_host(url) or _is_el_api_host(url):
                await route.continue_()
                return
            await route.abort()
        except Exception:
            # Route da bi huy (page dong giua chung) - bo qua.
            pass

    async def close(self) -> None:
        self._running = False
        if self._sweeper_task:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._sweeper_task = None
        await self._teardown_browser_only()

    async def _teardown_browser_only(self) -> None:
        if self._build_task:
            self._build_task.cancel()
            try:
                await self._build_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._build_task = None
        for slot in self._slots:
            try:
                if slot.ctx:
                    await slot.ctx.close()
            except Exception:
                pass
        self._slots = []
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                log.debug("[HSW] browser.close loi: %s", str(e)[:60])
            self._browser = None
        self._purge_own_profile_dir()

    def _purge_own_profile_dir(self) -> None:
        if not self._profile_dir:
            return
        try:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            log.debug("[ISO] Da xoa profile tam")
        except Exception as e:
            log.debug("[ISO] purge profile loi: %s", str(e)[:60])
        self._profile_dir = ""

    # ── muon / tra slot ─────────────────────────────────────────

    async def _acquire(self) -> Optional[HswSlot]:
        deadline = time.time() + cfg.HSW_ACQUIRE_TIMEOUT_SEC
        async with self._slot_free:
            while self._running:
                for slot in self._slots:
                    if not slot.busy:
                        slot.busy = True
                        slot.busy_since = time.time()
                        self._last_slot_idx = slot.index
                        return slot
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._slot_free.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return None
        return None

    async def _release(self, slot: HswSlot) -> None:
        slot.busy = False
        slot.busy_since = 0.0
        if slot.is_stale():
            asyncio.create_task(self._recycle_context(slot, "stale"))
        async with self._slot_free:
            self._slot_free.notify()

    async def _recycle_context(self, slot: HswSlot, reason: str) -> None:
        async with self._recycle_sem:
            if slot.busy:
                slot.needs_recycle = True
                return
            slot.busy = True
            try:
                try:
                    if slot.ctx:
                        await slot.ctx.close()
                except Exception:
                    pass
                await self._attach_context(slot)
                log.info(
                    "[HSW] Context [%d] da recycle (%s, %d solve)",
                    slot.index, reason, slot.solve_count,
                )
            except Exception as e:
                log.warning(
                    "[HSW] Recycle context [%d] that bai: %s", slot.index, str(e)[:90]
                )
            finally:
                slot.busy = False
                async with self._slot_free:
                    self._slot_free.notify()

    def force_recycle_slot(self, index: int, reason: str = "401") -> None:
        """Danh dau mot slot phai lam moi. Goi tu trong event loop cua pool."""
        for slot in self._slots:
            if slot.index != index:
                continue
            if slot.busy:
                slot.needs_recycle = True
                log.info(
                    "[HSW] Slot [%d] dang ban - se recycle khi tra (%s)", index, reason
                )
            else:
                asyncio.create_task(self._recycle_context(slot, reason))
            return
        log.debug("[HSW] force_recycle_slot: slot %d khong ton tai", index)

    def get_recent_slot_idx(self) -> int:
        return self._last_slot_idx

    # ── quet zombie + circuit breaker ───────────────────────────

    async def _zombie_sweeper(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(cfg.HSW_ZOMBIE_SWEEP_INTERVAL_SEC)
                now = time.time()
                for slot in self._slots:
                    if not slot.busy or not slot.busy_since:
                        continue
                    stuck = now - slot.busy_since
                    if stuck < cfg.HSW_ZOMBIE_STUCK_THRESHOLD_SEC:
                        continue
                    log.warning(
                        "[HSW] Slot [%d] treo %.0fs - cuong che recycle",
                        slot.index, stuck,
                    )
                    self._zombie_events.append(now)
                    slot.busy = False
                    slot.busy_since = 0.0
                    await self._recycle_context(slot, "zombie")

                cutoff = now - cfg.HSW_CB_ZOMBIE_WINDOW_SEC
                self._zombie_events = [t for t in self._zombie_events if t >= cutoff]
                if len(self._zombie_events) >= cfg.HSW_CB_ZOMBIE_THRESHOLD:
                    log.error(
                        "[HSW] Circuit breaker: %d zombie trong %ds - restart browser",
                        len(self._zombie_events), cfg.HSW_CB_ZOMBIE_WINDOW_SEC,
                    )
                    self._zombie_events = []
                    await self.restart_browser("zombie_circuit_breaker")
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("[HSW] Sweeper loi: %s", str(e)[:90])

    # ── restart ca browser ──────────────────────────────────────

    def note_401(self) -> bool:
        """Ghi nhan mot lan 401. Tra ve True neu du chuoi de restart browser."""
        now = time.time()
        cutoff = now - cfg.HSW_401_STREAK_WINDOW_SEC
        self._401_events = [t for t in self._401_events if t >= cutoff]
        self._401_events.append(now)

        if cfg.HSW_RECYCLE_ON_401 and self._last_slot_idx >= 0:
            self.force_recycle_slot(self._last_slot_idx, "401")

        if len(self._401_events) >= cfg.HSW_401_STREAK_TO_RESTART:
            log.error(
                "[HSW] Chuoi %d lan 401 trong %ds - can restart browser",
                len(self._401_events), cfg.HSW_401_STREAK_WINDOW_SEC,
            )
            self._401_events = []
            return True
        return False

    async def restart_browser(self, reason: str) -> bool:
        if self._restart_in_progress:
            log.info("[HSW] Restart dang chay - bo qua (%s)", reason)
            return False
        since = time.time() - self._last_restart_at
        if self._last_restart_at and since < cfg.HSW_BROWSER_RESTART_COOLDOWN_SEC:
            log.info(
                "[HSW] Bo qua restart (%s): moi restart %.0fs truoc, cooldown %ds",
                reason, since, cfg.HSW_BROWSER_RESTART_COOLDOWN_SEC,
            )
            return False

        async with self._restart_lock:
            self._restart_in_progress = True
            t0 = time.time()
            log.warning(
                "[HSW] Restart browser (%s), total_solves=%d", reason, self._total_solves
            )
            self._on_status("browser_restart")
            try:
                await self._teardown_browser_only()
                await self._launch_browser_and_contexts()
                self._last_restart_at = time.time()
                self._total_solves = 0
                log.info(
                    "[HSW] Restart xong sau %.1fs (%s) - fingerprint moi",
                    time.time() - t0, reason,
                )
                return True
            except Exception as e:
                log.error("[HSW] Restart THAT BAI: %s", e, exc_info=True)
                return False
            finally:
                self._restart_in_progress = False
                async with self._slot_free:
                    self._slot_free.notify_all()

    async def _maybe_restart_after_solves(self) -> None:
        if self._total_solves >= cfg.HSW_BROWSER_RESTART_AFTER_SOLVES:
            await self.restart_browser("solve_quota")

    # ── FIFO gate ───────────────────────────────────────────────

    async def _fifo_gate(self) -> None:
        """Giu khoang cach toi thieu giua hai lan solve de khong bi rate-limit."""
        async with self._gate_lock:
            wait = cfg.FIFO_GATE_MIN_SEC - (time.monotonic() - self._last_solve_ts)
            if wait > 0:
                log.debug("[HSW] solve-gate: cho %.2fs", wait)
                await asyncio.sleep(wait)
            self._last_solve_ts = time.monotonic()

    # ── solve ───────────────────────────────────────────────────

    async def solve(self) -> Tuple[Optional[str], str]:
        """Giai mot captcha. Tra ve ``(token, reason)``.

        reason: ``ok`` | ``demoted`` | ``timeout`` | ``rate_limited`` |
        ``error`` | ``no_slot`` | ``stopped``.
        """
        if not self._running:
            return None, "stopped"

        await self._fifo_gate()
        slot = await self._acquire()
        if slot is None:
            log.warning(
                "[HSW] Khong muon duoc slot sau %ds", cfg.HSW_ACQUIRE_TIMEOUT_SEC
            )
            return None, "no_slot"

        try:
            token, reason = await self._solve_on_slot(slot)
            slot.solve_count += 1
            slot.last_solve_at = time.time()
            if reason == "ok":
                self._total_solves += 1
            return token, reason
        finally:
            await self._release(slot)
            if self._total_solves and self._total_solves % 10 == 0:
                await self._maybe_restart_after_solves()

    async def _solve_on_slot(self, slot: HswSlot) -> Tuple[Optional[str], str]:
        page = slot.page
        if page is None or page.is_closed():
            slot.needs_recycle = True
            return None, "error"

        traffic = {"n": 0}
        console_lines: List[str] = []
        page_errors: List[str] = []

        def _on_request(req):
            if _is_hcaptcha_host(req.url):
                traffic["n"] += 1

        def _on_console(msg):
            console_lines.append("%s: %s" % (msg.type, msg.text[:160]))

        def _on_pageerror(err):
            page_errors.append(str(err)[:160])

        page.on("request", _on_request)
        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)

        try:
            await page.goto(HSW_LOADER_URL, wait_until="commit", timeout=30000)
            try:
                await page.wait_for_function(
                    "() => window.__hcapResult !== null",
                    timeout=int(cfg.CAPTCHA_TIMEOUT * 1000),
                )
            except Exception:
                await self._dump_diagnostics(
                    page, slot, traffic, console_lines, page_errors
                )
                slot.needs_recycle = True
                return None, "timeout"

            result = await page.evaluate("window.__hcapResult")
            if not result:
                return None, "error"

            if result.get("ok") and result.get("token"):
                token = result["token"]
                log.info("[HSW] Slot [%d] lay duoc token, len=%d", slot.index, len(token))
                return token, "ok"

            err = str(result.get("error") or "unknown")
            log.warning("[HSW] Slot [%d] solve that bai: %s", slot.index, err[:120])
            slot.needs_recycle = True
            low = err.lower()
            if "E1_" in err or "challenge" in low:
                return None, "demoted"
            if "rate" in low:
                return None, "rate_limited"
            return None, "error"
        except Exception as e:
            log.warning("[HSW] Slot [%d] loi: %s", slot.index, str(e)[:120])
            slot.needs_recycle = True
            return None, "error"
        finally:
            for event, handler in (
                ("request", _on_request),
                ("console", _on_console),
                ("pageerror", _on_pageerror),
            ):
                try:
                    page.remove_listener(event, handler)
                except Exception:
                    pass

    async def _dump_diagnostics(self, page, slot, traffic, console_lines, page_errors):
        """In du lieu chan doan khi widget khong tra ket qua kip thoi."""
        stage = "unknown"
        try:
            stage = await page.evaluate("window.__hcapStage")
        except Exception:
            pass
        log.warning(
            "[HSW] Slot [%d] het %ds ma widget chua xong (stage=%s)",
            slot.index, cfg.CAPTCHA_TIMEOUT, stage,
        )
        if traffic["n"]:
            log.warning("[HSW]   traffic hCaptcha: %d request", traffic["n"])
        else:
            log.warning("[HSW]   traffic hCaptcha: KHONG CO (mang bi chan?)")
        for line in console_lines[-5:]:
            log.warning("[HSW]   console: %s", line)
        for line in page_errors[-3:]:
            log.warning("[HSW]   pageerror: %s", line)
        try:
            probe = await page.evaluate(
                """() => {
                    const ifr = Array.from(document.querySelectorAll('iframe'));
                    return {
                        iframeCount: ifr.length,
                        iframes: ifr.slice(0, 3).map(f => ({
                            src: (f.src || '').slice(0, 100),
                            w: f.offsetWidth, h: f.offsetHeight,
                            visible: f.offsetParent !== null
                        })),
                        hcapDefined: typeof window.hcaptcha,
                        widId: window.__hcapWid || null,
                        hswCaptures: (window.__hsw_captures || []).length
                    };
                }"""
            )
            log.warning("[HSW]   DOM probe: %s", probe)
        except Exception as e:
            log.warning("[HSW]   DOM probe loi: %s", str(e)[:80])

    # ── dung chung cho phan TTS trong browser ───────────────────

    def primary_page(self):
        """Page dau tien con song - dung cho cookie/session va TTS."""
        for slot in self._slots:
            if slot.page is not None and not slot.page.is_closed():
                return slot.page
        return None

    def primary_context(self):
        for slot in self._slots:
            if slot.ctx is not None:
                return slot.ctx
        return None

    async def evaluate_on_slot(self, js: str):
        """Chay JS tren mot slot ranh (giu nguyen origin elevenlabs.io)."""
        slot = await self._acquire()
        if slot is None:
            return {"error": "no_slot"}
        try:
            if slot.page is None or slot.page.is_closed():
                slot.needs_recycle = True
                return {"error": "page_closed"}
            return await slot.page.evaluate(js)
        finally:
            await self._release(slot)

    # ── trang thai ──────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "contexts": len(self._slots),
            "busy": sum(1 for s in self._slots if s.busy),
            "total_solves": self._total_solves,
            "sitekey": self._sitekey,
            "restart_in_progress": self._restart_in_progress,
            "zombie_events": len(self._zombie_events),
            "slots": [
                {
                    "index": s.index,
                    "busy": s.busy,
                    "solves": s.solve_count,
                    "age": round(s.age()),
                    "needs_recycle": s.needs_recycle,
                }
                for s in self._slots
            ],
        }
