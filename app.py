"""
ElevenLabs Voice Pro - Flask Backend Server.

API routes for TTS, batch processing, voice search, profile management,
proxy configuration, and real-time SSE progress streaming.
"""

import os
import sys
import json
import time
import logging
import threading
import webbrowser
from pathlib import Path

from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from flask_cors import CORS

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("app")

# Flask app
BASE_DIR = Path(__file__).parent
app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
CORS(app)

@app.after_request
def add_no_cache(response):
    """Disable caching for HTML/JS/CSS during development."""
    if response.content_type and any(
        t in response.content_type
        for t in ['text/html', 'text/css', 'javascript']
    ):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# Core modules
from core.captcha_solver import CaptchaSolver
from core.proxy_rotator import ProxyRotator
from core.profile_manager import ProfileManager
from core.minimax_scraper import MinimaxScraper
from core.voice_search import VoiceSearcher
from core.tts_engine import TTSEngine
from core.tts_worker import TTSWorkerPool, TTSTask
from core.batch_processor import BatchProcessor
from core.text_utils import split_text, count_chars, clean_text
from core.audio_utils import find_ffmpeg
from core import engine_config as cfg

# Global instances
captcha_solver = CaptchaSolver()
minimax_scraper = MinimaxScraper()
proxy_rotator = ProxyRotator()
profile_manager = ProfileManager()
voice_searcher = VoiceSearcher()
worker_pool = TTSWorkerPool(
    captcha_solver=captcha_solver,
    proxy_rotator=proxy_rotator,
    minimax_scraper=minimax_scraper,
    worker_count=cfg.DEFAULT_WORKER_COUNT,
)
batch_processor = BatchProcessor(worker_pool=worker_pool)

# ─── Settings persistence ───────────────────────────────────────
import base64
_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "config", "settings.json")

def _load_settings() -> dict:
    """Load settings từ disk. Trả về dict rỗng nếu chưa tồn tại."""
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Decode obfuscated values
            result = {}
            for k, v in raw.items():
                try:
                    result[k] = base64.b64decode(v.encode()).decode() if v else ""
                except Exception:
                    result[k] = v  # fallback: dung plain
            return result
    except Exception as e:
        log.warning("[Settings] Load error: %s", e)
    return {}

def _save_settings(updates: dict):
    """Lưu settings vào disk (obfuscated base64)."""
    try:
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        # Merge voi file hien tai
        existing = {}
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        for k, v in updates.items():
            existing[k] = base64.b64encode(v.encode()).decode() if v else ""
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        log.info("[Settings] Saved: %s", list(updates.keys()))
    except Exception as e:
        log.warning("[Settings] Save error: %s", e)

# API key storage (alternative to captcha)
_saved = _load_settings()
_api_key = _saved.get("api_key", "")
_minimax_api_key = _saved.get("minimax_api_key", "")
if _api_key:
    log.info("[Settings] Restored API key from disk: %s...", _api_key[:8])
    worker_pool._api_key = _api_key  # Sync to worker pool ngay khi load
    voice_searcher.set_api_key(_api_key)  # Mo khoa Voice Library day du

# SSE event queue
_sse_events = []
_sse_lock = threading.Lock()


def push_event(event_type: str, data: dict):
    """Push an SSE event to all connected clients."""
    with _sse_lock:
        _sse_events.append({
            "type": event_type,
            "data": data,
            "time": time.time(),
        })
        # Keep only last 200 events
        if len(_sse_events) > 200:
            _sse_events[:] = _sse_events[-200:]


# Wire up callbacks
def _on_captcha_status(status):
    push_event("captcha", {"status": status})

def _on_file_progress(job):
    push_event("file_progress", {
        "file_index": job.file_index,
        "filename": job.filename,
        "done_chunks": job.done_chunks,
        "total_chunks": job.total_chunks,
        "failed_chunks": job.failed_chunks,
        "status": job.status,
        "progress": job.progress_pct(),
        "error": job.errors,
    })

def _on_batch_done():
    push_event("batch_done", batch_processor.get_progress())

captcha_solver._on_status = _on_captcha_status
batch_processor._on_file_progress = _on_file_progress
batch_processor._on_batch_done = _on_batch_done


# ===============================================================
# STATIC FILES
# ===============================================================

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/captcha-page")
def captcha_page():
    """Serve hCaptcha solver page for Camoufox."""
    from core.captcha_solver import CAPTCHA_HTML
    return CAPTCHA_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(app.static_folder, path)


# ===============================================================
# SSE - Server-Sent Events
# ===============================================================

@app.route("/api/events")
def sse_stream():
    """SSE endpoint for real-time progress updates."""
    def generate():
        last_idx = len(_sse_events)
        yield f"data: {json.dumps({'type': 'connected', 'data': {}})}\n\n"

        while True:
            time.sleep(0.5)
            with _sse_lock:
                if last_idx < len(_sse_events):
                    for evt in _sse_events[last_idx:]:
                        yield f"data: {json.dumps(evt)}\n\n"
                    last_idx = len(_sse_events)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ===============================================================
# VOICE SEARCH
# ===============================================================

@app.route("/api/voices/search")
def voices_search():
    """Search ElevenLabs voice library."""
    query = request.args.get("q", "")
    language = request.args.get("language", "")
    gender = request.args.get("gender", "")
    page = int(request.args.get("page", 0))
    result = voice_searcher.search(query, language, gender, page)
    return jsonify(result)

@app.route("/api/voices/popular")
def voices_popular():
    """Get popular voices."""
    lang = request.args.get("language", "vi")
    provider = request.args.get("provider", "elevenlabs")
    
    if provider == "minimax":
        voices = [v for v in cfg.MINIMAX_POPULAR_VOICES if v["language"] == lang or not lang]
        if not voices:
            voices = cfg.MINIMAX_POPULAR_VOICES
        return jsonify({"voices": voices})
        
    voices = voice_searcher.get_popular_voices(lang)
    return jsonify({"voices": voices})

@app.route("/api/voices/<voice_id>")
def voice_detail(voice_id):
    """Get voice details."""
    detail = voice_searcher.get_voice_detail(voice_id)
    if detail:
        return jsonify(detail)
    return jsonify({"error": "Voice not found"}), 404


# ===============================================================
# TTS - Single Text-to-Speech
# ===============================================================

