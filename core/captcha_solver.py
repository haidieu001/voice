"""User-assisted ElevenLabs CAPTCHA flow.

Camoufox opens a visible ElevenLabs page and the user completes any challenge
presented by hCaptcha. The resulting token stays in memory and is used only by
the local application.
"""

import asyncio
import concurrent.futures
import os
import json
import time
import logging
import threading
from typing import Optional, Callable

log = logging.getLogger(__name__)

# Token cache
_token_cache = {
    "token": None,
    "expires_at": 0.0,
}
_lock = threading.Lock()

# Loi khong the tu phuc hoi — bao thang len UI thay vi im lang thu lai mai.
_FATAL_KEYWORDS = (
    "is not installed", "camoufox fetch", "official/stable",
    "not found", "No such file", "manifest.json", "Addon path",
)

TOKEN_LIFETIME = 90   # giây — v4.1 thực tế dùng ~90s (không phải 110s)
TOKEN_REFRESH_THRESHOLD = 20  # Refresh khi còn < 20s

SITEKEY = "8e58fe8c-1a48-4f94-88ae-8e90b586a192"  # NEXT_PUBLIC_HCAPTCHA_SITEKEY_ANON_REQUEST
EL_PAGE = "https://elevenlabs.io/text-to-speech"

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_FILE = os.path.join(_BASE, "el_session.json")

# Global solver instance (set khi start())
_solver_instance: Optional["CaptchaSolver"] = None

def get_el_cookies() -> list:
    """Lấy cookies từ browser session."""
    if _solver_instance and _solver_instance._context:
        pass  # cookies lấy qua context.cookies() async, dùng _cookie_cache
    with _cookie_lock:
        return list(_cookie_cache)

_cookie_cache: list = []
_cookie_lock = threading.Lock()

def _update_cookie_cache(cookies: list):
    with _cookie_lock:
        _cookie_cache.clear()
        _cookie_cache.extend(cookies)

