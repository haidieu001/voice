"""
minimax_scraper.py — Minimax web token/cookie retrieval using Camoufox.

Approach:
1. Use Camoufox (stealth Firefox) to open https://www.minimax.io/audio.
2. User logs in and generates a test audio.
3. We intercept network requests to capture Authorization headers and Cookies.
4. Token is used for No-API TTS requests.
"""

import asyncio
import os
import re
from urllib.parse import urlparse
import time
import logging
import threading
import json
from typing import Optional, Callable

log = logging.getLogger(__name__)

# Token cache
# Web app cua MiniMax xac thuc bang header "token" lay tu localStorage,
# KHONG phai "Authorization: Bearer". Bat nham loai credential thi moi
# request toi /v1/api/... deu tra 401.
_WEB_TOKEN_KEYS = ("ACCESS_TOKEN", "mm_token", "_token", "access_token", "token")

_MM_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MM_SESSION_FILE = os.path.join(_MM_BASE, "minimax_session.json")
# Ghi lai body cua cac API giong noi khi nguoi dung thao tac tren web. Dung de
# hoc dung hinh dang payload cua clone_v2 thay vi doan tu bundle da minify.
MM_CAPTURE_FILE = os.path.join(_MM_BASE, "minimax_api_capture.json")

# Hook phai chay TRUOC moi script cua trang: web app mo WebSocket TTS ngay khi
# tai xong, cai hook sau do thi khong bat duoc socket da ton tai.
MM_WS_HOOK = """(function () {
  if (window.__mmHooked) return;
  window.__mmHooked = true;
  window.__mmA = {parts: [], done: false, err: null};
  window.__mmOverride = null;
  window.__mmWsCount = 0;

  var OrigSend = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    try {
      if (window.__mmOverride && typeof data === 'string'
          && data.indexOf('"payload"') !== -1) {
        var d = JSON.parse(data);
        if (d && d.payload) {
          var overrideObj = typeof window.__mmOverride === 'string' ? JSON.parse(window.__mmOverride) : window.__mmOverride;
          if (typeof d.payload === 'object') {
            d.payload = overrideObj;
          } else {
            d.payload = typeof window.__mmOverride === 'string' ? window.__mmOverride : JSON.stringify(window.__mmOverride);
          }
          window.__mmOverride = null;
          window.__mmA = {parts: [], finalAudio: null, done: false, err: null};
          data = JSON.stringify(d);
        }
      }
    } catch (e) {}
    return OrigSend.call(this, data);
  };

  var OrigWS = window.WebSocket;
  function Patched(url, protocols) {
    var s = protocols ? new OrigWS(url, protocols) : new OrigWS(url);
    if (String(url).indexOf('/v1/api/audio/ws') !== -1) {
      window.__mmWsCount++;
      s.addEventListener('message', function (ev) {
        try {
          var d = JSON.parse(ev.data);
          if (d.method === 'Heartbeat') return;
          var A = window.__mmA;
          if (!A) return;
          var si = d.statusInfo;
          if (si && si.code) { A.err = si.message || String(si.code); A.done = true; return; }
          var br = d.base_resp;
          if (br && br.status_code) { A.err = br.status_msg || String(br.status_code); A.done = true; return; }
          var hex = d.data && d.data.audio;
          var status = d.data && d.data.status;
          if (hex) {
            if (status === 2) {
              A.finalAudio = hex;
              A.done = true;
            } else {
              A.parts.push(hex);
            }
          }
          var ei = d.extra_info;
          if ((ei && (ei.audio_length || ei.audio_size)) || status === 2) A.done = true;
        } catch (e) {}
      });
    }
    return s;
  }
  Patched.prototype = OrigWS.prototype;
  Patched.CONNECTING = OrigWS.CONNECTING; Patched.OPEN = OrigWS.OPEN;
  Patched.CLOSING = OrigWS.CLOSING; Patched.CLOSED = OrigWS.CLOSED;
  window.WebSocket = Patched;
})();"""
# Ghi rong: chi bo qua nhung endpoint on ao khong lien quan. Lan truoc chi
# ghi /audio/voice/* nen bo sot han buoc luu /voice/resource/store.
_CAPTURE_SKIP = ("/config/", "/location/", "/user/device/", "/billing/",
                 "/history_list", "/voice/list", "/files/", "/charge/",
                 "/audio/model", "/audio/details")