@app.route("/api/tts", methods=["POST"])
def tts_single():
    """Convert text to speech."""
    global _api_key, _minimax_api_key
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    provider = data.get("provider", "elevenlabs")
    
    if provider == "minimax":
        voice_id = data.get("voice_id", cfg.MINIMAX_DEFAULT_VOICE)
    else:
        voice_id = data.get("voice_id", cfg.DEFAULT_VOICE_ID)

    if not text:
        return jsonify({"error": "Vui long nhap van ban"}), 400

    proxy_url = proxy_rotator.get_next() if proxy_rotator.enabled else None
    vs = data.get("voice_settings", {})
    audio_data = None
    error = None

    if provider == "minimax":
        api_key = _minimax_api_key or data.get("api_key", "")
        if not api_key:
            from core.minimax_engine import tts_web_session
            if not minimax_scraper._running or not minimax_scraper._page or minimax_scraper._page.is_closed():
                minimax_scraper.start(keep_open=True)
                for _ in range(25):
                    if minimax_scraper._page and not minimax_scraper._page.is_closed():
                        break
                    time.sleep(0.5)
                time.sleep(3)
            audio_data, error = tts_web_session(
                scraper=minimax_scraper,
                text=text,
                voice_id=voice_id,
                model=data.get("model_id", "speech-2.8-hd"),
                speed=float(vs.get("speed", 1.0) or 1.0),
                pitch=float(vs.get("pitch", 0) or 0),
                vol=float(vs.get("vol", 1.0) or 1.0),
            )
        else:
            from core.minimax_engine import MinimaxTTSEngine
            engine = MinimaxTTSEngine(proxy_url=proxy_url)
            audio_data, error = engine.synthesize(
                text=text,
                voice_id=voice_id,
                api_key=api_key,
                model_id=data.get("model_id", cfg.MINIMAX_DEFAULT_MODEL),
                language_code=data.get("language", cfg.DEFAULT_LANGUAGE),
                voice_settings=vs,
            )
    else:
        # ElevenLabs
        api_key = _api_key or data.get("api_key", "")
        captcha_token = ""
    
        if not api_key:
            # Auto-start solver headless nếu chưa chạy
            captcha_solver.ensure_running()
            captcha_token = captcha_solver.get_token() or ""
            if not captcha_token:
                # Chờ solver lấy token (tối đa 120s)
                log.info("[TTS] Chờ captcha solver lấy token (tối đa 120s)...")
                ok = captcha_solver.wait_for_token(timeout=120.0)
                captcha_token = captcha_solver.get_token() or ""
                if not captcha_token:
                    return jsonify({
                        "error": "⏳ Không lấy được captcha token sau 120s. Camoufox đang chạy, thử lại sau vài giây.",
                        "requires_user_action": False,
                    }), 503
    
        engine = TTSEngine(proxy_url=proxy_url)
        if not vs:
            vs = dict(cfg.VOICE_SETTINGS)
            
        audio_data, error = engine.synthesize(
            text=text,
            voice_id=voice_id,
            api_key=api_key if api_key else None,
            captcha_token=captcha_token,
            model_id=data.get("model_id", cfg.DEFAULT_MODEL_ID),
            language_code=data.get("language", cfg.DEFAULT_LANGUAGE),
            voice_settings=vs,
        )

    if audio_data:
        import tempfile
        os.makedirs(str(BASE_DIR / "output"), exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False, dir=str(BASE_DIR / "output")
        )
        tmp.write(audio_data)
        tmp.close()
        return send_file(tmp.name, mimetype="audio/mpeg", as_attachment=False)

    # Handle errors
    error_messages = {
        "invalid_api_key": "API key không hợp lệ. Kiểm tra lại.",
        "captcha_expired": "Captcha hết hạn, đang làm mới...",
        "rate_limited": "Bị giới hạn tốc độ. Thử lại sau.",
        "timeout": "Hết thời gian chờ. Thử lại.",
        "no_auth": "Cần API key hoặc captcha token.",
        "http_402": "❌ Tài khoản ElevenLabs hết ký tự hoặc chưa có subscription API. Nạp thêm credits tại elevenlabs.io hoặc dung captcha (xóa API key).",
        "payment_required": "❌ Tài khoản ElevenLabs hết credits. Vui lòng nạp thêm hoặc xóa API key để dùng captcha.",
        "ip_flagged": "IP đang bị ElevenLabs đánh dấu. Đổi proxy rồi thử lại.",
        "quota_exceeded": "IP này đã hết lượt miễn phí. Đổi proxy hoặc chờ.",
        "ip_pool_burned": "Đã thử hết proxy trong danh sách mà vẫn bị chặn.",
        "policy_blocked": "Nội dung bị chính sách của ElevenLabs từ chối.",
        "empty_audio_response": "Máy chủ trả về rỗng. Thử lại.",
    }

    if error == "captcha_expired":
        log.warning("Token hết hạn - yêu cầu người dùng xác minh lại")
        captcha_solver.refresh()

    msg = error_messages.get(error, error or "Loi khong xac dinh")
    # Catch 402 variants
    if error and ("402" in str(error) or "payment" in str(error).lower()):
        msg = error_messages["http_402"]
    return jsonify({"error": msg}), 500