# HTML giữ cho /captcha-page endpoint (tương thích)
CAPTCHA_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Voice Pro</title></head>
<body style="background:#0f172a;color:#94a3b8;font-family:system-ui;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-size:1.1rem">
⚡ ElevenLabs Voice Pro - Đang lấy token...
</body></html>"""

# JS inject hCaptcha vao trang EL — demote detection + auto-load CDN
_INJECT_JS = f"""
(async function() {{
    if (window.__elSolverActive) return;
    window.__elSolverActive = true;
    window.__elToken = null;

    // Kiem tra ngay: dang bi visual challenge (demote) roi?
    const isChallenged = () => !!document.querySelector(
        'iframe[src*="hcaptcha.com/challenge"], iframe[src*="newassets.hcaptcha.com/captcha"]'
    );
    if (isChallenged()) {{
        window.__elToken = {{ok: false, error: 'demoted', demoted: true}};
        return;
    }}

    // Buoc 1: Cho hCaptcha co san (trang EL tu load)
    let ready = await new Promise(res => {{
        const end = Date.now() + 8000;
        const t = setInterval(() => {{
            if (typeof hcaptcha !== 'undefined') {{ clearInterval(t); res(true); }}
            if (Date.now() > end) {{ clearInterval(t); res(false); }}
        }}, 200);
    }});

    // Buoc 2: Neu chua co -> tu inject script tu CDN hCaptcha
    if (!ready) {{
        console.log('[ELSolver] hCaptcha chua load, dang inject tu CDN...');
        const existing = document.querySelector('script[src*="hcaptcha.com"]');
        await new Promise((resolve) => {{
            if (existing) {{
                // Script da co nhung chua init xong, cho them
                setTimeout(resolve, 4000);
                return;
            }}
            const s = document.createElement('script');
            s.src = 'https://js.hcaptcha.com/1/api.js?render=explicit&recaptchacompat=off';
            s.async = true;
            s.onload = () => setTimeout(resolve, 2000);
            s.onerror = () => resolve();
            document.head.appendChild(s);
            setTimeout(resolve, 10000);
        }});
        ready = typeof hcaptcha !== 'undefined';
    }}

    if (!ready) {{
        window.__elToken = {{error:'hcaptcha_not_loaded'}};
        return;
    }}

    // Buoc 3: Cho hcaptcha.ready() neu co
    try {{
        if (typeof hcaptcha.ready === 'function') {{
            await new Promise(res => hcaptcha.ready(res));
        }}
    }} catch(e) {{ /* ignore */ }}

    // Buoc 4: Render + execute widget invisible
    let c = document.getElementById('__el_cap');
    if (!c) {{
        c = document.createElement('div');
        c.id = '__el_cap';
        c.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
        document.body.appendChild(c);
    }}

    try {{
        const wid = hcaptcha.render(c, {{
            sitekey: '8e58fe8c-1a48-4f94-88ae-8e90b586a192',
            size: 'invisible',
            callback: t => {{ window.__elToken = {{ok:true, token:t}}; }},
            'error-callback': e => {{ window.__elToken = {{ok:false, error:String(e)}}; }},
            'expired-callback': () => {{ window.__elToken = {{ok:false, error:'expired'}}; }},
        }});
        const p = hcaptcha.execute(wid, {{async:true}});
        if (p && p.then) p.then(r => {{
            if (r && r.response) window.__elToken = {{ok:true, token:r.response}};
        }}).catch(e => {{ window.__elToken = {{ok:false, error:String(e)}}; }});
    }} catch(e) {{
        window.__elToken = {{ok:false, error:'render:'+String(e)}};
    }}
}})();
"""

# JS poll demote status từ Python (không cần inject lại)
_CHECK_DEMOTE_JS = """
(function() {
    const iframe = document.querySelector(
        'iframe[src*="hcaptcha.com/challenge"], iframe[src*="newassets.hcaptcha.com/captcha"]'
    );
    return {demoted: !!iframe};
})()
"""

# Auto-solve: invisible widget HTML (khong inject vao EL page)
_WIDGET_HTML = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>hCap</title></head>'
    '<body><div id="cap"></div>'
    '<script>'
    'var __hcap_token=null,__hcap_error=null,__hcap_expired=false;'
    'function __ok(t){window.__hcap_token=t;}'
    'function __err(e){window.__hcap_error=String(e||"err");}'
    'function __exp(){window.__hcap_expired=true;}'
    'function __hcapReady(){'
    'try{hcaptcha.render("cap",{'
    'sitekey:"8e58fe8c-1a48-4f94-88ae-8e90b586a192",size:"invisible",'
    'callback:__ok,"error-callback":__err,"expired-callback":__exp'
    '});hcaptcha.execute();}catch(e){window.__hcap_error="render:"+String(e);}}'
    '</script>'
    '<script src="https://js.hcaptcha.com/1/api.js?onload=__hcapReady&render=explicit'
    '&host=elevenlabs.io" async defer></script>'
    '</body></html>'
)


def _repair_camoufox_addons() -> None:
    """Xoa addon dir hong (thieu manifest.json) de Camoufox tai lai.

    Camoufox chi kiem tra thu muc addon co ton tai hay khong. Neu lan giai nen
    truoc bi ngat giua chung thi thu muc van con nhung rong, va moi lan khoi
    dong sau deu chet voi "manifest.json is missing".
    """
    try:
        import shutil
        from camoufox.addons import ADDONS_DIR
    except Exception:
        return
    try:
        addons_dir = str(ADDONS_DIR)
        if not os.path.isdir(addons_dir):
            return
        for name in os.listdir(addons_dir):
            path = os.path.join(addons_dir, name)
            if not os.path.isdir(path):
                continue
            if os.path.exists(os.path.join(path, "manifest.json")):
                continue
            log.warning("[Camoufox] Addon '%s' hong (thieu manifest.json) - xoa de tai lai", name)
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        log.warning("[Camoufox] Khong kiem tra duoc addons: %s", e)


async def _new_browser_with_fallback(pw, kwargs: dict):
    """Khoi dong Camoufox; neu addon loi thi chay tiep ma khong dung addon."""
    from camoufox.async_api import AsyncNewBrowser
    try:
        return await AsyncNewBrowser(pw, **kwargs)
    except Exception as e:
        err = str(e)
        if "manifest.json" not in err and "Addon path" not in err:
            raise
        log.warning(
            "[Camoufox] Addon loi (%s) - khoi dong lai khong dung addon", err[:90]
        )
        retry = dict(kwargs)
        retry.pop("addons", None)
        try:
            from camoufox.addons import DefaultAddons

            retry["exclude_addons"] = list(DefaultAddons)
        except Exception:
            pass
        return await AsyncNewBrowser(pw, **retry)


class CaptchaSolver:
    """ElevenLabs anonymous TTS solver - browser-based approach."""

    def __init__(self, on_status: Optional[Callable] = None):
        global _solver_instance
        _solver_instance = self
        self._browser = None
        self._context = None
        self._page = None
        self._running = False
        self._on_status = on_status or (lambda s: None)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread = None
        self._proxy_url: Optional[str] = None
        self._headless = True
        self._manual_mode = False
        self._status = "idle"
        self._tts_lock = None
        self._cap_page = None   # Trang rieng cho auto-captcha widget
        self._pool = None       # HswPool — chi dung o che do tu dong
        self._fatal_error: Optional[str] = None
        # ── GAP 1 + GAP 2: Robustness counters ──
        self._consecutive_401: int = 0    # Số lần 401 liên tiếp từ TTS
        self._demote_count: int = 0       # Số lần bị demote sang visual challenge
        self._need_full_restart: bool = False  # Signal yêu cầu restart browser hoàn toàn

    @property
    def status(self) -> str:
        return self._status

    def _emit_status(self, status: str):
        """Store the latest state and publish it to the UI."""
        self._status = status
        self._on_status(status)

    def set_proxy(self, proxy_url: str):
        self._proxy_url = proxy_url

    @property
    def has_valid_token(self) -> bool:
        with _lock:
            return (
                _token_cache["token"] is not None
                and time.time() < _token_cache["expires_at"]
            )

    def get_token(self) -> Optional[str]:
        with _lock:
            if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
                return _token_cache["token"]
        return None

    def set_manual_token(self, token: str):
        with _lock:
            _token_cache["token"] = token
            _token_cache["expires_at"] = time.time() + TOKEN_LIFETIME
        self._emit_status("manual_token_set")

    def _set_token(self, token: str):
        with _lock:
            _token_cache["token"] = token
            _token_cache["expires_at"] = time.time() + TOKEN_LIFETIME
        log.info("✅ Token (elevenlabs.io) len=%d, expires=%ds", len(token), TOKEN_LIFETIME)
        self._emit_status("token_ready")

    def _live_context(self):
        """Context dang dung: cua pool o che do tu dong, hoac context don."""
        if self._pool is not None:
            return self._pool.primary_context()
        return self._context

    async def _save_session(self):
        if not self._live_context():
            return
        try:
            state = await self._live_context().storage_state()
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
            log.info("[Session] Saved")
        except Exception as e:
            log.warning("[Session] Save error: %s", e)

    async def _update_cookies(self):
        ctx = self._live_context()
        if not ctx:
            return
        try:
            cookies = await ctx.cookies(
                ["https://elevenlabs.io", "https://api.elevenlabs.io"]
            )
            _update_cookie_cache(cookies)
            log.info("[Cookies] Cached %d cookies", len(cookies))
        except Exception as e:
            log.warning("[Cookies] Error: %s", e)

    async def _run_one_solve(self) -> tuple:
        """Auto-solve hCaptcha via direct API: checksiteconfig -> HSW -> getcaptcha.
        Chay fetch() tu ben trong browser (EL page, dung cookies + origin).
        Returns (token, reason): reason = 'ok'|'error'|'timeout'|'stopped'
        """
        self._emit_status('solving_captcha')
        log.info('[AutoSolve] Bat dau giai hCaptcha (direct API)...')

        if not self._page or self._page.is_closed():
            # Neu _page khong co, thu dung _cap_page
            if not self._cap_page or self._cap_page.is_closed():
                return None, 'no_page'
            target_page = self._cap_page
        else:
            target_page = self._page

        # Chay JS giai hCaptcha qua direct API trong browser context
        _HCAP_JS = r"""
