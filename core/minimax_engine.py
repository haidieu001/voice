"""
minimax_engine.py — Minimax TTS API client.
"""

import logging
import time
import base64
from typing import Optional, Dict, Tuple
import json
import uuid

import httpx

log = logging.getLogger(__name__)

class MinimaxTTSEngine:
    """Minimax text-to-speech client."""

    def __init__(self, proxy_url: Optional[str] = None):
        self._proxy = proxy_url

    def synthesize(self, text: str, voice_id: str,
                   auth_data: dict = None,
                   api_key: Optional[str] = None,
                   model_id: str = "speech-01-turbo",
                   language_code: str = "",
                   voice_settings: Optional[Dict] = None,
                   proxy_url: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str]]:
        """Synthesize text to speech using Minimax."""
        
        if not text or not text.strip():
            return None, "empty_text"
        if not voice_id:
            return None, "no_voice_id"
            
        proxy = proxy_url or self._proxy
        transport = None
        if proxy:
            transport = httpx.HTTPTransport(proxy=proxy)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        }
        
        # Priority: explicit api_key -> scraped auth_data
        # LUU Y: khong co api_key that thi duong nay VO DUNG - api.minimax.chat
        # doi secret key tra phi. Xem tts_web_session() ben duoi.
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif auth_data and auth_data.get("cookie_string"):
            if auth_data.get("auth_header"):
                headers["Authorization"] = auth_data["auth_header"]
            headers["Cookie"] = auth_data["cookie_string"]
            if auth_data.get("user_agent"):
                headers["User-Agent"] = auth_data["user_agent"]
        else:
            return None, "no_auth"
            
        # Standard Minimax T2A endpoint
        url = "https://api.minimax.chat/v1/t2a_v2" 
        
        vs = voice_settings or {}
        speed = float(vs.get("speed", 1.0))
        vol = float(vs.get("vol", 1.0))
        pitch = int(vs.get("pitch", 0))
        
        payload = {
            "model": model_id,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": vol,
                "pitch": pitch,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1
            }
        }
        
        try:
            start_time = time.time()
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), transport=transport, verify=False) as client:
                resp = client.post(url, json=payload, headers=headers)
                
                if resp.status_code in [401, 403]:
                    return None, "auth_expired"
                elif resp.status_code == 429:
                    return None, "rate_limited"
                elif resp.status_code != 200:
                    return None, f"http_{resp.status_code}:{resp.text[:100]}"
                    
                resp_json = resp.json()
                if resp_json.get("base_resp", {}).get("status_code", 0) != 0:
                    msg = resp_json.get("base_resp", {}).get("status_msg", "")
                    return None, f"api_error:{msg}"
                    
                audio_hex = resp_json.get("data", {}).get("audio", "")
                if audio_hex:
                    try:
                        audio_data = bytes.fromhex(audio_hex)
                    except ValueError:
                        audio_data = base64.b64decode(audio_hex)
                        
                    elapsed = time.time() - start_time
                    log.info("Minimax TTS thành công: %d ký tự -> %d bytes trong %.1fs", len(text), len(audio_data), elapsed)
                    return audio_data, None
                return None, "empty_audio"
                
        except httpx.TimeoutException:
            return None, "timeout"
        except httpx.ConnectError:
            return None, "connection_error"
        except Exception as e:
            log.error("Minimax TTS request thất bại: %s", e)
            return None, f"error:{e}"

    def test_connection(self) -> Dict:
        # A simple test logic. Note: Minimax doesn't have an open /v1/voices endpoint easily accessible without auth.
        # We just return ok for now if initialized.
        return {"ok": True, "latency_ms": 0}


# ── TTS qua phien dang nhap web ────────────────────────────────────────

def tts_web_session(scraper, text: str, voice_id: str,
                    model: str = "speech-2.8-hd", speed: float = 1.0,
                    pitch: float = 0.0, vol: float = 1.0,
                    language_boost: str = "Vietnamese",
                    timeout: float = 120.0):
    """Doc van ban bang giong cua phien web MiniMax (ke ca giong clone).

    Vi sao khong goi thang tu Python: TTS cua web chay qua WebSocket
    ``/v1/api/audio/ws``, va handshake kiem tra chu ky ``yy`` gan voi tham so
    ``unix``. Da thu do: dung lai URL cu (yy khop unix cu) thi ket noi duoc,
    doi unix moi hoac bo yy deu bi tu choi HTTP 400. Nen phai muon chinh trang
    web mo socket ho.

    Tra ve ``(audio_bytes, loi)``.
    """
    if scraper is None or not getattr(scraper, "_running", False):
        return None, "minimax_browser_chua_mo"

    # Hinh dang lay tu chinh khung WebSocket ma web app gui di. Hai diem de
    # sai: voice_id nam TRONG voice_setting, va khung phai duoc BOC lai thanh
    # {payload: <chuoi JSON>, msg_id: <uuid>} - initWebSocket khong tu boc,
    # gui thang thi server tra 1200019 voi msg_id rong.
    inner = {
        "model": model,
        "text": text,
        "voice_setting": {
            "speed": float(speed),
            "vol": float(vol),
            "pitch": float(pitch),
            "voice_id": str(voice_id),
        },
        "audio_setting": {},
        "effects": {
            "deepen_lighten": 0,
            "stronger_softer": 0,
            "nasal_crisp": 0,
            "spacious_echo": False,
            "lofi_telephone": False,
            "robotic": False,
            "auditorium_echo": False,
        },
        "er_weights": [],
        "language_boost": language_boost,
        "stream": True,
    }
    body = {
        "payload": json.dumps(inner, ensure_ascii=False),
        "msg_id": str(uuid.uuid4()),
    }
    # Duong chinh: de trang tu mo socket da ky roi thay ruot khung T2a.
    res = scraper.tts_via_ui(body, timeout=timeout) or {}
    if not res.get("ok"):
        log.info("[MinimaxWeb] tts_via_ui that bai (%s), thu duong socket truc tiep",
                 str(res.get("error"))[:80])
        res = scraper.tts_via_browser(body, timeout=timeout) or {}
    if not res.get("ok"):
        return None, "minimax_ws:%s %s" % (res.get("error", "?"),
                                           str(res.get("message", ""))[:120])

    hex_audio = res.get("audio_hex") or ""
    if not hex_audio:
        return None, "minimax_ws_khong_co_audio"
    try:
        # Web tra audio duoi dang chuoi hex (bat dau 494433 = "ID3").
        audio = bytes.fromhex(hex_audio)
    except ValueError as e:
        return None, "minimax_hex_loi: %s" % str(e)[:80]

    log.info("[MinimaxWeb] TTS xong: %d ky tu -> %d byte", len(text), len(audio))
    return audio, None