@app.route("/api/tts/smart", methods=["POST"])
def tts_smart():
    """Smart TTS: auto-splits long text, processes chunks sequentially, merges audio."""
    global _api_key, _minimax_api_key
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    provider = data.get("provider", "elevenlabs")
    
    if provider == "minimax":
        voice_id = data.get("voice_id", cfg.MINIMAX_DEFAULT_VOICE)
    else:
        voice_id = data.get("voice_id", cfg.DEFAULT_VOICE_ID)
        
    split_mode = data.get("split_mode", cfg.DEFAULT_SPLIT_MODE)
    output_dir_req = data.get("output_dir", "")

    if not text:
        return jsonify({"error": "Vui long nhap van ban"}), 400

    # Determine auth
    api_key = _minimax_api_key if provider == "minimax" else _api_key
    if not api_key:
        api_key = data.get("api_key", "")

    # Clean text
    text = clean_text(text)

    # Split text into chunks
    max_chars = cfg.MAX_CHARS_PER_REQUEST
    chunks = split_text(text, split_mode, max_chars)

    if not chunks:
        return jsonify({"error": "Van ban rong sau khi xu ly"}), 400

    log.info("Smart TTS: %d chars → %d chunks (mode=%s)", len(text), len(chunks), split_mode)

    # Process each chunk
    audio_chunks = []
    vs = data.get("voice_settings", {})
    model_id = data.get("model_id", cfg.MINIMAX_DEFAULT_MODEL if provider == "minimax" else cfg.DEFAULT_MODEL_ID)
    language = data.get("language", cfg.DEFAULT_LANGUAGE)

    from core import tts_cache as _cache
    cache_params = {
        "provider": provider,
        "voice_id": voice_id,
        "model_id": model_id,
        "language_code": language,
        "speed": vs.get("speed"),
        "pitch": vs.get("pitch"),
        "vol": vs.get("vol"),
        "stability": vs.get("stability"),
        "similarity_boost": vs.get("similarity_boost"),
        "style": vs.get("style"),
        "use_speaker_boost": vs.get("use_speaker_boost"),
    }
    cache_hits = 0

    for i, chunk_text in enumerate(chunks):
        # Chunk nay da tao roi voi dung tham so nay -> dung lai, khong goi API.
        # Day la cho tiet kiem credit nhieu nhat: sua mot chu roi tao lai ca
        # bai thi cac chunk khong doi van phai tra tien lai.
        cached = _cache.get(chunk_text, cache_params)
        if cached:
            cache_hits += 1
            audio_chunks.append(cached)
            log.info("Chunk %d/%d lay tu cache (%d bytes)", i + 1, len(chunks), len(cached))
            continue

        # Get auth for this chunk
        captcha_token = ""
        auth_data = None
        
        if not api_key:
            if provider == "minimax":
                auth_data = minimax_scraper.get_auth_data()
                if not auth_data:
                    waited = 0
                    while not auth_data and waited < 120:
                        time.sleep(2)
                        waited += 2
                        auth_data = minimax_scraper.get_auth_data()
                    if not auth_data:
                        return jsonify({
                            "error": f"Het Minimax auth tai chunk {i+1}/{len(chunks)}.",
                            "partial_chunks": len(audio_chunks),
                        }), 503
            else:
                captcha_token = captcha_solver.get_token() or ""
                if not captcha_token:
                    # Auto-start solver nếu chưa chạy
                    captcha_solver.ensure_running()
                    # Chờ token
                    waited = 0
                    while not captcha_token and waited < 180:
                        time.sleep(2)
                        waited += 2
                        captcha_token = captcha_solver.get_token() or ""
                    if not captcha_token:
                        return jsonify({
                            "error": f"⏳ Captcha solver đang khởi động, chưa có token tại chunk {i+1}/{len(chunks)}. Thử lại sau ít giây.",
                            "requires_user_action": False,
                            "partial_chunks": len(audio_chunks),
                        }), 503

        proxy_url = proxy_rotator.get_next() if proxy_rotator.enabled else None
        
        if provider == "minimax":
            from core.minimax_engine import MinimaxTTSEngine
            engine = MinimaxTTSEngine(proxy_url=proxy_url)
        else:
            engine = TTSEngine(proxy_url=proxy_url)

        # Retry each chunk up to 4 times
        audio_data = None
        last_error = None
        for attempt in range(4):
            if provider == "minimax" and not api_key:
                # Khong co API key tra phi -> di qua phien dang nhap web.
                # api.minimax.chat luon tu choi credential cua phien web
                # ("login fail: Please carry the API secret key").
                from core.minimax_engine import tts_web_session
                if not minimax_scraper._running or not minimax_scraper._page or minimax_scraper._page.is_closed():
                    minimax_scraper.start(keep_open=True)
                    for _ in range(25):
                        if minimax_scraper._page and not minimax_scraper._page.is_closed():
                            break
                        time.sleep(0.5)
                    time.sleep(3)
                audio_data, error = tts_web_session(
                    scraper=minimax_scraper,
                    text=chunk_text,
                    voice_id=voice_id,
                    model=model_id or "speech-2.8-hd",
                    speed=float(vs.get("speed", 1.0) or 1.0),
                    pitch=float(vs.get("pitch", 0) or 0),
                    vol=float(vs.get("vol", 1.0) or 1.0),
                )
            elif provider == "minimax":
                audio_data, error = engine.synthesize(
                    text=chunk_text,
                    voice_id=voice_id,
                    api_key=api_key if api_key else None,
                    auth_data=auth_data,
                    model_id=model_id,
                    language_code=language,
                    voice_settings=vs,
                )
            else:
                # === EL anonymous: thử browser_synthesize trước ===
                audio_data = None
                error = None
                import json as _json, base64 as _b64
                from core import captcha_solver as _cs_mod

                # --- Đường 1: browser_synthesize (Camoufox có cookies + fingerprint) ---
                if not api_key and _cs_mod._solver_instance and _cs_mod._solver_instance._running:
                    br_payload = {
                        "text": chunk_text,
                        "model_id": model_id,
                        "language_code": language,
                        "voice_settings": {
                            "stability":        max(0.0, min(1.0, (vs or {}).get("stability", 0.75))),
                            "similarity_boost": max(0.0, min(1.0, (vs or {}).get("similarity_boost", 0.80))),
                            "style":            max(0.0, min(1.0, (vs or {}).get("style", 0.30))),
                            "use_speaker_boost": (vs or {}).get("use_speaker_boost", True),
                            "speed":            (vs or {}).get("speed", 1.0),
                        },
                        "hcaptcha_token": captcha_token,
                    }
                    try:
                        br = _cs_mod.browser_synthesize(voice_id, br_payload, timeout=40.0)
                        log.info("browser_synthesize result: %s",
                                 {k: (v[:80] if isinstance(v, str) else v) for k, v in br.items()})
                        if br.get("ok") and br.get("body"):
                            chunks_b = []
                            for line in br["body"].split("\n"):
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    obj = _json.loads(line)
                                    b64 = obj.get("audio_base64", "")
                                    if b64:
                                        chunks_b.append(_b64.b64decode(b64))
                                except Exception:
                                    pass
                            if chunks_b:
                                audio_data = b"".join(chunks_b)
                                log.info("✅ browser_synthesize OK: %d bytes", len(audio_data))
                            else:
                                error = "empty_audio_response"
                                log.warning("browser_synthesize: 200 but no audio_base64 chunks")
                        elif "error" in br:
                            err_val = str(br["error"])
                            log.warning("browser_synthesize error: %s", err_val)
                            if "401" in err_val or "http_401" in err_val:
                                error = "captcha_expired"
                            else:
                                error = f"browser_error:{err_val}"
                    except Exception as be:
                        log.warning("browser_synthesize exception: %s", be)

                # --- Đường 2: fallback tls_client / httpx ---
                if not audio_data and error != "captcha_expired":
                    audio_data, error = engine.synthesize(
                        text=chunk_text,
                        voice_id=voice_id,
                        api_key=api_key if api_key else None,
                        captcha_token=captcha_token,
                        model_id=model_id,
                        language_code=language,
                        voice_settings=vs or dict(cfg.VOICE_SETTINGS),
                    )

            if audio_data:
                # GAP 2: Reset 401 counter khi chunk thanh cong
                captcha_solver.reset_401_counter()
                break
            last_error = error
            err_str_retry = str(error or "")
            if error == "captcha_expired" or error == "auth_expired" or ("401" in err_str_retry and not api_key):
                # GAP 2: Bao 401 len solver -> co the trigger full restart browser
                if not api_key:
                    captcha_solver.report_401()
                if provider != "minimax":
                    captcha_solver.refresh()
                    time.sleep(5)
                    captcha_token = captcha_solver.get_token() or ""
                    if not captcha_token:
                        waited = 0
                        while not captcha_token and waited < 30:
                            time.sleep(2)
                            waited += 2
                            captcha_token = captcha_solver.get_token() or ""
                else:
                    time.sleep(5)
                    auth_data = minimax_scraper.get_auth_data()
                continue
            elif error in ("ip_flagged", "quota_exceeded"):
                # Van de o IP chu khong phai token — doi proxy, dung giai lai captcha.
                wait = 10 if error == "ip_flagged" else 20
                log.warning("[IP] %s — doi proxy va cho %ds", error, wait)
                if proxy_rotator.enabled:
                    proxy_rotator.get_next()
                time.sleep(wait)
                continue
            elif error == "policy_blocked":
                log.error("[Policy] Noi dung bi tu choi — dung thu lai")
                break
            elif error and "rate_limited" in error:
                # GAP 4: Doc retry_after tu error string thay vi sleep 15s co dinh
                import re as _re_rl
                retry_after = 15
                try:
                    m = _re_rl.search(r"retry[_\-]after[=:\s]*(\d+)", err_str_retry, _re_rl.IGNORECASE)
                    if not m:
                        m = _re_rl.search(r"rate_limited[:\s]+(\d+)", err_str_retry, _re_rl.IGNORECASE)
                    if m:
                        retry_after = min(int(m.group(1)) + 2, 60)
                except Exception:
                    pass
                log.info("[GAP4] Rate limited - doi %ds...", retry_after)
                time.sleep(retry_after)
                continue
            else:
                break

        if not audio_data:
            err_str = str(last_error or "")
            # --- 402 Payment Required: fallback sang captcha mode ---
            if "402" in err_str or "payment" in err_str.lower():
                log.warning("[SmartTTS] 402 từ API key — tự động chuyển sang captcha mode")
                push_event("tts_warn", {"message": "⚠️ API key hết credits (402) — đang chuyển sang captcha..."})
                # Clear API key tạm thời cho chunk này, thử lại với captcha
                captcha_token = captcha_solver.get_token() or ""
                if captcha_token:
                    audio_data, retry_err = engine.synthesize(
                        text=chunk_text,
                        voice_id=voice_id,
                        api_key=None,  # bỏ qua API key
                        captcha_token=captcha_token,
                        model_id=model_id,
                        language_code=language,
                        voice_settings=vs or dict(cfg.VOICE_SETTINGS),
                    )
                    if audio_data:
                        log.info("[SmartTTS] Captcha fallback thành công cho chunk %d", i+1)
                    else:
                        last_error = retry_err or last_error

            if not audio_data:
                err_user = last_error or "unknown"
                if "402" in str(err_user) or "payment" in str(err_user).lower():
                    err_user = "❌ Tài khoản ElevenLabs hết credits (402 Payment Required). Nạp thêm hoặc xóa API key để dùng captcha miễn phí."
                elif "401" in str(err_user):
                    err_user = "❌ API key không hợp lệ (401). Kiểm tra lại hoặc xóa key trong Settings."
                return jsonify({
                    "error": f"Chunk {i+1}/{len(chunks)} that bai: {err_user}",
                    "partial_chunks": len(audio_chunks),
                }), 500

        _cache.put(chunk_text, cache_params, audio_data)
        audio_chunks.append(audio_data)
        log.info("Chunk %d/%d done (%d bytes)", i + 1, len(chunks), len(audio_data))

        # Push progress event
        push_event("tts_chunk", {
            "done": i + 1,
            "total": len(chunks),
            "progress": round((i + 1) / len(chunks) * 100),
        })

        # Rate limit delay between chunks (captcha mode)
        if not api_key and i < len(chunks) - 1:
            time.sleep(2)

    # Merge chunks
    if len(audio_chunks) == 1:
        # Single chunk, return directly
        final_audio = audio_chunks[0]
    else:
        # Use merge utility
        from core.audio_utils import merge_audio_chunks
        import tempfile
        out_dir = output_dir_req if output_dir_req else str(BASE_DIR / "output")
        os.makedirs(out_dir, exist_ok=True)
        tmp_path = os.path.join(out_dir, f"merged_{int(time.time())}.mp3")
        success = merge_audio_chunks(audio_chunks, tmp_path)
        if success and os.path.isfile(tmp_path):
            with open(tmp_path, 'rb') as f:
                final_audio = f.read()
            # Keep the file for user
        else:
            # Fallback: raw concatenation
            final_audio = b''.join(audio_chunks)

    # Save to output directory
    out_dir = output_dir_req if output_dir_req else str(BASE_DIR / "output")
    os.makedirs(out_dir, exist_ok=True)
    voice_name = voice_id[:10]
    out_filename = f"tts_{voice_name}_{int(time.time())}.mp3"
    out_path = os.path.join(out_dir, out_filename)
    with open(out_path, 'wb') as f:
        f.write(final_audio)
    log.info("Smart TTS complete: %d chunks → %s (%d bytes)", len(chunks), out_path, len(final_audio))

    push_event("tts_done", {
        "chunks": len(chunks),
        "file": out_filename,
        "size": len(final_audio),
    })

    return send_file(
        out_path, mimetype="audio/mpeg", as_attachment=False,
        download_name=out_filename,
    )