_token_cache = {
    "op_ticket": None,
    "web_token": None,
    "auth_header": None,
    "cookie_string": None,
    "user_agent": None,
    "expires_at": 0.0,
}
_lock = threading.Lock()

TOKEN_LIFETIME = 24 * 3600  # Assume token valid for 24 hours

def load_op_ticket_from_session() -> str:
    """Doc op_ticket tu phien da luu, de khoi dong lai app khong phai dang nhap.

    op_ticket nam trong localStorage duoi khoa ``user_detail`` cua storage
    state ma Playwright luu.
    """
    if not os.path.exists(MM_SESSION_FILE):
        return ""
    try:
        with open(MM_SESSION_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        for origin in state.get("origins", []):
            for item in origin.get("localStorage", []):
                if item.get("name") != "user_detail":
                    continue
                ticket = json.loads(item.get("value") or "{}").get("op_ticket", "")
                if ticket and len(ticket) > 10:
                    return ticket
    except Exception as e:
        log.warning("[Minimax] Doc op_ticket tu phien loi: %s", str(e)[:90])
    return ""


class MinimaxScraper:
    """Minimax token scraper using Camoufox (anti-detect Firefox)."""

    def __init__(self, on_status: Optional[Callable] = None):
        self._browser = None
        self._page = None
        self._running = False
        self._on_status = on_status or (lambda s: None)
        self._loop = None
        self._thread = None
        self._proxy_url: Optional[str] = None
        self._dumped_keys = False
        self._dumped_auth = False
        self._announced_cookie = False
        # Giu cua so mo de tts_web_session co the gui WebSocket bat cu luc nao.
        self.keep_open = True
        # Trang mo dau; doi sang /audio/voices-cloning khi can thao tac clone.
        self.start_url = "https://www.minimax.io/audio"

        # Nap lai op_ticket tu phien da luu ngay khi khoi tao.
        if not _token_cache["op_ticket"]:
            saved = load_op_ticket_from_session()
            if saved:
                with _lock:
                    _token_cache["op_ticket"] = saved
                    _token_cache["expires_at"] = time.time() + TOKEN_LIFETIME
                log.info("[Minimax] Nap lai op_ticket tu phien da luu")
        self._headless = False  # Usually False so user can log in

    def set_proxy(self, proxy_url: str):
        self._proxy_url = proxy_url
        log.info("MinimaxScraper proxy set: %s", proxy_url)

    @property
    def has_valid_token(self) -> bool:
        with _lock:
            return (
                _token_cache["cookie_string"] is not None
                and time.time() < _token_cache["expires_at"]
            )

    def get_auth_data(self) -> dict:
        """Get current valid auth data."""
        with _lock:
            if time.time() < _token_cache["expires_at"]:
                return {
                    "op_ticket": _token_cache["op_ticket"],
                    "web_token": _token_cache["web_token"],
                    "auth_header": _token_cache["auth_header"],
                    "cookie_string": _token_cache["cookie_string"],
                    "user_agent": _token_cache["user_agent"],
                }
        return {}

    @property
    def op_ticket(self) -> Optional[str]:
        """Credential that de goi /v1/api/... cua MiniMax web."""
        with _lock:
            if time.time() < _token_cache["expires_at"]:
                return _token_cache["op_ticket"]
        return None

    @property
    def web_token(self) -> Optional[str]:
        """Token cua web app — thu dung de goi /v1/api/... truc tiep."""
        with _lock:
            if time.time() < _token_cache["expires_at"]:
                return _token_cache["web_token"]
        return None

    async def _read_op_ticket(self, page) -> str:
        """Doc ``localStorage["user_detail"].op_ticket`` cua trang da dang nhap.

        Day moi la credential that: thu nghiem cho thay chi rieng header
        ``op_ticket`` da du de goi /v1/api/..., con cookie thi khong.
        """
        try:
            res = await page.evaluate(
                """() => {
                    const out = {ticket: "", keys: []};
                    let ls = null;
                    try { ls = localStorage; } catch (e) { return out; }
                    for (let i = 0; i < ls.length; i++) out.keys.push(ls.key(i));
                    for (const k of ["user_detail", "userDetail", "user_info"]) {
                        let raw = "";
                        try { raw = ls.getItem(k) || ""; } catch (e) {}
                        if (!raw) continue;
                        try {
                            const o = JSON.parse(raw);
                            if (o && typeof o.op_ticket === "string" && o.op_ticket.length > 10) {
                                out.ticket = o.op_ticket;
                                return out;
                            }
                        } catch (e) {}
                    }
                    return out;
                }"""
            )
            if isinstance(res, dict):
                if res.get("keys") and not self._dumped_keys:
                    self._dumped_keys = True
                    log.info("[Minimax] Khoa localStorage: %s",
                             ", ".join(res["keys"][:30]) or "(rong)")
                return res.get("ticket") or ""
            return ""
        except Exception as e:
            log.warning("[Minimax] Khong doc duoc user_detail: %s", str(e)[:80])
            return ""

    async def _call_web_api_async(self, path: str, payload: dict, timeout: float):
        """Goi API cua MiniMax TU BEN TRONG trang dang mo.

        Vi sao khong goi thang tu Python: interceptor cua web tu gan chu ky
        ``yy`` vao moi request, va ``clone_v2`` bat buoc phai co (gui lai y
        nguyen body tu Python deu bi tu choi). Chay qua chinh axios instance
        cua trang thi chu ky duoc tinh ho.
        """
        if not self._page or self._page.is_closed():
            return {"error": "browser_chua_mo"}
        js = """
        async ([path, payload]) => {
            const grab = () => {
                const chunks = window.webpackChunk_N_E;
                if (!chunks || typeof chunks.push !== 'function') return null;
                let inst = null;
                try {
                    chunks.push([[Math.random()], {}, (req) => {
                        try {
                            const m = req(42101);
                            if (m && m.Z && m.Z.interceptors) { inst = m.Z; return; }
                        } catch (e) {}
                        const ids = Object.keys(req.m || {});
                        for (const id of ids) {
                            try {
                                const mod = req(id);
                                for (const k in mod) {
                                    const v = mod[k];
                                    if (v && v.interceptors && typeof v.post === 'function') {
                                        inst = v; return;
                                    }
                                }
                            } catch (e) {}
                        }
                    }]);
                } catch (e) {}
                return inst;
            };
            const ax = grab();
            if (!ax) return {error: 'khong_tim_thay_axios'};
            try {
                const r = await ax.post(path, payload);
                return {ok: true, data: r && r.data};
            } catch (e) {
                const resp = e && e.response;
                return {error: 'request_that_bai',
                        status: resp && resp.status,
                        body: resp && resp.data ? JSON.stringify(resp.data).slice(0, 600)
                                                : String(e).slice(0, 300)};
            }
        }
        """
        try:
            return await asyncio.wait_for(
                self._page.evaluate(js, [path, payload]), timeout=timeout
            )
        except asyncio.TimeoutError:
            return {"error": "het_thoi_gian"}
        except Exception as e:
            return {"error": "evaluate_loi: %s" % str(e)[:150]}

    async def _tts_ws_async(self, body: dict, timeout: float):
        """Chay TTS qua WebSocket TU BEN TRONG trang.

        Bat buoc phai o trong trang: handshake cua ``/v1/api/audio/ws`` kiem
        tra chu ky ``yy`` gan voi ``unix``, mo tu Python deu bi tu choi 400.
        Ta muon lai ham initWebSocket cua chinh web app de no tu ky.
        """
        if not self._page or self._page.is_closed():
            return {"error": "browser_chua_mo"}
        js = """
        async ([body, timeoutMs]) => {
          try {
            // Muon initWebSocket cua web app CHI de lay URL da ky (co yy khop
            // unix). Sau do tu mo socket rieng: vong doi socket cua helper do
            // no quan ly, gui frame qua no thi bi dong truoc khi co audio.
            const findHelper = () => {
                const chunks = window.webpackChunk_N_E;
                if (!chunks || typeof chunks.push !== 'function') return null;
                let helper = null;
                try {
                    chunks.push([[Math.random()], {}, (req) => {
                        for (const id of Object.keys(req.m || {})) {
                            let mod;
                            try { mod = req(id); } catch (e) { continue; }
                            for (const k in mod) {
                                let v;
                                try { v = mod[k]; } catch (e) { continue; }
                                if (v && typeof v.initWebSocket === 'function') {
                                    helper = v; return;
                                }
                            }
                        }
                    }]);
                } catch (e) {}
                return helper;
            };
            const helper = findHelper();
            if (!helper) return {error: 'khong_tim_thay_initWebSocket'};

            // Bat lay CHINH doi tuong socket ma helper tao (no da duoc ky
            // dung). Dong socket do roi mo lai bang URL cu thi server im lang,
            // nen ta giu no va tu dieu khien.
            const OrigWS = window.WebSocket;
            let grabbed = null;
            window.WebSocket = function (url, protocols) {
                const sock = protocols ? new OrigWS(url, protocols) : new OrigWS(url);
                if (!grabbed && String(url).indexOf('/v1/api/audio/ws') !== -1) {
                    grabbed = sock;
                }
                return sock;
            };
            window.WebSocket.prototype = OrigWS.prototype;
            // Dua LUON body that cho helper: no gui khung dau tien. Truoc day
            // ta dua body rong nen server bao loi roi dong ngay, khung cua ta
            // gui sau khong bao gio duoc tra loi.
            try {
                helper.initWebSocket({url: '/v1/api/audio/ws', body: body,
                    onOpen(){}, onMessage(){}, onError(){}, onClose(){}});
            } catch (e) {}
            for (let i = 0; i < 40 && !grabbed; i++) {
                await new Promise(r => setTimeout(r, 100));
            }
            window.WebSocket = OrigWS;
            if (!grabbed) return {error: 'khong_bat_duoc_socket'};

            return await new Promise((resolve) => {
                const parts = [];
                let done = false;
                const finish = (r) => {
                    if (done) return;
                    done = true;
                    try { ws.close(); } catch (e) {}
                    resolve(r);
                };
                const timer = setTimeout(
                    () => finish({error: 'het_thoi_gian', frames: parts.length}), timeoutMs);
                // Helper da gui khung roi; ta chi nghe tren chinh socket do.
                const ws = grabbed;
                ws.onerror = () => finish({error: 'ws_onerror'});
                ws.onclose = () => finish(parts.length
                    ? {ok: true, audio_hex: parts.join('')}
                    : {error: 'ws_dong_khi_chua_co_audio'});
                ws.onmessage = (ev) => {
                    let d;
                    try { d = JSON.parse(ev.data); } catch (e) { return; }
                    if (d.method === 'Heartbeat') {
                        try { ws.send(JSON.stringify({method: 'Heartbeat',
                              msg_id: d.msg_id, timestamp: Date.now()})); } catch (e) {}
                        return;
                    }
                    const si = d.statusInfo;
                    if (si && si.code) {
                        return finish({error: 'minimax_' + si.code,
                                       message: si.message || ''});
                    }
                    let finalAudioHex = null;
                    const hex = d.data && d.data.audio;
                    const status = d.data && d.data.status;
                    if (hex) {
                        if (status === 2) {
                            finalAudioHex = hex;
                        } else {
                            parts.push(hex);
                        }
                    }
                    const br = d.base_resp;
                    if (br && br.status_code) {
                        return finish({error: 'minimax_' + br.status_code,
                                       message: br.status_msg || ''});
                    }
                    const ei = d.extra_info;
                    if ((ei && (ei.audio_length || ei.audio_size)) || status === 2) {
                        clearTimeout(timer);
                        return finish({ok: true, audio_hex: finalAudioHex || parts.join(''), info: ei});
                    }
                };
            });
          } catch (e) {
            // Khong nuot loi: tra ve de Python bao duoc nguyen nhan that.
            return {error: 'js_loi: ' + String((e && e.stack) || e).slice(0, 400)};
          }
        }
        """
        try:
            res = await asyncio.wait_for(
                self._page.evaluate(js, [body, int(timeout * 1000)]),
                timeout=timeout + 15,
            )
            if not isinstance(res, dict):
                return {"error": "js_tra_ve_%s" % type(res).__name__}
            return res
        except asyncio.TimeoutError:
            return {"error": "het_thoi_gian"}
        except Exception as e:
            return {"error": "evaluate_loi: %s" % str(e)[:200]}

    async def _eval_async(self, js: str, arg, timeout: float):
        if not self._page or self._page.is_closed():
            return {"error": "browser_chua_mo"}
        try:
            return await asyncio.wait_for(self._page.evaluate(js, arg), timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": "het_thoi_gian"}
        except Exception as e:
            return {"error": "evaluate_loi: %s" % str(e)[:250]}

    def eval_in_page(self, js: str, arg=None, timeout: float = 60.0):
        """Chay JS trong trang dang mo. Dung cho dieu khien giao dien."""
        if not self._running or not self._loop:
            return {"error": "scraper_chua_chay"}
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._eval_async(js, arg, timeout), self._loop
            )
            return fut.result(timeout=timeout + 10)
        except Exception as e:
            return {"error": "goi_that_bai: %s" % str(e)[:150]}

    async def _goto_async(self, url: str, timeout: float):
        if not self._page or self._page.is_closed():
            return {"error": "browser_chua_mo"}
        try:
            await self._page.goto(url, wait_until="domcontentloaded",
                                  timeout=int(timeout * 1000))
            return {"ok": True, "url": self._page.url}
        except Exception as e:
            return {"error": "goto_loi: %s" % str(e)[:200]}

    def goto(self, url: str, timeout: float = 45.0):
        """Dua trang dang mo sang mot URL khac."""
        if not self._running or not self._loop:
            return {"error": "scraper_chua_chay"}
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._goto_async(url, timeout), self._loop)
            return fut.result(timeout=timeout + 10)
        except Exception as e:
            return {"error": "goi_that_bai: %s" % str(e)[:150]}

    async def _tts_ui_async(self, body: dict, timeout: float):
        """TTS bang cach de trang tu bam Generate, roi THAY RUOT khung gui di.

        Vi sao khong tu mo socket: handshake ``/v1/api/audio/ws`` kiem tra chu
        ky ``yy`` gan voi ``unix``; socket tu mo bi server im lang roi dong.

        Vi sao phai go bang ban phim that: o nhap la contenteditable do editor
        cua React quan ly. Dat text bang execCommand/textContent chi doi DOM,
        state cua editor van rong (bo dem hien 0/5000) nen Generate khong lam
        gi ca. Playwright go phim sinh su kien that nen editor cap nhat dung.
        """
        page = self._page
        if not page or page.is_closed():
            return {"error": "browser_chua_mo"}

        try:
            armed = await page.evaluate(
                """(p) => {
                    if (!window.__mmHooked) return false;
                    window.__mmOverride = p;
                    window.__mmA = {parts: [], done: false, err: null};
                    return true;
                }""",
                body.get("payload", ""),
            )
            if not armed:
                return {"error": "hook_chua_duoc_cai"}

            box = page.locator('[contenteditable="true"]').first
            await box.evaluate("el => el.focus()")
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await page.keyboard.type("a", delay=40)
            await page.wait_for_timeout(1000)

            btn = page.locator("button", has_text=re.compile(r"generate", re.I)).first
            try:
                await btn.click(timeout=8000)
            except Exception as b_err:
                log.info("[MinimaxWeb] Click button failed (%s), clicking via evaluate", b_err)
                await btn.evaluate("el => el.click()")

            log.info("[MinimaxWeb] Da click Generate, bat dau cho audio...")
            deadline = time.time() + timeout
            while time.time() < deadline:
                await asyncio.sleep(0.5)
                st = await page.evaluate(
                    """() => {
                        const A = window.__mmA || {parts: [], finalAudio: null, done: false, err: null};
                        return {n: A.parts.length, hasFinal: !!A.finalAudio, done: !!A.done, err: A.err || null,
                                pending: window.__mmOverride !== null};
                    }"""
                )
                if st.get("err"):
                    return {"error": "minimax: %s" % st["err"]}
                if st.get("done") or st.get("hasFinal"):
                    hex_audio = await page.evaluate(
                        "() => (window.__mmA.finalAudio || (window.__mmA.parts || []).join(''))")
                    return {"ok": True, "audio_hex": hex_audio}

            st = await page.evaluate(
                """() => ({n: (window.__mmA && window.__mmA.parts.length) || 0,
                           hasFinal: !!(window.__mmA && window.__mmA.finalAudio),
                           pending: window.__mmOverride !== null})""")
            if st.get("hasFinal") or st.get("n"):
                hex_audio = await page.evaluate(
                    "() => (window.__mmA.finalAudio || (window.__mmA.parts || []).join(''))")
                return {"ok": True, "audio_hex": hex_audio, "partial": True}
            if st.get("pending"):
                return {"error": "khung_T2a_khong_duoc_gui - trang chua bat dau tao"}
            return {"error": "het_thoi_gian_khong_co_audio"}
        except Exception as e:
            return {"error": "ui_loi: %s" % str(e)[:250]}

    def tts_via_ui(self, body: dict, timeout: float = 120.0) -> dict:
        """Ban dong bo cua ``_tts_ui_async``."""
        if not self._running or not self._loop:
            return {"error": "scraper_chua_chay"}
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._tts_ui_async(body, timeout), self._loop)
            return fut.result(timeout=timeout + 35)
        except Exception as e:
            return {"error": "goi_that_bai: %s" % str(e)[:150]}

    def tts_via_browser(self, body: dict, timeout: float = 120.0) -> dict:
        """Ban dong bo cua ``_tts_ws_async`` — goi duoc tu Flask."""
        if not self._running or not self._loop:
            return {"error": "scraper_chua_chay"}
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._tts_ws_async(body, timeout), self._loop
            )
            return fut.result(timeout=timeout + 25)
        except Exception as e:
            return {"error": "goi_that_bai: %s" % str(e)[:150]}

    def call_web_api(self, path: str, payload: dict, timeout: float = 90.0) -> dict:
        """Ban dong bo cua ``_call_web_api_async`` — goi duoc tu Flask."""
        if not self._running or not self._loop:
            return {"error": "scraper_chua_chay"}
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._call_web_api_async(path, payload, timeout), self._loop
            )
            return fut.result(timeout=timeout + 10)
        except Exception as e:
            return {"error": "goi_that_bai: %s" % str(e)[:150]}

    def _capture_ws(self, url: str, kind: str, payload) -> None:
        """Ghi khung WebSocket. Khung nhi phan chi ghi kich thuoc."""
        try:
            if isinstance(payload, (bytes, bytearray)):
                body = {"_binary_bytes": len(payload)}
            elif payload:
                try:
                    body = json.loads(payload)
                except Exception:
                    body = {"_raw": str(payload)[:1500]}
            else:
                body = {}
            # Heartbeat lap lien tuc lam tran file, che mat khung can xem.
            if isinstance(body, dict) and body.get("method") == "Heartbeat":
                return
            # Khung audio tra ve rat lon; chi giu phan mo ta.
            if isinstance(body, dict) and kind == "recv":
                for k in list(body.keys()):
                    v = body[k]
                    if isinstance(v, str) and len(v) > 300:
                        body[k] = "<%d ky tu>" % len(v)
            self._append_capture({"url": url, "method": "WS:" + kind, "body": body})
        except Exception as e:
            log.debug("[Minimax] Ghi WS loi: %s", str(e)[:80])

    def _append_capture(self, entry: dict) -> None:
        try:
            existing = []
            if os.path.exists(MM_CAPTURE_FILE):
                with open(MM_CAPTURE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
            existing.append(entry)
            with open(MM_CAPTURE_FILE, "w", encoding="utf-8") as f:
                json.dump(existing[-60:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("[Minimax] Ghi capture loi: %s", str(e)[:90])

    def _capture_payload(self, url: str, request) -> None:
        """Luu body cua request de biet chinh xac payload web app gui."""
        try:
            body = request.post_data
        except Exception:
            body = None
        if not body:
            return
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"_raw": str(body)[:2000]}
        entry = {"url": url, "method": request.method, "body": parsed}
        self._append_capture(entry)
        log.info("[Minimax] Da ghi payload %s",
                 urlparse(url).path.rsplit("/", 1)[-1])

    def _set_op_ticket(self, ticket: str):
        with _lock:
            _token_cache["op_ticket"] = ticket
            _token_cache["expires_at"] = time.time() + TOKEN_LIFETIME
        log.info("[Minimax] Lay duoc op_ticket (dai=%d) - day la credential that",
                 len(ticket))

    def _set_web_token(self, token: str):
        with _lock:
            _token_cache["web_token"] = token
            if not _token_cache["expires_at"]:
                _token_cache["expires_at"] = time.time() + TOKEN_LIFETIME
        log.info("[Minimax] Lay duoc web token (dai=%d)", len(token))

    def _set_token(self, auth_header: str, cookie_string: str, user_agent: str):
        """Internal: cache a new token."""
        with _lock:
            # Only update if we actually have a cookie
            if cookie_string:
                _token_cache["auth_header"] = auth_header
                _token_cache["cookie_string"] = cookie_string
                _token_cache["user_agent"] = user_agent
                _token_cache["expires_at"] = time.time() + TOKEN_LIFETIME

        # Ham nay chay tren MOI request /v1/api/ de cookie luon moi, nen chi
        # bao trang thai lan dau; khong thi UI bi doi bom token_ready.
        if cookie_string and not self._announced_cookie:
            self._announced_cookie = True
            log.info("[Minimax] Da bat duoc cookie phien (moi %ds)", TOKEN_LIFETIME)
            self._on_status("token_ready")

    async def _solve_async(self):
        try:
            from camoufox.async_api import AsyncNewBrowser
            from playwright.async_api import async_playwright, Request
        except ImportError:
            log.error("Camoufox not installed.")
            self._on_status("camoufox_missing")
            return

        self._on_status("starting_browser")
        log.info("Starting Camoufox browser for Minimax...")

        pw = None
        try:
            pw = await async_playwright().start()

            # Dung chung bo tham so voi captcha solver: co executable_path +
            # ff_version tro toi Camoufox trong runtime/. Khong the thi
            # Camoufox di tra ban cai trong cache va bao "not installed".
            from .hsw_pool import build_launch_kwargs

            kwargs = build_launch_kwargs(
                headless=self._headless,
                proxy_url=self._proxy_url,
                block_images=False,   # nguoi dung phai nhin thay trang de dang nhap
            )

            async with await AsyncNewBrowser(pw, **kwargs) as browser:
                self._browser = browser
                # Nap lai phien da luu de khong phai dang nhap lai moi lan.
                ctx_opts = {"viewport": {"width": 1024, "height": 768}}
                if os.path.exists(MM_SESSION_FILE):
                    try:
                        with open(MM_SESSION_FILE, "r", encoding="utf-8") as f:
                            ctx_opts["storage_state"] = json.load(f)
                        log.info("[Minimax] Nap phien tu %s", MM_SESSION_FILE)
                    except Exception as e:
                        log.warning("[Minimax] Doc phien loi: %s", str(e)[:80])

                context = await browser.new_context(**ctx_opts)
                # Cai truoc khi trang chay script nao.
                await context.add_init_script(MM_WS_HOOK)
                page = await context.new_page()
                self._page = page

                seen_api = {"n": 0}

                async def sniff_api_token(request: Request):
                    """Doc header 'token' tu chinh request cua web app."""
                    if _token_cache["web_token"]:
                        return
                    url = request.url
                    if "/v1/api/" not in url:
                        return
                    try:
                        hdrs = await request.all_headers()
                    except Exception:
                        hdrs = request.headers or {}
                    tok = hdrs.get("token") or ""
                    seen_api["n"] += 1
                    if seen_api["n"] <= 3:
                        # Chi in TEN header, khong in gia tri.
                        log.info("[Minimax] Request %s | header: %s",
                                 url.split("?")[0][-52:], ", ".join(sorted(hdrs.keys()))[:220])
                    if tok and len(tok) > 20:
                        self._set_web_token(tok)
                        self._on_status("token_ready")

                # Gan o cap context: dang nhap Google mo popup rieng, gan o
                # cap page se bo sot request phat ra tu do.
                context.on("request", sniff_api_token)

                # Endpoint chi tra 200 khi da dang nhap. Bat credential tu
                # chinh nhung request nay moi chac; ban cu chot vao request DAU
                # TIEN — von xay ra luc trang vua tai, truoc khi dang nhap — nen
                # luon luu nham cookie chua co phien.
                AUTH_PATHS = (
                    "/v1/api/audio/voice/equity",
                    "/v1/api/audio/billing/credit",
                    "/v1/api/files/request_policy",
                    "/v1/api/audio/history_list",
                    "/v1/api/user/info",
                )
                captured = False

                async def handle_request(request: Request):
                    nonlocal captured
                    url = request.url
                    if "/v1/api/" not in url:
                        return
                    try:
                        headers = await request.all_headers()
                    except Exception:
                        headers = request.headers or {}

                    cookie = headers.get("cookie", "")
                    auth = headers.get("authorization", "")
                    ua = headers.get("user-agent", "")

                    if (request.method == "POST"
                            and not any(k in url for k in _CAPTURE_SKIP)):
                        self._capture_payload(url, request)

                    is_auth_ep = any(p in url for p in AUTH_PATHS)
                    if is_auth_ep and not self._dumped_auth:
                        self._dumped_auth = True
                        names = [c.split("=", 1)[0].strip() for c in cookie.split(";") if c.strip()]
                        # Chi in TEN, khong in gia tri (deu la credential).
                        log.info("[Minimax] Request can dang nhap: %s", url.split("?")[0][-60:])
                        log.info("[Minimax]   header: %s", ", ".join(sorted(headers.keys()))[:260])
                        log.info("[Minimax]   cookie: %s", ", ".join(names)[:260])

                    # Luon cap nhat theo request moi nhat thay vi chot mot lan:
                    # cookie phien chi xuat hien sau khi dang nhap xong.
                    if cookie and (is_auth_ep or not captured):
                        self._set_token(auth, cookie, ua)
                        if is_auth_ep:
                            captured = True

                context.on("request", handle_request)

                # TTS cua web chay qua WebSocket nen khong hien ra duoi dang
                # POST. Ghi lai khung gui di de biet dung dinh dang.
                def _on_websocket(ws):
                    try:
                        url = ws.url
                    except Exception:
                        return
                    if "/v1/api/" not in url:
                        return
                    log.info("[Minimax] WebSocket mo: %s", url.split("?")[0][-60:])
                    self._capture_ws(url, "open", "")
                    ws.on("framesent",
                          lambda payload: self._capture_ws(url, "sent", payload))
                    ws.on("framereceived",
                          lambda payload: self._capture_ws(url, "recv", payload))

                page.on("websocket", _on_websocket)

                log.info("Loading Minimax page: %s", self.start_url)
                await page.goto(self.start_url, wait_until="domcontentloaded")

                self._on_status("waiting_login")
                log.info("🔍 Minimax loaded. Please log in (if needed) and generate an audio on the web page to capture the token.")

                # Cho toi khi bat duoc credential tu mot request CAN DANG NHAP.
                # Do la moc duy nhat chac chan la da dang nhap xong.
                while self._running:
                    if not _token_cache["op_ticket"]:
                        ticket = await self._read_op_ticket(page)
                        if ticket:
                            self._set_op_ticket(ticket)
                            self._on_status("token_ready")
                    if _token_cache["op_ticket"] and not self.keep_open:
                        log.info("[Minimax] Da bat duoc phien dang nhap, dong browser sau 3s...")
                        self._on_status("token_ready")
                        await self._save_session()
                        await asyncio.sleep(3)
                        break
                    await asyncio.sleep(2)

        except Exception as e:
            log.error("Minimax scraper error: %s", e)
            err = str(e)
            if any(k in err for k in ("is not installed", "camoufox fetch",
                                      "official/stable", "manifest.json", "Addon path")):
                log.error("[Minimax] Khong mo duoc Camoufox: %s", err[:160])
                self._on_status("camoufox_missing")
            else:
                self._on_status(f"error:{e}")
        finally:
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
            self._on_status("browser_closed")

    async def _save_session(self):
        try:
            if self._page and not self._page.is_closed():
                ctx = self._page.context
                state = await ctx.storage_state()
                with open(MM_SESSION_FILE, "w", encoding="utf-8") as f:
                    json.dump(state, f)
                log.info("[Minimax] Da luu phien dang nhap vao %s", MM_SESSION_FILE)
        except Exception as e:
            log.warning("[Minimax] Luu phien that bai: %s", e)

    def start(self, keep_open: bool = True):
        self.keep_open = keep_open
        if self._running:
            return
        self._running = True

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._solve_async())
            finally:
                self._loop.close()
                self._running = False

        self._thread = threading.Thread(target=_run, daemon=True, name="minimax-scraper")
        self._thread.start()
        log.info("Minimax scraper started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Minimax scraper stopped")
        
    def get_status(self) -> dict:
        """Get scraper status."""
        with _lock:
            has_token = _token_cache["op_ticket"] is not None
            expires = _token_cache["expires_at"]
        remaining = max(0, expires - time.time()) if has_token else 0
        return {
            "running": self._running,
            "has_token": has_token and remaining > 0,
            "expires_in": round(remaining),
            "has_proxy": bool(self._proxy_url),
            "has_op_ticket": bool(_token_cache["op_ticket"]),
            "has_web_token": bool(_token_cache["web_token"]),
        }
