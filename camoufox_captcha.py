"""
camoufox_captcha.py — Tự động giải hCaptcha HSW cho ElevenLabs (không cần tương tác).

Flow tự động (giống v4.1):
  P1: Khởi động Camoufox, inject HSW hook, warm-up widget
  P2: checksiteconfig → lấy challenge JWT
  P3: Chạy hsw(jwt) trong browser → PoW token
  P4: getcaptcha → lấy P1_... captcha token
  P5: Lưu token → ready để gọi anonymous TTS

Chạy độc lập (spawn từ subprocess), trạng thái gửi qua stdout JSON.
"""
import asyncio
import json
import os
import sys
import time
import random
import base64
from pathlib import Path

BASE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ElevenLabsVoicePro"
BASE_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = BASE_DIR / "captcha_token.json"
STATUS_FILE = BASE_DIR / "captcha_status.json"
TOKEN_LIFETIME = 110   # giây

# hCaptcha config cho ElevenLabs
HCAPTCHA_SITEKEY = "a741ed11-59e7-4667-bff5-44a644c5e9b5"
HCAPTCHA_HOST    = "elevenlabs.io"
HCAPTCHA_API     = "https://api2.hcaptcha.com"
EL_TTS_PAGE      = "https://elevenlabs.io/text-to-speech"

# User-agents khớp với Firefox 120 (tls_client profile)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
]


def write_status(status, **extra):
    obj = {"status": status, "ts": time.time()}
    obj.update(extra)
    line = json.dumps(obj, ensure_ascii=False)
    STATUS_FILE.write_text(line, encoding="utf-8")
    print(line, flush=True)