@app.route("/api/tts/upload", methods=["POST"])
def tts_upload_files():
    """Upload text files for TTS tab processing."""
    if "files" not in request.files:
        return jsonify({"error": "No files"}), 400

    results = []
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        # Read content
        raw = f.read()
        # Try UTF-8 first, then fallback
        text = ""
        for enc in ['utf-8', 'utf-8-sig', 'utf-16', 'cp1252', 'latin-1']:
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if not text:
            text = raw.decode('utf-8', errors='replace')

        results.append({
            "name": f.filename,
            "text": text,
            "chars": len(text),
        })

    return jsonify({"files": results})


# ===============================================================
# API KEY
# ===============================================================

@app.route("/api/apikey/set", methods=["POST"])
def apikey_set():
    """Set ElevenLabs API key."""
    global _api_key
    data = request.get_json(force=True)
    key = data.get("key", "").strip()
    _api_key = key
    worker_pool._api_key = key  # Sync to worker pool for batch mode
    voice_searcher.set_api_key(key)  # Nap lai danh sach giong voi quyen day du
    if key:
        # Quick validation + fetch user voices
        engine = TTSEngine(api_key=key)
        result = engine.test_connection()
        # Count cloned voices
        cloned_count = 0
        try:
            import httpx as _httpx
            with _httpx.Client(timeout=10, verify=False) as client:
                vr = client.get(
                    f"{__import__('core.engine_config', fromlist=['ELEVENLABS_API_BASE']).ELEVENLABS_API_BASE}/v1/voices",
                    headers={"xi-api-key": key, "Accept": "application/json"}
                )
                if vr.status_code == 200:
                    vdata = vr.json()
                    cloned_count = sum(
                        1 for v in vdata.get("voices", [])
                        if v.get("category", "") in ("cloned", "professional", "generated")
                    )
        except Exception:
            pass
        msg = "API key hop le!"
        if cloned_count:
            msg += f" Tim thay {cloned_count} voice clone cua ban."
        log.info("API key set OK, cloned voices: %d", cloned_count)
        # Lưu vào disk
        _save_settings({"api_key": key})
        return jsonify({"status": "ok", "message": msg, "cloned_count": cloned_count})
    log.info("API key da duoc xoa -> chuyen sang captcha")
    # Xóa khỏi disk
    _save_settings({"api_key": ""})
    return jsonify({"status": "cleared", "message": "Da xoa API key. Dung captcha de tao giong noi."})


@app.route("/api/voices/my")
def voices_my():
    """Fetch user's own voices (including clones) using stored API key."""
    global _api_key
    api_key = request.args.get("api_key", "").strip() or _api_key
    if not api_key:
        return jsonify({"error": "Chua co API key. Vao Settings de nhap key.", "voices": []}), 401

    import httpx as _httpx
    from core import engine_config as _cfg
    try:
        with _httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(
                f"{_cfg.ELEVENLABS_API_BASE}/v1/voices",
                headers={
                    "xi-api-key": api_key,
                    "Accept": "application/json",
                }
            )
        if resp.status_code == 401:
            return jsonify({"error": "API key khong hop le.", "voices": []}), 401
        if resp.status_code != 200:
            return jsonify({"error": f"ElevenLabs tra ve loi {resp.status_code}", "voices": []}), 502

        data = resp.json()
        all_voices = data.get("voices", [])

        # Separate cloned / generated / professional
        cloned = []
        premade = []
        for v in all_voices:
            cat = v.get("category", "")
            voice_obj = {
                "voice_id": v.get("voice_id", ""),
                "name": v.get("name", "Unknown"),
                "category": cat,
                "description": v.get("description", ""),
                "preview_url": v.get("preview_url", ""),
                "gender": (v.get("labels") or {}).get("gender", ""),
                "age": (v.get("labels") or {}).get("age", ""),
                "language": (v.get("labels") or {}).get("language", ""),
                "use_case": (v.get("labels") or {}).get("use_case", ""),
                "cloned": cat in ("cloned", "professional", "generated"),
                "sharing": v.get("sharing") or {},
            }
            if voice_obj["cloned"]:
                cloned.append(voice_obj)
            else:
                premade.append(voice_obj)

        log.info("[voices/my] %d cloned + %d premade", len(cloned), len(premade))
        return jsonify({
            "cloned": cloned,
            "premade": premade,
            "total": len(all_voices),
            "cloned_count": len(cloned),
        })

    except Exception as e:
        log.error("voices/my error: %s", e)
        return jsonify({"error": str(e)[:200], "voices": []}), 500