(async function() {
async function hcapDirectSolve() {
    const SITEKEY = '8e58fe8c-1a48-4f94-88ae-8e90b586a192';
    const HOST = 'elevenlabs.io';
    const HDR = {
        'Accept': 'application/json',
        'User-Agent': navigator.userAgent
    };

    // Step 1: checksiteconfig
    let cfg;
    try {
        const cfgUrl = `https://api2.hcaptcha.com/checksiteconfig?v=1&host=${HOST}&sitekey=${SITEKEY}&sc=1&swa=1&spst=1`;
        const r = await fetch(cfgUrl, {headers: HDR});
        cfg = await r.json();
    } catch(e) {
        return {error: 'checksiteconfig_fetch: ' + String(e)};
    }

    if (!cfg || !cfg.c || !cfg.c.req) {
        return {error: 'checksiteconfig_bad: ' + JSON.stringify(cfg).slice(0, 200)};
    }

    const challenge_type = cfg.c.type;   // 'hsw' | 'hsl' | 'enterprise' etc
    const challenge_req = cfg.c.req;     // JWT for HSW PoW

    if (challenge_type !== 'hsw' && challenge_type !== 'hsl') {
        return {error: 'unsupported_challenge_type: ' + challenge_type};
    }

    // Step 2: Get HSW script version from config, load and run
    let n_token = '';
    try {
        // Try to use cached hsw if already in window
        if (typeof window.__hsw === 'function') {
            n_token = await window.__hsw(challenge_req);
        } else {
            // Load HSW script dynamically
            const hswResp = await fetch('https://newassets.hcaptcha.com/c/a/hsw.js', {headers: HDR});
            const hswCode = await hswResp.text();
            // Execute HSW function
            const hswFn = new Function('return ' + hswCode)();
            if (typeof hswFn === 'function') {
                n_token = await hswFn(challenge_req);
                window.__hsw = hswFn;  // cache it
            } else {
                // Some versions export differently
                window.__hsw_module = {};
                eval(hswCode);
                n_token = await (window.__hsw || window.hsw || window.__hsw_module.default)(challenge_req);
            }
        }
    } catch(e) {
        return {error: 'hsw_failed: ' + String(e)};
    }

    if (!n_token || typeof n_token !== 'string') {
        return {error: 'hsw_empty_token'};
    }

    // Step 3: getcaptcha to get the final P1_ token
    let token;
    try {
        const body = new URLSearchParams({
            v: '1',
            sitekey: SITEKEY,
            host: HOST,
            hl: 'vi',
            motionData: JSON.stringify({
                st: Date.now(), dct: Date.now(),
                mm: [], md: [], mu: [], v: 1
            }),
            n: n_token,
            c: JSON.stringify({type: challenge_type, req: challenge_req})
        });
        const capResp = await fetch(`https://hcaptcha.com/getcaptcha/${SITEKEY}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded', ...HDR},
            body: body.toString()
        });
        const capData = await capResp.json();
        if (capData && capData.pass === true && capData.key) {
            token = capData.key;
        } else if (capData && capData.key && capData.key.startsWith('P1_')) {
            token = capData.key;
        } else {
            return {error: 'getcaptcha_failed: ' + JSON.stringify(capData).slice(0, 200)};
        }
    } catch(e) {
        return {error: 'getcaptcha_fetch: ' + String(e)};
    }

    return {token: token};
}
return await hcapDirectSolve();
})()
"""

        for attempt in range(3):
            if not self._running:
                return None, 'stopped'
            try:
                log.info('[AutoSolve] Thu lan %d/3...', attempt + 1)
                result = await asyncio.wait_for(
                    target_page.evaluate(_HCAP_JS),
                    timeout=45.0
                )
                if not result:
                    log.warning('[AutoSolve] evaluate tra ve None')
                    await asyncio.sleep(3)
                    continue
                if result.get('token') and len(result['token']) > 20:
                    log.info('[AutoSolve] THANH CONG! Token %d ky tu', len(result['token']))
                    return result['token'], 'ok'
                err = result.get('error', 'unknown')
                log.warning('[AutoSolve] Lan %d loi: %s', attempt + 1, err[:120])
                # Phan loai loi
                if 'unsupported_challenge' in err:
                    return None, 'demoted'  # Enterprise challenge = bi demote
                if 'checksiteconfig_fetch' in err or 'getcaptcha_fetch' in err:
                    await asyncio.sleep(5)  # Network issue, retry
                    continue
                if 'hsw_failed' in err:
                    await asyncio.sleep(3)
                    continue
                return None, 'error'  # Loi khong retry duoc
            except asyncio.TimeoutError:
                log.warning('[AutoSolve] Lan %d timeout 45s', attempt + 1)
                await asyncio.sleep(2)
            except Exception as e:
                log.warning('[AutoSolve] Lan %d exception: %s', attempt + 1, str(e)[:100])
                await asyncio.sleep(3)

        log.warning('[AutoSolve] Tat ca 3 lan thu deu that bai')
        return None, 'timeout'

    async def _browser_tts_async(self, voice_id: str, payload: dict) -> dict:
        """Gửi TTS request TỪ BÊN TRONG browser — tự động có đúng cookies/origin."""
        if not self._page and not self._pool:
            return {"error": "no_page"}

        if not self._tts_lock:
            self._tts_lock = asyncio.Lock()

        async with self._tts_lock:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream/with-timestamps/anonymous"

            js = f"""
            (async () => {{
                try {{
                    const resp = await fetch({json.dumps(url)}, {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'audio/mpeg'
                        }},
                        body: JSON.stringify({json.dumps(payload)})
                    }});
                    const status = resp.status;
                    const text = await resp.text();
                    if (status !== 200) {{
                        return {{error: 'http_' + status, status: status, detail: text.substring(0, 300)}};
                    }}
                    return {{ok: true, body: text, status: status}};
                }} catch(e) {{
                    return {{error: 'fetch_error:' + String(e)}};
                }}
            }})()
            """
            try:
                if self._pool is not None:
                    # Che do pool: muon mot slot ranh, giu nguyen origin
                    # elevenlabs.io cua trang loader.
                    result = await self._pool.evaluate_on_slot(js)
                else:
                    result = await self._page.evaluate(js)
                return result or {"error": "null_result"}
            except Exception as e:
                return {"error": f"evaluate_error:{str(e)[:100]}"}

    def browser_synthesize(self, voice_id: str, payload: dict, timeout: float = 35.0) -> dict:
        """
        Gửi TTS request từ trong Camoufox browser (thread-safe).
        Trả về dict: {ok: True, body: "..."} hoặc {error: "..."}
        """
        if not self._running or not self._loop or (not self._page and not self._pool):
            return {"error": "browser_not_ready"}

        future = asyncio.run_coroutine_threadsafe(
            self._browser_tts_async(voice_id, payload),
            self._loop
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return {"error": "browser_tts_timeout"}
        except Exception as e:
            return {"error": f"browser_tts_error:{e}"}

    async def _solve_async(self):
        global _solver_instance
        try:
            from camoufox.async_api import AsyncNewBrowser
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("Camoufox not installed.")
            self._emit_status("camoufox_missing")
            return

        self._tts_lock = asyncio.Lock()
        self._emit_status("manual_starting" if self._manual_mode else "starting_browser")
        log.info("[Camoufox] Starting (headless=%s)...", self._headless)
        pw = None

        _repair_camoufox_addons()

        try:
            pw = await async_playwright().start()
            from .hsw_pool import build_launch_kwargs

            # Che do thu cong KHONG chan anh: nguoi dung phai nhin thay anh
            # cua visual challenge moi bam duoc.
            kwargs = build_launch_kwargs(
                headless=self._headless,
                proxy_url=self._proxy_url,
                block_images=False,
            )

            async with await _new_browser_with_fallback(pw, kwargs) as browser:
                self._browser = browser

                # Load saved session
                storage_state = None
                if os.path.exists(SESSION_FILE):
                    try:
                        with open(SESSION_FILE, "r", encoding="utf-8") as f:
                            storage_state = json.load(f)
                        log.info("[Session] Loaded from %s", SESSION_FILE)
                    except Exception as e:
                        log.warning("[Session] Load error: %s", e)

                ctx_opts = {"viewport": {"width": 1280, "height": 800}}
                if storage_state:
                    ctx_opts["storage_state"] = storage_state

                self._context = await browser.new_context(**ctx_opts)
                self._page = await self._context.new_page()
                _solver_instance = self

                # ── Interceptor: bắt hCaptcha token từ network ──
                # Khi EL page tự giải captcha (hoặc user giải), token xuất hiện trong:
                # 1. Response từ hcaptcha.com/checkcaptcha (generated_pass_UUID)
                # 2. POST body gửi đến EL TTS API (hcaptcha_token field)
                async def _on_response(resp):
                    try:
                        url = resp.url
                        if 'hcaptcha.com/checkcaptcha' in url or 'hcaptcha.com/getcaptcha' in url:
                            try:
                                data = await resp.json()
                                uuid = data.get('generated_pass_UUID') or data.get('key') or ''
                                if uuid and len(uuid) > 20 and uuid.startswith('P'):
                                    log.info('[Intercept] Got hCaptcha token from checkcaptcha! len=%d', len(uuid))
                                    self._set_token(uuid)
                                    self._emit_status('token_ready')
                            except Exception:
                                pass
                    except Exception:
                        pass

                async def _on_request(req):
                    try:
                        url = req.url
                        if 'elevenlabs.io' in url and 'anonymous' in url and req.method == 'POST':
                            try:
                                body = req.post_data
                                if body:
                                    import json as _json
                                    d = _json.loads(body)
                                    tok = d.get('hcaptcha_token', '')
                                    if tok and len(tok) > 20:
                                        log.info('[Intercept] Got hCaptcha token from TTS request! len=%d', len(tok))
                                        self._set_token(tok)
                                        self._emit_status('token_ready')
                            except Exception:
                                pass
                    except Exception:
                        pass

                self._page.on('response', _on_response)
                self._page.on('request', _on_request)
                log.info('[Intercept] Network interceptors registered')

                # Manual mode must remain visible and large enough for the
                # user to complete an interactive challenge.
                if not self._headless and not self._manual_mode:
                    try:
                        cdp = await self._context.new_cdp_session(self._page)
                        win = await cdp.send("Browser.getWindowForTarget")
                        wid = win.get("windowId", 0)
                        if wid:
                            await cdp.send("Browser.setWindowBounds", {
                                "windowId": wid,
                                "bounds": {"left": 10, "top": 10, "width": 460, "height": 360}
                            })
                    except Exception:
                        pass

                # Open EL page
                log.info("[Camoufox] Opening %s ...", EL_PAGE)
                self._emit_status("opening_manual_page" if self._manual_mode else "waiting_captcha")
                try:
                    await self._page.goto(EL_PAGE, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    log.warning("[Camoufox] goto warning: %s", str(e)[:80])

                # Doi EL page load xong + hCaptcha init (5s)
                await asyncio.sleep(5)
                await self._update_cookies()
                await self._save_session()
                last_save = time.time()

                # ── Main captcha loop v2 (GAP 1 + GAP 2) ──
                if self._manual_mode:
                    log.info("[Camoufox] Che do thu cong: Cua so da mo, dang cho nguoi dung tuong tac...")
                    self._emit_status("waiting_user")
                    start_manual_wait = time.time()
                    while self._running:
                        # 1. Kiem tra neu nguoi dung tu dong tab/trang
                        if not self._page or self._page.is_closed():
                            log.info("[Camoufox] Nguoi dung da dong cua so browser.")
                            self._emit_status("browser_closed")
                            break

                        # 2. Kiem tra xem interceptor da bat duoc token chua
                        if self.has_valid_token:
                            log.info("[Camoufox] Da bat duoc token hop le tu network!")
                            self._emit_status("token_ready")
                            await self._update_cookies()
                            await self._save_session()
                            await asyncio.sleep(2)
                            continue

                        # 3. Kiem tra token trong DOM h-captcha-response
                        try:
                            dom_token = await self._page.evaluate(
                                '() => { const el = document.querySelector(\'[name="h-captcha-response"]\'); return (el && el.value && el.value.length > 20) ? el.value : null; }'
                            )
                            if dom_token and len(dom_token) > 20:
                                log.info("[Camoufox] Bat duoc token tu DOM input! len=%d", len(dom_token))
                                self._set_token(dom_token)
                                self._emit_status("token_ready")
                                await self._update_cookies()
                                await self._save_session()
                                continue
                        except Exception:
                            pass

                        # 4. Timeout thu cong sau 15 phut (900 giay)
                        if time.time() - start_manual_wait > 900:
                            log.warning("[Camoufox] Het thoi gian cho thu cong (900s).")
                            self._emit_status("manual_timeout")
                            break

                        await asyncio.sleep(1)
                    return

                while self._running:
                    # GAP 2: Signal full restart từ report_401() → thoát loop
                    if self._need_full_restart:
                        log.warning("[Camoufox] Nhận tín hiệu full_restart (> 3 lần 401). Khởi động lại browser.")
                        self._need_full_restart = False
                        self._consecutive_401 = 0
                        break  # thoát → finally → _thread_target sẽ restart

                    log.info("[Camoufox] Solving captcha...")
                    token, reason = await self._run_one_solve()

                    if token and reason == 'ok':
                        self._consecutive_401 = 0  # reset khi có token mới
                        self._demote_count = 0
                        self._set_token(token)
                        await self._update_cookies()

                        if time.time() - last_save > 600:
                            await self._save_session()
                            last_save = time.time()

                        # Chờ token gần hết hạn (GAP 6: threshold 20s)
                        while self._running and self.has_valid_token:
                            if self._need_full_restart:
                                break
                            with _lock:
                                remaining = _token_cache["expires_at"] - time.time()
                            if remaining < TOKEN_REFRESH_THRESHOLD:
                                log.info("[Camoufox] Token còn %ds, refresh...", remaining)
                                self._emit_status("waiting_user" if self._manual_mode else "refreshing")
                                break
                            await asyncio.sleep(2)

                        if self._running and not self._need_full_restart:
                            try:
                                await self._page.reload(wait_until="domcontentloaded", timeout=20000)
                                await asyncio.sleep(3)
                            except Exception as e:
                                log.warning("[Camoufox] reload err: %s", str(e)[:60])

                    elif reason == 'demoted':
                        if self._manual_mode:
                            # Keep the challenge visible. Do not reload or
                            # rotate anything while the user is interacting.
                            self._emit_status("waiting_user")
                            await asyncio.sleep(2)
                            continue
                        # GAP 1: Bị demote — báo UI, reload trang
                        self._demote_count += 1
                        log.warning("[Camoufox] DEMOTED #%d - hCaptcha yêu cầu visual challenge", self._demote_count)
                        self._emit_status("demoted")

                        if self._demote_count >= 3:
                            # Demote nhiều lần → restart browser hoàn toàn
                            log.error("[Camoufox] Demote %d lần - restart browser", self._demote_count)
                            self._demote_count = 0
                            break

                        await asyncio.sleep(5)
                        try:
                            await self._page.goto(EL_PAGE, wait_until="domcontentloaded", timeout=30000)
                            await asyncio.sleep(4)
                        except Exception as e:
                            log.warning("[Camoufox] goto after demote err: %s", str(e)[:60])

                    else:
                        if self._manual_mode and reason in ('timeout', 'stopped'):
                            self._emit_status("manual_timeout" if reason == 'timeout' else "stopped")
                            break
                        log.warning("[Camoufox] Solve failed (reason=%s), retry 5s...", reason)
                        self._emit_status("waiting_user" if self._manual_mode else "refreshing")
                        await asyncio.sleep(5)

                        # inject_failed / page_error → page bị closed, cần goto lại
                        if reason in ('inject_failed', 'page_error', 'no_page'):
                            log.info("[Camoufox] Page closed, navigate lại EL_PAGE...")
                            try:
                                self._page = await self._context.new_page()
                                await self._page.goto(EL_PAGE, wait_until="domcontentloaded", timeout=30000)
                                await asyncio.sleep(4)
                            except Exception as e:
                                log.warning("[Camoufox] new_page/goto err: %s", str(e)[:80])
                        elif reason == 'not_loaded':
                            # hCaptcha script chưa load — goto lại để fresh load
                            log.info("[Camoufox] hCaptcha not_loaded - goto EL_PAGE fresh...")
                            try:
                                await self._page.goto(EL_PAGE, wait_until="domcontentloaded", timeout=30000)
                                await asyncio.sleep(5)  # chờ lâu hơn để script load
                            except Exception as e:
                                log.warning("[Camoufox] goto err: %s", str(e)[:60])
                        else:
                            try:
                                await self._page.reload(wait_until="domcontentloaded", timeout=20000)
                                await asyncio.sleep(3)
                            except Exception:
                                pass

        except Exception as e:
            err_str = str(e)
            log.error("[Camoufox] Fatal: %s", e)
            self._emit_status(f"error:{e}")
            # Đánh dấu fatal nếu là lỗi không thể tự recover
            self._mark_fatal_if_needed(err_str)
        finally:
            _solver_instance = None
            self._page = None
            self._cap_page = None
            self._context = None
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
            self._emit_status("browser_closed")
            self._running = False

    def _mark_fatal_if_needed(self, err_str: str) -> bool:
        """Danh dau loi khong tu phuc hoi duoc de UI hien dung nguyen nhan."""
        if any(k in err_str for k in _FATAL_KEYWORDS):
            self._fatal_error = err_str
            log.error("[Captcha] Fatal - browser dong: %s", err_str)
            return True
        return False

    async def _pool_loop(self):
        """Che do tu dong: HSW pool luon giu san mot token con han."""
        global _solver_instance
        try:
            from camoufox.async_api import AsyncNewBrowser  # noqa: F401
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("Camoufox not installed.")
            self._emit_status("camoufox_missing")
            return

        from .hsw_pool import HswPool

        _repair_camoufox_addons()
        self._tts_lock = asyncio.Lock()
        self._emit_status("starting_browser")
        pw = None
        try:
            pw = await async_playwright().start()
            self._pool = HswPool(
                headless=self._headless,
                proxy_url=self._proxy_url,
                on_status=self._emit_status,
            )
            await self._pool.start(pw)
            _solver_instance = self
            self._emit_status("pool_ready")
            await self._update_cookies()
            await self._save_session()
            last_save = time.time()

            while self._running:
                if self._need_full_restart:
                    self._need_full_restart = False
                    self._consecutive_401 = 0
                    await self._pool.restart_browser("report_401")

                if self.has_valid_token:
                    with _lock:
                        remaining = _token_cache["expires_at"] - time.time()
                    if remaining > TOKEN_REFRESH_THRESHOLD:
                        await asyncio.sleep(min(2.0, remaining - TOKEN_REFRESH_THRESHOLD))
                        continue
                    log.info("[HSW] Token con %ds - lam moi", int(remaining))
                    self._emit_status("refreshing")

                token, reason = await self._pool.solve()

                if token and reason == "ok":
                    self._consecutive_401 = 0
                    self._demote_count = 0
                    self._set_token(token)
                    await self._update_cookies()
                    if time.time() - last_save > 600:
                        await self._save_session()
                        last_save = time.time()
                elif reason == "demoted":
                    self._demote_count += 1
                    log.warning(
                        "[HSW] DEMOTED #%d - hCaptcha doi visual challenge",
                        self._demote_count,
                    )
                    self._emit_status("demoted")
                    if self._demote_count >= 3:
                        self._demote_count = 0
                        await self._pool.restart_browser("demote_streak")
                    else:
                        await asyncio.sleep(5)
                elif reason == "rate_limited":
                    log.warning("[HSW] Bi rate-limit - nghi 30s")
                    self._emit_status("rate_limited")
                    await asyncio.sleep(30)
                elif reason == "no_slot":
                    await asyncio.sleep(2)
                elif reason == "stopped":
                    break
                else:
                    self._emit_status("refreshing")
                    await asyncio.sleep(5)
        except Exception as e:
            log.error("[HSW] Fatal: %s", e)
            self._emit_status(f"error:{e}")
            self._mark_fatal_if_needed(str(e))
        finally:
            _solver_instance = None
            if self._pool is not None:
                try:
                    await self._pool.close()
                except Exception as e:
                    log.debug("[HSW] close loi: %s", str(e)[:60])
                self._pool = None
            self._page = None
            self._cap_page = None
            self._context = None
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
            self._emit_status("browser_closed")
            self._running = False

    def _thread_target(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            if self._manual_mode:
                self._loop.run_until_complete(self._solve_async())
            else:
                self._loop.run_until_complete(self._pool_loop())
        finally:
            self._loop.close()
            self._running = False

    def start(self, manual: bool = False):
        if self._running:
            log.info("Solver already running.")
            return
        self._manual_mode = bool(manual)
        if self._manual_mode:
            self._headless = False
            self._need_full_restart = False
            self._consecutive_401 = 0
            self._demote_count = 0
        self._fatal_error = None
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_target, daemon=True, name="captcha-solver"
        )
        self._thread.start()
        log.info("Captcha solver started (headless=%s)", self._headless)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._emit_status("stopped")

    def refresh(self):
        with _lock:
            _token_cache["token"] = None
            _token_cache["expires_at"] = 0
        if self._running:
            self._emit_status("waiting_user" if self._manual_mode else "refreshing")
            log.info("Token cleared; waiting for a new challenge completion...")
        else:
            self._emit_status("manual_required")
            log.info("Token cleared; manual CAPTCHA must be started by the user.")

    def ensure_running(self):
        """Đảm bảo auto-solver đang chạy headless.
        Tự động start nếu chưa chạy. Không start nếu có fatal error.
        Gọi hàm này trước mỗi TTS để luôn có solver sẵn sàng.
        """
        if self._fatal_error:
            log.warning("[AutoSolve] Khong start vi fatal error: %s", self._fatal_error[:80])
            return False
        if not self._running:
            log.info("[AutoSolve] Tu dong start headless solver...")
            self._headless = True
            self._manual_mode = False
            self.start()
            return True
        return self._running

    def clear_fatal_error(self):
        """Xóa fatal error flag — dùng sau khi user đã chạy camoufox fetch."""
        self._fatal_error = None
        log.info("[ManualCaptcha] Fatal error flag đã xóa")

    def wait_for_token(self, timeout: float = 120.0) -> bool:
        """Blocking wait cho đến khi có token hợp lệ (max timeout giây).
        Trả về True nếu có token, False nếu timeout."""
        import time as _time
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            if self.has_valid_token:
                return True
            _time.sleep(0.5)
        return False


    def get_status(self) -> dict:
        with _lock:
            has_token = _token_cache["token"] is not None
            expires = _token_cache["expires_at"]
        remaining = max(0, expires - time.time()) if has_token else 0
        return {
            "running": self._running,
            "has_token": has_token and remaining > 0,
            "expires_in": round(remaining),
            "has_proxy": bool(self._proxy_url),
            "fatal_error": self._fatal_error,
            "mode": "manual" if self._manual_mode else "legacy",
            "status": self._status,
            "consecutive_401": self._consecutive_401,
            "demote_count": self._demote_count,
            "requires_user_action": self._manual_mode and self._running and not (has_token and remaining > 0),
            "pool": self._pool.stats() if self._pool is not None else None,
        }

    # ── GAP 2: 401 tracking + full browser restart ──

    def report_401(self):
        """Gọi từ app.py mỗi khi TTS trả về 401.
        Khi đếm >= 3 lần liên tiếp → kích hoạt full restart browser.
        """
        if self._manual_mode:
            with _lock:
                _token_cache["token"] = None
                _token_cache["expires_at"] = 0.0
            self._emit_status("manual_required")
            log.warning("[ManualCaptcha] TTS returned 401; user verification is required again.")
            return

        self._consecutive_401 += 1
        log.warning("[GAP2] Báo cáo 401 lần #%d", self._consecutive_401)

        # Che do pool: recycle ngay slot vua dung; du chuoi thi restart browser.
        if self._pool is not None and self._loop is not None:
            pool = self._pool

            async def _handle():
                if pool.note_401():
                    await pool.restart_browser("401_streak")

            try:
                asyncio.run_coroutine_threadsafe(_handle(), self._loop)
            except Exception as e:
                log.warning("[HSW] note_401 khong gui duoc: %s", str(e)[:70])

        if self._consecutive_401 >= 3:
            log.error("[GAP2] 401 liên tiếp %d lần — kích hoạt full restart browser!", self._consecutive_401)
            self._emit_status("browser_restart_401")
            # Signal cho async loop thoát và restart
            self._need_full_restart = True
            # Xóa token cũ ngay
            with _lock:
                _token_cache["token"] = None
                _token_cache["expires_at"] = 0.0

    def reset_401_counter(self):
        """Reset counter khi TTS thành công."""
        if self._consecutive_401 > 0:
            log.info("[GAP2] Reset 401 counter (was %d)", self._consecutive_401)
        self._consecutive_401 = 0

    def full_restart(self):
        """Dừng browser hiện tại, xóa session, restart hoàn toàn.
        Dùng khi bị fingerprint-ban (401 liên tục không recover được).
        """
        if self._manual_mode:
            self.refresh()
            log.warning("[ManualCaptcha] Automatic full restart suppressed; user action required.")
            return

        log.warning("[GAP2] full_restart() được gọi — dừng browser + xóa session")
        self._need_full_restart = True
        # Xóa session cũ để browser mới không bị nhiễm
        if os.path.exists(SESSION_FILE):
            try:
                os.remove(SESSION_FILE)
                log.info("[GAP2] Đã xóa el_session.json cũ")
            except Exception as e:
                log.warning("[GAP2] Không xóa được session: %s", e)
        # Clear token
        with _lock:
            _token_cache["token"] = None
            _token_cache["expires_at"] = 0.0
        # Nếu browser đang chạy, signal sẽ được xử lý trong async loop
        # Nếu không chạy, restart ngay
        if not self._running and not self._fatal_error:
            self._headless = True
            self.start()


def browser_synthesize(voice_id: str, payload: dict, timeout: float = 35.0) -> dict:
    """
    Module-level helper: gửi TTS request từ Camoufox browser.
    Trả về {ok: True, body: "..."} hoặc {error: "..."}
    """
    global _solver_instance
    if _solver_instance is None or not _solver_instance._running:
        return {"error": "browser_not_running"}
    return _solver_instance.browser_synthesize(voice_id, payload, timeout)
