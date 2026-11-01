"""
elevenlabs_login.py - Mo trang ElevenLabs trong trinh duyet mac dinh (Chrome/Edge).
Camoufox chay an hoan toan. User dang nhap Chrome/Edge, lay API key, dan vao trang local.
"""
import json
import time
import os
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ElevenLabsVoicePro"
BASE_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = BASE_DIR / "session.json"
STATUS_FILE = BASE_DIR / "login_status.json"
CALLBACK_PORT = 19876

_received_key = None
_server_done = threading.Event()


def write_status(status, **extra):
    obj = {"status": status, "ts": time.time()}
    obj.update(extra)
    STATUS_FILE.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _build_html():
    return """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>VoiceStudio - Luu API Key ElevenLabs</title>
<style>
* { box-sizing: border-box; }
body { background: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }
.card { background: #1e293b; border: 1px solid rgba(255,255,255,.12); border-radius: 16px;
  padding: 32px; max-width: 500px; width: 100%; box-shadow: 0 20px 50px rgba(0,0,0,.5); }
h2 { color: #38bdf8; margin: 0 0 12px; font-size: 20px; }
p { color: #94a3b8; font-size: 14px; margin: 0 0 14px; line-height: 1.7; }
ol { color: #94a3b8; font-size: 14px; padding-left: 20px; margin: 0 0 20px; line-height: 2.2; }
ol li a { color: #38bdf8; }
input { width: 100%; padding: 12px 16px; border-radius: 10px; border: 1px solid rgba(255,255,255,.2);
  background: #0f172a; color: #f8fafc; font-size: 14px; margin-bottom: 12px; }
input:focus { outline: none; border-color: #3b82f6; }
button { width: 100%; padding: 14px; border-radius: 10px; border: none;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6); color: white;
  font-size: 15px; font-weight: 700; cursor: pointer; letter-spacing: .3px; }
button:hover { filter: brightness(1.1); }
#msg { margin-top: 14px; padding: 12px; border-radius: 8px; font-size: 13px;
  display: none; text-align: center; font-weight: 600; }
.ok { background: rgba(74,222,128,.15); color: #4ade80; border: 1px solid rgba(74,222,128,.3); }
.err { background: rgba(248,113,113,.15); color: #f87171; border: 1px solid rgba(248,113,113,.3); }
.note { background: rgba(56,189,248,.1); color: #38bdf8; font-size: 12px;
  padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; border: 1px solid rgba(56,189,248,.2); }
</style>
</head>
<body>
<div class="card">
  <h2>&#128273; Kich hoat ElevenLabs</h2>
  <div class="note">&#9432; Camoufox dang chay an trong nen. Hay dung trinh duyet nay de dang nhap.</div>
  <p>Lam theo cac buoc:</p>
  <ol>
    <li>Dang nhap ElevenLabs trong tab vua mo</li>
    <li>Vao <strong>Settings &#8594; API Keys</strong> (hoac click link duoi)</li>
    <li>Tao hoac copy API key (bat dau bang <code>sk_</code>)</li>
    <li>Dan vao o ben duoi roi bam <strong>Luu</strong></li>
  </ol>
  <p><a href="https://elevenlabs.io/app/account/api-key" target="_blank" style="color:#38bdf8">
    &#8594; Mo trang API Keys ElevenLabs
  </a></p>
  <input id="k" type="text" placeholder="Dan API key vao day (sk_...)" autocomplete="off" />
  <button onclick="save()">&#128190; Luu va kich hoat VoiceStudio</button>
  <div id="msg"></div>
</div>
<script>
function save() {
  const k = document.getElementById('k').value.trim();
  if (!k || k.length < 10) { show('Vui long dan API key day du!', 'err'); return; }
  fetch('/save?key=' + encodeURIComponent(k)).then(r => r.json()).then(d => {
    if (d.ok) { show('Da luu! VoiceStudio da kich hoat ElevenLabs. Co the dong trang nay.', 'ok'); }
    else { show('Loi: ' + d.error, 'err'); }
  }).catch(() => show('Loi ket noi', 'err'));
}
function show(t, cls) {
  const el = document.getElementById('msg');
  el.textContent = t; el.className = cls; el.style.display = 'block';
}
document.getElementById('k').addEventListener('keydown', e => { if (e.key === 'Enter') save(); });
</script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        global _received_key
        parsed = urlparse(self.path)

        if parsed.path == "/":
            html = _build_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif parsed.path == "/save":
            qs = parse_qs(parsed.query)
            key = qs.get("key", [""])[0].strip()
            if key and len(key) > 10:
                _received_key = key
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                _server_done.set()
            else:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"API key qua ngan"}')

        else:
            self.send_response(404)
            self.end_headers()


def run():
    write_status("starting")

    # 1. Open ElevenLabs sign-in in default browser (Chrome/Edge - always visible)
    webbrowser.open("https://elevenlabs.io/app/sign-in")
    time.sleep(0.8)

    # 2. Open local input page in default browser
    webbrowser.open("http://localhost:" + str(CALLBACK_PORT) + "/")

    write_status("needs_login")

    # 3. Start local HTTP server to receive API key
    srv = HTTPServer(("", CALLBACK_PORT), _Handler)
    srv.timeout = 1
    deadline = time.time() + 600  # 10 minutes

    while time.time() < deadline:
        srv.handle_request()
        if _server_done.is_set():
            break

    srv.server_close()

    if _received_key:
        # Verify the API key is valid
        user_info = {}
        try:
            import httpx
            r = httpx.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": _received_key},
                verify=False,
                timeout=10,
            )
            if r.status_code == 200:
                d = r.json()
                user_info = {
                    "email": d.get("email", ""),
                    "tier": d.get("subscription", {}).get("tier", "free"),
                }
            elif r.status_code == 401:
                write_status("invalid_api_key", error="API key khong hop le, vui long kiem tra lai")
                write_status("done")
                return
        except Exception:
            pass

        session = {
            "api_key": _received_key,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        session.update(user_info)
        SESSION_FILE.write_text(
            json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        write_status("success", preview="***" + _received_key[-4:], **user_info)
    else:
        write_status("timeout")

    write_status("done")


if __name__ == "__main__":
    run()