@app.route("/api/voices/clone", methods=["POST"])
def voices_clone():
    """Tạo Instant Voice Clone từ file audio mẫu.
    Nhận multipart/form-data:
      - name: tên voice (required)
      - description: mô tả (optional)
      - files: 1+ file audio (WAV/MP3/M4A, nên 30s-5min, giọng rõ)
    Trả về: {voice_id, name, message}
    """
    global _api_key
    api_key = request.form.get("api_key", "").strip() or _api_key
    if not api_key:
        return jsonify({"error": "Cần API key để tạo voice clone. Vào Settings để nhập key."}), 401

    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Vui lòng đặt tên cho voice clone."}), 400

    description = request.form.get("description", "").strip()
    files = request.files.getlist("files")
    if not files or not any(f.filename for f in files):
        return jsonify({"error": "Vui lòng tải lên ít nhất 1 file audio mẫu."}), 400

    import httpx as _httpx
    from core import engine_config as _cfg
    try:
        # Build multipart payload
        multipart_files = []
        for f in files:
            if f and f.filename:
                data = f.read()
                ct = f.content_type or "audio/mpeg"
                multipart_files.append(("files", (f.filename, data, ct)))
                log.info("[Clone] File: %s (%d bytes)", f.filename, len(data))

        form_data = {"name": name}
        if description:
            form_data["description"] = description

        with _httpx.Client(timeout=60, verify=False) as client:
            resp = client.post(
                f"{_cfg.ELEVENLABS_API_BASE}/v1/voices/add",
                headers={"xi-api-key": api_key},
                data=form_data,
                files=multipart_files,
            )

        if resp.status_code == 401:
            return jsonify({"error": "API key không hợp lệ."}), 401
        # ElevenLabs tra 400/402 kem code paid_plan_required khi goi tai khoan
        # khong co quyen clone. Bao thang thay vi do nguyen JSON ra man hinh.
        if resp.status_code in (400, 402, 403):
            try:
                detail = resp.json().get("detail", {})
                code = (detail.get("code") or "") if isinstance(detail, dict) else ""
                status = (detail.get("status") or "") if isinstance(detail, dict) else ""
            except Exception:
                code = status = ""
            if "paid_plan" in code or "can_not_use_instant_voice_cloning" in status:
                return jsonify({
                    "error": "Gói ElevenLabs hiện tại không có quyền tạo voice clone. "
                             "Voice cloning cần gói trả phí (Starter trở lên) — "
                             "không thể thay bằng CAPTCHA.",
                    "code": "paid_plan_required",
                }), 402
        if resp.status_code == 422:
            detail = resp.json().get("detail", {})
            return jsonify({"error": f"File audio không hợp lệ: {detail}"}), 422
        if resp.status_code not in (200, 201):
            return jsonify({"error": f"ElevenLabs lỗi {resp.status_code}: {resp.text[:300]}"}), 502

        result = resp.json()
        voice_id = result.get("voice_id", "")
        log.info("[Clone] Tạo voice clone thành công: %s (%s)", name, voice_id)
        return jsonify({
            "status": "ok",
            "voice_id": voice_id,
            "name": name,
            "message": f"✅ Đã tạo voice clone '{name}' thành công!",
        })

    except Exception as e:
        log.error("[Clone] Error: %s", e)
        return jsonify({"error": str(e)[:300]}), 500


@app.route("/api/voices/<voice_id>", methods=["DELETE"])
def voices_delete(voice_id):
    """Xóa voice clone theo voice_id."""
    global _api_key
    api_key = request.args.get("api_key", "").strip() or _api_key
    if not api_key:
        return jsonify({"error": "Cần API key."}), 401

    import httpx as _httpx
    from core import engine_config as _cfg
    try:
        with _httpx.Client(timeout=15, verify=False) as client:
            resp = client.delete(
                f"{_cfg.ELEVENLABS_API_BASE}/v1/voices/{voice_id}",
                headers={"xi-api-key": api_key},
            )
        if resp.status_code in (200, 204):
            return jsonify({"status": "deleted", "voice_id": voice_id})
        return jsonify({"error": f"Lỗi {resp.status_code}: {resp.text[:200]}"}), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


_clone_cap_cache = {"key": None, "data": None}


def _clone_capability(api_key: str) -> dict:
    """Tai khoan co duoc phep tao voice clone khong (co cache theo key)."""
    if not api_key:
        return {"can_clone": False, "reason": "no_api_key"}
    if _clone_cap_cache["key"] == api_key and _clone_cap_cache["data"]:
        return _clone_cap_cache["data"]

    import httpx as _httpx
    from core import engine_config as _cfg
    info = {"can_clone": False, "reason": "unknown"}
    try:
        with _httpx.Client(timeout=15, verify=False) as client:
            r = client.get(f"{_cfg.ELEVENLABS_API_BASE}/v1/user",
                           headers={"xi-api-key": api_key})
        if r.status_code == 200:
            d = r.json()
            sub = d.get("subscription", {}) or {}
            can = bool(d.get("can_use_instant_voice_cloning",
                             sub.get("can_use_instant_voice_cloning", False)))
            info = {
                "can_clone": can,
                "reason": "" if can else "paid_plan_required",
                "tier": sub.get("tier", ""),
                "voice_limit": sub.get("voice_limit"),
                "voice_slots_used": sub.get("voice_slots_used"),
            }
        elif r.status_code == 401:
            info = {"can_clone": False, "reason": "invalid_api_key"}
    except Exception as e:
        log.warning("[Clone] Khong kiem tra duoc quyen clone: %s", str(e)[:80])
        info = {"can_clone": False, "reason": "check_failed"}

    _clone_cap_cache["key"] = api_key
    _clone_cap_cache["data"] = info
    return info


@app.route("/api/apikey/status")
def apikey_status():
    """Check API key status."""
    return jsonify({
        "has_key": bool(_api_key),
        "key_preview": _api_key[:8] + "..." if _api_key and len(_api_key) > 8 else "",
    })


@app.route("/api/minimax/clone/probe", methods=["POST"])
def minimax_clone_probe():
    """Kiem tra token MiniMax vua lay duoc lam duoc nhung gi."""
    from core import minimax_clone as _mc
    auth = minimax_scraper.get_auth_data() or {}
    if not auth.get("op_ticket"):
        return jsonify({
            "error": "Chua co op_ticket MiniMax. Bam 'Bat dau lay Token' va dang nhap truoc.",
        }), 401
    return jsonify(_mc.probe_session(auth, proxy_rotator.get_next()
                                     if proxy_rotator.enabled else None))


@app.route("/api/minimax/clone", methods=["POST"])
def minimax_clone():
    """Tao voice clone tren MiniMax bang token cua phien dang nhap web."""
    import tempfile
    from core import minimax_clone as _mc

    auth = minimax_scraper.get_auth_data() or {}
    if not auth.get("op_ticket"):
        return jsonify({
            "error": "Chua co op_ticket MiniMax. Bam 'Bat dau lay Token' va dang nhap truoc.",
        }), 401

    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Vui long dat ten cho voice clone."}), 400

    files = request.files.getlist("files")
    if not files or not any(f.filename for f in files):
        return jsonify({"error": "Vui long tai len 1 file audio mau."}), 400

    src = files[0]
    suffix = os.path.splitext(src.filename or "")[1] or ".mp3"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        src.save(tmp.name)
        tmp.close()
        # Buoc clone_v2 phai chay TRONG trang: no doi chu ky "yy" do
        # interceptor cua web sinh ra. Gui lai y nguyen body tu Python deu bi
        # tu choi (da thu ca payload that cua nguoi dung).
        if minimax_scraper._running:
            voice_id, err = _mc.create_clone_via_browser(
                scraper=minimax_scraper,
                auth=auth,
                file_path=tmp.name,
                name=name,
                language_tag=request.form.get("language_tag", "Vietnamese").strip()
                             or "Vietnamese",
                preview_text=request.form.get("preview_text", "").strip(),
                proxy_url=proxy_rotator.get_next() if proxy_rotator.enabled else None,
            )
        else:
            voice_id = None
            err = ("Hay bam 'Mo trinh duyet MiniMax' trong Cai Dat truoc khi tao "
                   "clone - buoc cuoi bat buoc chay trong trang web dang mo.")
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    if err:
        return jsonify({"error": err}), 502
    return jsonify({
        "status": "ok",
        "voice_id": voice_id,
        "name": name,
        "message": "Da tao voice clone '%s' tren MiniMax." % name,
    })


@app.route("/api/voices/clone/capability")
def voices_clone_capability():
    """Cho UI biet truoc co tao clone duoc khong, tranh ghi am xong moi bao loi."""
    return jsonify(_clone_capability(_api_key))