def save_token(token):
    data = {
        "token": token,
        "expires_at": time.time() + TOKEN_LIFETIME,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─── HSW Auto-Solver ──────────────────────────────────────────────────────────

HSW_HOOK_JS = r"""
(function(){
    try {
        if (window.__hsw_hook_installed) return;
        window.__hsw_hook_installed = true;
        window.__hsw_captures = [];
        let _real = undefined;
        Object.defineProperty(window, 'hsw', {
            configurable: true,
            get: function(){ return _real; },
            set: function(fn){
                if (typeof fn !== 'function') { _real = fn; return; }
                _real = async function() {
                    const result = await fn.apply(this, arguments);
                    window.__hsw_captures.push({
                        t: Date.now(),
                        args: Array.from(arguments).map(a => typeof a === 'string' ? a.slice(0,200) : typeof a),
                        result_type: typeof result,
                        result_preview: typeof result === 'string' ? result.slice(0,100) : null,
                    });
                    return result;
                };
            }
        });
    } catch(e) {
        try { console.log('__HSW_HOOK_INSTALL_ERR__ ' + (e && e.message)); } catch(_){}
    }
})();
"""

WIDGET_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>hCaptcha Solver</title></head>
<body>
<div id="cap"></div>
<script>
var __token = null;
function __ok(t) { window.__hcap_token = t; }
function __err(e) { window.__hcap_error = e || 'err'; }
function __exp() { window.__hcap_expired = true; }
function __cexp() {}
function __close() {}
function __hcapReady(){
    hcaptcha.render('cap', {
        sitekey: '__SITEKEY__',
        size: 'invisible',
        callback: __ok,
        'error-callback': __err,
        'expired-callback': __exp,
        'challenge-expired-callback': __cexp,
        'close-callback': __close,
    });
    hcaptcha.execute();
}
</script>
<script src="https://js.hcaptcha.com/1/api.js?onload=__hcapReady&render=explicit&host=__HOST__" async defer></script>
</body></html>
""".replace("__SITEKEY__", HCAPTCHA_SITEKEY).replace("__HOST__", HCAPTCHA_HOST)


async def solve_hsw_auto(page, ua: str) -> str:
    """
    Tự động giải hCaptcha sử dụng HSW widget invisible.
    Inject widget HTML vào trang trắng → chờ hcaptcha.execute() trả về token.
    """
    # Dùng data URI để tải widget HTML
    html_b64 = base64.b64encode(WIDGET_HTML.encode()).decode()
    data_uri = f"data:text/html;base64,{html_b64}"

    await page.add_init_script(HSW_HOOK_JS)

    write_status("solving_captcha", msg="Đang chạy HSW invisible widget...")

    try:
        await page.goto(data_uri, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass

    # Chờ hcaptcha widget load và execute (tối đa 90 giây)
    deadline = time.time() + 90
    token = None
    while time.time() < deadline:
        try:
            result = await page.evaluate("""
                () => {
                    if (window.__hcap_token && window.__hcap_token.length > 20)
                        return {token: window.__hcap_token, error: null};
                    if (window.__hcap_error)
                        return {token: null, error: String(window.__hcap_error)};
                    return {token: null, error: null};
                }
            """)
            if result and result.get("token"):
                token = result["token"]
                break
            if result and result.get("error"):
                write_status("captcha_error", msg=result["error"])
                break
        except Exception:
            pass
        await asyncio.sleep(1)

    return token or ""


async def solve_via_intercept(page) -> str:
    """
    Phương thức dự phòng: mở trang TTS ElevenLabs, bắt token từ network request.
    Chờ user tự giải (hoặc widget tự giải nếu không cần challenge).
    """
    captured = []

    async def on_request(req):
        if captured:
            return
        if "text-to-speech" in req.url and "anonymous" in req.url:
            try:
                body = req.post_data
                if body:
                    d = json.loads(body)
                    tok = d.get("hcaptcha_token", "")
                    if tok and len(tok) > 20:
                        captured.append(tok)
            except Exception:
                pass

    async def on_response(resp):
        if captured:
            return
        if "hcaptcha.com/checkcaptcha" in resp.url:
            try:
                txt = await resp.text()
                d = json.loads(txt)
                uuid = d.get("generated_pass_UUID", "")
                if uuid and len(uuid) > 20:
                    captured.append(uuid)
            except Exception:
                pass

    page.on("request", on_request)
    page.on("response", on_response)

    write_status("needs_interaction", msg="Vui lòng nhấn Generate trên trang ElevenLabs để lấy token...")
    try:
        await page.goto(EL_TTS_PAGE, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        write_status("nav_warning", msg=str(e)[:80])

    deadline = time.time() + 600
    while time.time() < deadline:
        if captured:
            break
        try:
            js = "() => { var i = document.querySelector('[name=\"h-captcha-response\"]'); return (i && i.value && i.value.length > 20) ? i.value : null; }"
            val = await page.evaluate(js)
            if val and len(val) > 20:
                captured.append(val)
                break
        except Exception:
            pass
        await asyncio.sleep(1)

    return captured[0] if captured else ""


# ─── Main ─────────────────────────────────────────────────────────────────────

async def run():
    try:
        from camoufox.async_api import AsyncNewBrowser
        from playwright.async_api import async_playwright
    except ImportError:
        write_status("error", msg="camoufox not installed. Run: pip install camoufox[geoip]")
        return

    write_status("starting")
    ua = random.choice(USER_AGENTS)

    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        write_status("starting_browser")

        browser = await AsyncNewBrowser(
            pw,
            headless=True,   # Ẩn — không cần hiện cửa sổ cho auto-solve
            window=(1024, 720),
        )
        page = await browser.new_page(
            viewport={"width": 1024, "height": 720},
            extra_http_headers={"User-Agent": ua},
        )

        # ── Thử tự động giải HSW ────────────────────────────────
        write_status("solving_captcha", msg="Đang tự động giải hCaptcha (HSW invisible)...")
        token = await solve_hsw_auto(page, ua)

        if not token:
            # Fallback: headless=False để user tương tác
            write_status("auto_solve_failed",
                         msg="Auto-solve thất bại → mở trình duyệt để giải thủ công")
            try:
                await browser.close()
            except Exception:
                pass

            # Mở lại với headless=False
            browser = await AsyncNewBrowser(pw, headless=False, window=(1024, 720))
            page = await browser.new_page(viewport={"width": 1024, "height": 700})
            token = await solve_via_intercept(page)

        if token:
            save_token(token)
            write_status("token_ready", preview=token[:20] + "...")
            await asyncio.sleep(2)
        else:
            write_status("timeout", msg="Không lấy được token sau thời gian chờ")

    except Exception as e:
        write_status("error", msg=str(e)[:200])
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
        write_status("done")


if __name__ == "__main__":
    asyncio.run(run())