# ===============================================================
# BATCH PROCESSING
# ===============================================================

@app.route("/api/batch/start", methods=["POST"])
def batch_start():
    """Start batch processing.

    Body:
        files: list of {name, text} dicts
        voice_id: str
        model_id: str (optional)
        language: str (optional)
        voice_settings: dict (optional)
        output_dir: str (optional)
        output_format: str (optional)
        split_mode: str (optional)
    """
    data = request.get_json(force=True)

    if batch_processor.is_running:
        return jsonify({"error": "Batch already running"}), 409

    # Configure
    batch_processor.clear()
    provider = data.get("provider", "elevenlabs")
    batch_processor.provider = provider
    
    if provider == "minimax":
        batch_processor.voice_id = data.get("voice_id", cfg.MINIMAX_DEFAULT_VOICE)
        batch_processor.model_id = data.get("model_id", cfg.MINIMAX_DEFAULT_MODEL)
        batch_processor.voice_settings = data.get("voice_settings", dict(cfg.MINIMAX_VOICE_SETTINGS))
    else:
        batch_processor.voice_id = data.get("voice_id", cfg.DEFAULT_VOICE_ID)
        batch_processor.model_id = data.get("model_id", cfg.DEFAULT_MODEL_ID)
        batch_processor.voice_settings = data.get("voice_settings", dict(cfg.VOICE_SETTINGS))
        
    batch_processor.language = data.get("language", cfg.DEFAULT_LANGUAGE)
    batch_processor.output_dir = data.get("output_dir", str(BASE_DIR / "output"))
    batch_processor.output_format = data.get("output_format", cfg.DEFAULT_OUTPUT_FORMAT)
    batch_processor.split_mode = data.get("split_mode", cfg.DEFAULT_SPLIT_MODE)

    # Add texts
    texts = data.get("files", [])
    if texts:
        batch_processor.add_texts(texts)

    # Start
    batch_processor.start()
    push_event("batch_started", {"total_files": len(texts)})

    return jsonify({"status": "started", "total_files": len(texts)})

@app.route("/api/batch/stop", methods=["POST"])
def batch_stop():
    """Stop batch processing."""
    batch_processor.stop()
    return jsonify({"status": "stopped"})

@app.route("/api/batch/progress")
def batch_progress():
    """Get batch processing progress."""
    return jsonify(batch_processor.get_progress())

@app.route("/api/batch/upload", methods=["POST"])
def batch_upload():
    """Upload files for batch processing."""
    if "files" not in request.files:
        return jsonify({"error": "No files"}), 400

    uploaded = []
    upload_dir = str(BASE_DIR / "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    for f in request.files.getlist("files"):
        if f.filename:
            save_path = os.path.join(upload_dir, f.filename)
            f.save(save_path)
            # Read content
            from core.text_utils import read_text_file
            content = read_text_file(save_path)
            uploaded.append({
                "name": f.filename,
                "text": content,
                "chars": len(content),
                "path": save_path,
            })

    return jsonify({"files": uploaded})


# ===============================================================
# PROFILES
# ===============================================================

@app.route("/api/profiles", methods=["GET"])
def profiles_list():
    return jsonify({"profiles": profile_manager.list_profiles()})

@app.route("/api/profiles", methods=["POST"])
def profiles_create():
    data = request.get_json(force=True)
    profile = profile_manager.add_profile(data)
    return jsonify(profile), 201

@app.route("/api/profiles/<profile_id>", methods=["PUT"])
def profiles_update(profile_id):
    data = request.get_json(force=True)
    profile = profile_manager.update_profile(profile_id, data)
    if profile:
        return jsonify(profile)
    return jsonify({"error": "Profile not found"}), 404

@app.route("/api/profiles/<profile_id>", methods=["DELETE"])
def profiles_delete(profile_id):
    if profile_manager.delete_profile(profile_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Profile not found"}), 404


# ===============================================================
# PROXY
# ===============================================================

@app.route("/api/proxy/load", methods=["POST"])
def proxy_load():
    """Load proxy list.

    Body: { proxies: ["http://host:port", ...] }
    """
    data = request.get_json(force=True)
    proxies = data.get("proxies", [])
    proxy_rotator.load_proxies(proxies)
    return jsonify({"loaded": proxy_rotator.count})

@app.route("/api/proxy/test", methods=["POST"])
def proxy_test():
    """Test a single proxy."""
    data = request.get_json(force=True)
    proxy_url = data.get("proxy", "")
    result = proxy_rotator.test_proxy(proxy_url)
    return jsonify(result)

@app.route("/api/proxy/stats")
def proxy_stats():
    return jsonify({
        "enabled": proxy_rotator.enabled,
        "total": proxy_rotator.count,
        "active": proxy_rotator.active_count,
        "proxies": proxy_rotator.get_stats(),
    })

@app.route("/api/proxy/toggle", methods=["POST"])
def proxy_toggle():
    data = request.get_json(force=True)
    proxy_rotator.set_enabled(data.get("enabled", False))
    return jsonify({"enabled": proxy_rotator.enabled})


# ===============================================================
# CAPTCHA
# ===============================================================

@app.route("/api/captcha/start", methods=["POST"])
@app.route("/api/captcha/start-manual", methods=["POST"])
def captcha_start():
    """Open a visible browser for a user-assisted CAPTCHA challenge."""
    data = request.get_json(silent=True) or {}
    proxy = data.get("proxy", "")
    manual = data.get("mode", "manual") == "manual"

    # A hidden legacy session cannot become visible after launch. Stop it
    # before opening the manual browser requested by the user.
    if manual and captcha_solver._running and not captcha_solver._manual_mode:
        captcha_solver.stop()

    if not proxy and proxy_rotator.enabled:
        proxy = proxy_rotator.get_next() or ""
    if proxy:
        captcha_solver.set_proxy(proxy)
    captcha_solver._headless = False if manual else bool(data.get("headless", True))
    captcha_solver.start(manual=manual)
    return jsonify({
        "status": "manual_starting" if manual else "starting",
        "proxy": proxy or None,
        "headless": captcha_solver._headless,
        "mode": "manual" if manual else "legacy",
        "message": "Hoàn thành CAPTCHA trong cửa sổ Camoufox." if manual else "Solver đang khởi động.",
    })

@app.route("/api/captcha/stop", methods=["POST"])
def captcha_stop():
    captcha_solver.stop()
    return jsonify({"status": "stopped"})

@app.route("/api/captcha/status")
def captcha_status():
    status = captcha_solver.get_status()
    status["headless"] = captcha_solver._headless
    return jsonify(status)

@app.route("/api/captcha/manual", methods=["POST", "OPTIONS"])
def captcha_manual():
    """Set a manual captcha token."""
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(force=True)
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "No token"}), 400
    captcha_solver.set_manual_token(token)
    log.info("Manual captcha token set (len=%d)", len(token))
    return jsonify({"status": "set", "message": "Token received!"})

@app.route("/api/captcha/refresh", methods=["POST"])
def captcha_refresh():
    captcha_solver.refresh()
    return jsonify({"status": "refreshing"})


@app.route("/api/captcha/ensure", methods=["POST"])
def captcha_ensure():
    """Report readiness without starting a hidden CAPTCHA solver."""
    status = captcha_solver.get_status()
    ready = status["has_token"]
    running = status["running"]
    return jsonify({
        "ready": ready,
        "running": running,
        "expires_in": status["expires_in"],
        "fatal_error": status.get("fatal_error"),
        "requires_user_action": not ready,
        "mode": status.get("mode"),
        "status": status.get("status"),
        "message": (
            f"Lỗi: {status['fatal_error']}" if status.get("fatal_error")
            else "Token sẵn sàng" if ready
            else "Hoàn thành CAPTCHA trong cửa sổ Camoufox." if running
            else "Nhấn Mở CAPTCHA thủ công trước khi tạo giọng."
        )
    })


@app.route("/api/captcha/reset-fatal", methods=["POST"])
def captcha_reset_fatal():
    """Xóa fatal error flag để có thể thử lại (sau khi chạy camoufox fetch)."""
    captcha_solver.clear_fatal_error()
    return jsonify({"status": "ok", "message": "Fatal error đã được xóa. Có thể thử lại."})

@app.route("/api/captcha/logout", methods=["POST"])
def captcha_logout():
    """Xoa session ElevenLabs (dang xuat)."""
    import os
    from core.captcha_solver import SESSION_FILE
    captcha_solver.refresh()  # clear token
    # Xóa file session → lần sau phải đăng nhập lại
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
            msg = "Đã xóa session. Lần sau sẽ phải đăng nhập lại."
        except Exception as e:
            msg = f"Lỗi xóa session: {e}"
    else:
        msg = "Không có session để xóa."
    return jsonify({"status": "logged_out", "message": msg})

@app.route("/api/captcha/session-status")
def captcha_session_status():
    """Kiểm tra session ElevenLabs đã lưu chưa."""
    import os
    from core.captcha_solver import SESSION_FILE
    has_session = os.path.exists(SESSION_FILE)
    session_size = 0
    if has_session:
        try:
            session_size = os.path.getsize(SESSION_FILE)
        except Exception:
            pass
    return jsonify({
        "has_session": has_session,
        "session_file": SESSION_FILE,
        "session_size_bytes": session_size,
    })


@app.route("/api/captcha/headless", methods=["POST"])
def captcha_set_headless():
    """CAPTCHA is always visible so the user can complete it themselves."""
    captcha_solver._headless = False
    return jsonify({
        "headless": False,
        "mode": "manual",
        "message": "Chế độ CAPTCHA thủ công luôn hiển thị trình duyệt.",
    })


# ===============================================================
# MINIMAX SCRAPER
# ===============================================================

@app.route("/api/minimax_scraper/start", methods=["POST"])
def minimax_scraper_start():
    data = request.get_json(silent=True) or {}
    proxy = data.get("proxy", "")
    headless = data.get("headless", minimax_scraper._headless)
    if not proxy and proxy_rotator.enabled:
        proxy = proxy_rotator.get_next() or ""
    if proxy:
        minimax_scraper.set_proxy(proxy)
    minimax_scraper._headless = bool(headless)
    # keep_open=true: giu cua so mo de ghi lai payload API khi nguoi dung
    # thao tac tren web (dung de hoc hinh dang request clone_v2).
    minimax_scraper.keep_open = bool(data.get("keep_open", False))
    minimax_scraper.start()
    return jsonify({"status": "starting", "proxy": proxy or None, "headless": minimax_scraper._headless})

@app.route("/api/minimax_scraper/open", methods=["POST"])
def minimax_scraper_open():
    """Mo lai cua so MiniMax de thao tac tay (tao clone, xoa giong...).

    Cua so duoc giu mo va moi request API cua trang deu duoc ghi lai vao
    minimax_api_capture.json - do la cach chac chan de biet payload that.
    """
    data = request.get_json(silent=True) or {}
    page = (data.get("page") or "voices-cloning").strip("/")
    url = "https://www.minimax.io/audio" + ("/" + page if page else "")

    if minimax_scraper._running:
        minimax_scraper.stop()
        time.sleep(1)

    proxy = data.get("proxy", "")
    if not proxy and proxy_rotator.enabled:
        proxy = proxy_rotator.get_next() or ""
    if proxy:
        minimax_scraper.set_proxy(proxy)

    minimax_scraper._headless = False
    minimax_scraper.keep_open = True
    minimax_scraper.start_url = url
    minimax_scraper.start()
    return jsonify({"status": "opening", "url": url, "keep_open": True})


@app.route("/api/tts/dialogue/parse", methods=["POST"])
def dialogue_parse():
    """Doc kich ban, tra ve cac luot noi va danh sach nhan vat de UI gan giong."""
    from core import dialogue as _dlg
    data = request.get_json(force=True) or {}
    known = data.get("speakers") or []
    turns = _dlg.parse_script(data.get("script", ""), known_speakers=known)
    return jsonify({
        "turns": [t.to_dict() for t in turns],
        "speakers": _dlg.list_speakers(turns),
        "total_chars": sum(len(t.text) for t in turns),
    })


@app.route("/api/tts/dialogue", methods=["POST"])
def tts_dialogue():
    """Tao hoi thoai nhieu nhan vat: moi nhan vat mot giong, ghep lai mot file.

    Body: {script, voices: {"Ten nhan vat": {provider, voice_id, model_id,
           language, speed, pitch, vol}}, gap_same_ms, gap_change_ms}
    """
    from core import dialogue as _dlg
    from core import tts_cache as _cache

    data = request.get_json(force=True) or {}
    script = (data.get("script") or "").strip()
    voices = data.get("voices") or {}
    if not script:
        return jsonify({"error": "Chua co kich ban."}), 400
    if not voices:
        return jsonify({"error": "Chua gan giong cho nhan vat nao."}), 400

    turns = _dlg.parse_script(script, known_speakers=list(voices.keys()))
    if not turns:
        return jsonify({"error": "Kich ban khong co luot noi nao."}), 400

    order = _dlg.list_speakers(turns)
    missing = [s for s in order if not _dlg.resolve_voice(s, voices, order)]
    if missing:
        return jsonify({"error": "Chua gan giong cho: %s" % ", ".join(missing)}), 400

    segments = []
    for i, turn in enumerate(turns):
        # Dat ten 'vcfg' de khong che module config toan cuc ('cfg').
        vcfg = _dlg.resolve_voice(turn.speaker, voices, order) or {}
        provider = vcfg.get("provider", "minimax")
        voice_id = vcfg.get("voice_id", "")
        model_id = vcfg.get("model_id") or (
            "speech-2.8-hd" if provider == "minimax" else cfg.DEFAULT_MODEL_ID)
        params = {
            "provider": provider, "voice_id": voice_id, "model_id": model_id,
            "language_code": vcfg.get("language", "vi"),
            "speed": vcfg.get("speed", 1.0), "pitch": vcfg.get("pitch", 0),
            "vol": vcfg.get("vol", 1.0),
        }

        audio = _cache.get(turn.text, params)
        if audio:
            segments.append((turn.speaker, audio))
            continue

        if provider == "minimax":
            from core.minimax_engine import tts_web_session
            audio, err = tts_web_session(
                scraper=minimax_scraper, text=turn.text, voice_id=voice_id,
                model=model_id, speed=float(params["speed"]),
                pitch=float(params["pitch"]), vol=float(params["vol"]),
                language_boost=vcfg.get("language_boost", "Vietnamese"))
        else:
            # Token captcha dung MOT LAN: luot thu hai tro di se dinh
            # captcha_expired neu khong lay token moi. Thu lai co lam moi.
            audio, err = None, None
            for attempt in range(3):
                if not _api_key:
                    captcha_solver.ensure_running()
                    token = captcha_solver.get_token() or ""
                    if not token:
                        captcha_solver.wait_for_token(timeout=120)
                        token = captcha_solver.get_token() or ""
                else:
                    token = ""
                audio, err = TTSEngine(proxy_url=None).synthesize(
                    text=turn.text, voice_id=voice_id,
                    api_key=_api_key if _api_key else None, captcha_token=token,
                    model_id=model_id, language_code=params["language_code"],
                    voice_settings=vcfg.get("voice_settings") or {})
                if audio:
                    break
                if err in ("captcha_expired", "auth_expired") and not _api_key:
                    captcha_solver.refresh()
                    captcha_solver.wait_for_token(timeout=90)
                    continue
                if str(err or "").startswith("rate_limited"):
                    time.sleep(8)
                    continue
                break

        if not audio:
            return jsonify({
                "error": "Luot %d/%d (%s) that bai: %s" % (
                    i + 1, len(turns), turn.speaker, err),
                "done_turns": len(segments),
            }), 502

        _cache.put(turn.text, params, audio)
        segments.append((turn.speaker, audio))
        log.info("[Dialogue] Luot %d/%d xong (%s, %d byte)",
                 i + 1, len(turns), turn.speaker, len(audio))

    out_name = "dialogue_%d.mp3" % int(time.time())
    out_dir = str(BASE_DIR / "output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, out_name)
    ok = _dlg.merge_turns(
        segments, out_path,
        gap_same_ms=int(data.get("gap_same_ms", _dlg.GAP_SAME_SPEAKER_MS)),
        gap_change_ms=int(data.get("gap_change_ms", _dlg.GAP_SPEAKER_CHANGE_MS)))
    if not ok:
        return jsonify({"error": "Ghep audio that bai (kiem tra ffmpeg)."}), 500

    return jsonify({
        "status": "ok", "file": out_name, "path": out_path,
        "turns": len(turns), "speakers": order,
        "size": os.path.getsize(out_path),
    })


@app.route("/api/cache/stats")
def cache_stats():
    from core import tts_cache as _cache
    return jsonify(_cache.stats())


@app.route("/api/cache/clear", methods=["POST"])
def cache_clear():
    from core import tts_cache as _cache
    data = request.get_json(silent=True) or {}
    removed, freed = _cache.clear(float(data.get("older_than_days", 0) or 0))
    return jsonify({"removed": removed, "freed_mb": round(freed / 1048576.0, 2)})


@app.route("/api/cache/toggle", methods=["POST"])
def cache_toggle():
    from core import tts_cache as _cache
    data = request.get_json(silent=True) or {}
    _cache.enabled = bool(data.get("enabled", True))
    return jsonify({"enabled": _cache.enabled})


@app.route("/api/minimax/voices")
def minimax_voices():
    """Danh sach giong MiniMax (ke ca giong clone cua nguoi dung)."""
    from core import minimax_clone as _mc
    auth = minimax_scraper.get_auth_data() or {}
    vtype = request.args.get("type", "all")
    try:
        data = _mc.list_voices(auth, vtype)
    except Exception as e:
        return jsonify({"error": str(e)[:200], "voices": [], "cloned": []}), 502

    raw = data.get("voice_list", []) if isinstance(data, dict) else []
    voices = [_mc.normalize_voice(v) for v in raw]
    cloned = [v for v in voices if v["cloned"]]
    return jsonify({
        "voices": voices,
        "cloned": cloned,
        "total": len(voices),
        "cloned_count": len(cloned),
        "has_session": bool(auth.get("op_ticket")),
    })


@app.route("/api/minimax/equity")
def minimax_equity():
    """So slot giong con lai cua tai khoan MiniMax."""
    from core import minimax_clone as _mc
    auth = minimax_scraper.get_auth_data() or {}
    if not auth.get("op_ticket"):
        return jsonify({"error": "chua_dang_nhap", "has_session": False}), 401
    try:
        eq = _mc.get_equity(auth)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502
    used, total = eq.get("used", 0), eq.get("total", 0)
    return jsonify({
        "has_session": True,
        "used": used,
        "total": total,
        "free": max(0, total - used),
        "browser_open": bool(minimax_scraper._running),
    })


@app.route("/api/minimax_scraper/call", methods=["POST"])
def minimax_scraper_call():
    """Goi mot endpoint /v1/api/... tu ben trong trang dang mo.

    Dung de do hinh dang API ma khong phai doan: request di qua axios cua web
    nen co du chu ky yy va cac tham so ma server doi.
    """
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    if not path.startswith("/v1/api/"):
        return jsonify({"error": "path phai bat dau bang /v1/api/"}), 400
    if not minimax_scraper._running:
        return jsonify({"error": "Chua mo trinh duyet MiniMax."}), 409
    return jsonify(minimax_scraper.call_web_api(path, data.get("payload") or {}))


@app.route("/api/minimax_scraper/capture")
def minimax_scraper_capture():
    """Doc lai cac payload API da ghi duoc tu thao tac tren web."""
    from core.minimax_scraper import MM_CAPTURE_FILE
    if not os.path.exists(MM_CAPTURE_FILE):
        return jsonify({"entries": [], "message": "Chua ghi duoc payload nao."})
    try:
        with open(MM_CAPTURE_FILE, "r", encoding="utf-8") as f:
            return jsonify({"entries": json.load(f)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/minimax_scraper/stop", methods=["POST"])
def minimax_scraper_stop():
    minimax_scraper.stop()
    return jsonify({"status": "stopped"})

@app.route("/api/minimax_scraper/status")
def minimax_scraper_status():
    status = minimax_scraper.get_status()
    status["headless"] = minimax_scraper._headless
    return jsonify(status)

# ===============================================================
# SETTINGS & STATUS
# ===============================================================

@app.route("/api/status")
def app_status():
    """Get overall application status."""
    from core.captcha_solver import SESSION_FILE
    return jsonify({
        "captcha": captcha_solver.get_status(),
        "session": {
            "has_session": os.path.exists(SESSION_FILE),
        },
        "minimax_scraper": minimax_scraper.get_status(),
        "api_key": {
            "has_key": bool(_api_key),
            "key_preview": _api_key[:8] + "..." if _api_key and len(_api_key) > 8 else "",
        },
        "minimax_api_key": {
            "has_key": bool(_minimax_api_key),
            "key_preview": _minimax_api_key[:8] + "..." if _minimax_api_key and len(_minimax_api_key) > 8 else "",
        },
        "proxy": {
            "enabled": proxy_rotator.enabled,
            "total": proxy_rotator.count,
            "active": proxy_rotator.active_count,
        },
        "workers": worker_pool.stats,
        "batch": batch_processor.get_progress() if batch_processor.is_running else None,
        "ffmpeg": find_ffmpeg() is not None,
        "ffmpeg_path": find_ffmpeg(),
    })

@app.route("/api/settings/workers", methods=["POST"])
def set_workers():
    data = request.get_json(force=True)
    count = int(data.get("count", cfg.DEFAULT_WORKER_COUNT))
    worker_pool.set_worker_count(count)
    return jsonify({"workers": count})

@app.route("/api/output/files")
def output_files():
    """List files in output directory."""
    output_dir = str(BASE_DIR / "output")
    if not os.path.isdir(output_dir):
        return jsonify({"files": []})
    files = []
    for f in sorted(os.listdir(output_dir)):
        fp = os.path.join(output_dir, f)
        if os.path.isfile(fp):
            files.append({
                "name": f,
                "size": os.path.getsize(fp),
                "modified": os.path.getmtime(fp),
            })
    return jsonify({"files": files})

@app.route("/api/output/<filename>")
def output_download(filename):
    """Download an output file."""
    output_dir = str(BASE_DIR / "output")
    return send_from_directory(output_dir, filename, as_attachment=True)


# ===============================================================
# MAIN
# ===============================================================

if __name__ == "__main__":
    os.makedirs(str(BASE_DIR / "output"), exist_ok=True)
    os.makedirs(str(BASE_DIR / "uploads"), exist_ok=True)

    port = int(os.environ.get("PORT", 5055))
    log.info("=" * 50)
    log.info("  ElevenLabs Voice Pro")
    log.info("  http://localhost:%d", port)
    log.info("=" * 50)

    # Auto-open browser
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

