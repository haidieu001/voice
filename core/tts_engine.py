"""
tts_engine.py — ElevenLabs TTS API client.

Supports TWO modes:
  1. API Key mode (recommended): Uses official /v1/text-to-speech endpoint
  2. Anonymous mode: Uses anonymous endpoint with hCaptcha token
"""

import json
import base64
import random
import logging
import time
from typing import Optional, List, Dict, Tuple

import re

import httpx

try:
    import tls_client as _tls_client
    _HAS_TLS_CLIENT = True
except ImportError:
    _HAS_TLS_CLIENT = False

from . import engine_config as cfg

log = logging.getLogger(__name__)


_RETRY_AFTER_RE = re.compile(r"retry[-_]?after[:=]\s*([0-9]+(?:\.[0-9]+)?)", re.I)

# Dau hieu trong than loi 401 de tach nguyen nhan that su.
_IP_FLAG_HINTS = (
    "unusual activity", "unusual_activity", "detected_unusual",
    "abuse", "blocked", "banned", "forbidden_region", "vpn", "proxy",
)
_QUOTA_HINTS = (
    "quota", "exceeded", "limit reached", "limit_reached",
    "too_many_concurrent", "concurrent_requests",
)
_POLICY_HINTS = (
    "against our policy", "content_against_policy", "policy violation",
    "moderation", "flagged_content",
)


def _mask_proxy(proxy_url) -> str:
    """Bo user:pass khoi proxy truoc khi ghi log."""
    if not proxy_url:
        return "(direct)"
    return re.sub(r"://[^@]+@", "://", str(proxy_url))


def _headers_to_dict(headers) -> dict:
    """Chuan hoa headers cua httpx / tls_client ve dict chu thuong."""
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers).items()}
    except Exception:
        return {}


def parse_retry_after(headers=None, body: str = "", default: float = 30.0) -> float:
    """Lay so giay phai cho tu header Retry-After hoac tu than phan hoi."""
    hdr = _headers_to_dict(headers).get("retry-after", "")
    if hdr:
        try:
            return max(1.0, float(hdr.strip()))
        except (TypeError, ValueError):
            pass
    m = _RETRY_AFTER_RE.search(body or "")
    if m:
        try:
            return max(1.0, float(m.group(1)))
        except ValueError:
            pass
    return default


def classify_401(headers=None, body: str = "") -> str:
    """Tach 401 thanh ``IP_FLAGGED`` | ``IP_QUOTA`` | ``TOKEN``.

    Ba truong hop nay can xu ly khac han nhau: co IP thi phai doi proxy, het
    quota thi phai doi IP va cho, con token het han thi chi can giai lai
    captcha. Gop chung lam mot se doi proxy oan hoac giai captcha vo ich.
    """
    text = (body or "").lower()
    joined = text + " " + " ".join(_headers_to_dict(headers).values()).lower()
    if any(h in joined for h in _IP_FLAG_HINTS):
        return "IP_FLAGGED"
    if any(h in joined for h in _QUOTA_HINTS):
        return "IP_QUOTA"
    return "TOKEN"


def is_policy_error(body: str = "") -> bool:
    """Noi dung bi chan boi chinh sach — thu lai bao nhieu lan cung hong."""
    low = (body or "").lower()
    return any(h in low for h in _POLICY_HINTS)


class TTSEngine:
    """ElevenLabs text-to-speech client."""

    def __init__(self, proxy_url: Optional[str] = None, api_key: Optional[str] = None):
        self._proxy = proxy_url
        self._api_key = api_key

    def synthesize(self, text: str, voice_id: str,
                   captcha_token: str = "",
                   api_key: Optional[str] = None,
                   model_id: str = "",
                   language_code: str = "",
                   voice_settings: Optional[Dict] = None,
                   proxy_url: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str]]:
        """Synthesize text to speech.

        Uses API key if available, otherwise falls back to anonymous+captcha.

        Returns:
            Tuple of (audio_bytes, error_message).
        """
        if not text or not text.strip():
            return None, "empty_text"
        if not voice_id:
            return None, "no_voice_id"

        key = api_key or self._api_key
        if key:
            return self._synthesize_api_key(text, voice_id, key,
                                            model_id, language_code,
                                            voice_settings, proxy_url)
        elif captcha_token:
            return self._synthesize_anonymous(text, voice_id, captcha_token,
                                             model_id, language_code,
                                             voice_settings, proxy_url)
        else:
            return None, "no_auth"

    def _synthesize_api_key(self, text, voice_id, api_key,
                            model_id, language_code,
                            voice_settings, proxy_url) -> Tuple[Optional[bytes], Optional[str]]:
        """Synthesize using official API key endpoint (direct MP3 response)."""
        model_id = model_id or cfg.DEFAULT_MODEL_ID
        language_code = language_code or cfg.DEFAULT_LANGUAGE
        vs = voice_settings or dict(cfg.VOICE_SETTINGS)

        url = f"{cfg.ELEVENLABS_API_BASE}/v1/text-to-speech/{voice_id}"

        payload = {
            "text": text,
            "model_id": model_id,
            "language_code": language_code,
            "voice_settings": {
                "stability": vs.get("stability", 0.75),
                "similarity_boost": vs.get("similarity_boost", 0.80),
                "style": vs.get("style", 0.30),
                "use_speaker_boost": vs.get("use_speaker_boost", True),
                "speed": vs.get("speed", 1.0),
            },
        }

        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        }

        proxy = proxy_url or self._proxy
        transport = None
        if proxy:
            transport = httpx.HTTPTransport(proxy=proxy)

        try:
            start_time = time.time()
            with httpx.Client(
                timeout=httpx.Timeout(60.0, connect=15.0),
                transport=transport,
                verify=False,
            ) as client:
                resp = client.post(url, json=payload, headers=headers)

                if resp.status_code == 401:
                    return None, "invalid_api_key"
                elif resp.status_code == 429:
                    return None, "rate_limited"
                elif resp.status_code == 400:
                    try:
                        err = resp.json()
                        detail = err.get("detail", {}).get("message", str(err))
                    except Exception:
                        detail = resp.text[:200]
                    return None, f"bad_request:{detail}"
                elif resp.status_code != 200:
                    return None, f"http_{resp.status_code}"

                audio_data = resp.content
                if not audio_data or len(audio_data) < 100:
                    return None, "empty_audio"

                elapsed = time.time() - start_time
                log.info("TTS (API key) thành công: %d ký tự → %d bytes trong %.1fs",
                         len(text), len(audio_data), elapsed)
                return audio_data, None

        except httpx.TimeoutException:
            return None, "timeout"
        except httpx.ConnectError:
            return None, "connection_error"
        except Exception as e:
            log.error("TTS request thất bại: %s", e)
            return None, f"error:{e}"

    def _synthesize_anonymous(self, text, voice_id, captcha_token,
                              model_id, language_code,
                              voice_settings, proxy_url) -> Tuple[Optional[bytes], Optional[str]]:
        """Synthesize using anonymous endpoint — dùng tls_client firefox_120 như v4.1."""
        model_id = model_id or cfg.DEFAULT_MODEL_ID
        language_code = language_code or cfg.DEFAULT_LANGUAGE
        vs = voice_settings or dict(cfg.VOICE_SETTINGS)

        endpoint = cfg.ELEVENLABS_ANON_ENDPOINT.format(voice_id=voice_id)
        url = f"{cfg.ELEVENLABS_API_BASE}{endpoint}"

        payload = {
            "text": text,
            "model_id": model_id,
            "language_code": language_code,
            "voice_settings": {
                "stability": max(0.0, min(1.0, vs.get("stability", 0.75))),
                "similarity_boost": max(0.0, min(1.0, vs.get("similarity_boost", 0.80))),
                "style": max(0.0, min(1.0, vs.get("style", 0.30))),
                "use_speaker_boost": vs.get("use_speaker_boost", True),
                "speed": vs.get("speed", 1.0),
            },
            "hcaptcha_token": captcha_token,
        }

        # Headers chính xác từ v4.1 binary (WIRE format)
        headers = dict(cfg.ELEVENLABS_HEADERS)
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"

        proxy = proxy_url or self._proxy
        start_time = time.time()

        log.info("[TTS] voice=%s model=%s lang=%s token_len=%d",
                 voice_id, model_id, language_code, len(captcha_token))

        # === 1. Ưu tiên: Gửi TTS request từ chính Camoufox browser session ===
        try:
            from . import captcha_solver as _cs
            if _cs._solver_instance and _cs._solver_instance._running:
                log.info("[TTS] Trying browser_synthesize inside Camoufox...")
                br = _cs.browser_synthesize(voice_id, payload, timeout=40.0)
                if br.get("ok") and br.get("body"):
                    audio_data = self._parse_streaming_response(br["body"])
                    if audio_data:
                        log.info("✅ TTS OK (browser_synthesize): %d chars → %d bytes (%.1fs)",
                                 len(text), len(audio_data), time.time() - start_time)
                        return audio_data, None
                    log.warning("[browser_synthesize] 200 OK but no audio_base64")
                elif "error" in br:
                    err_val = str(br["error"])
                    detail = br.get("detail", "")
                    log.warning("[browser_synthesize] Status: %s, Detail: %s", err_val, detail)
                    if "401" in err_val or "http_401" in err_val:
                        return None, "captcha_expired"
        except Exception as be:
            log.warning("[browser_synthesize] Exception: %s", be)

        # === 2. Fallback: tls_client firefox_120 ===
        if _HAS_TLS_CLIENT:
            try:
                session = _tls_client.Session(
                    client_identifier="firefox_120",
                    random_tls_extension_order=True,
                )
                if proxy:
                    session.proxies = {"http": proxy, "https": proxy}

                resp = session.post(url, json=payload, headers=headers, timeout_seconds=30)
                log.info("[tls_client firefox_120] TTS status: %d (%.1fs)",
                         resp.status_code, time.time() - start_time)

                if resp.status_code == 200:
                    audio_data = self._parse_streaming_response(resp.text)
                    if audio_data:
                        log.info("✅ TTS OK: %d chars → %d bytes (%.1fs)",
                                 len(text), len(audio_data), time.time() - start_time)
                        return audio_data, None
                    return None, "empty_audio_response"
                elif resp.status_code == 401:
                    body = resp.text or ""
                    kind = classify_401(getattr(resp, "headers", None), body)
                    log.warning("[401-MARK] kind=%s proxy=%s body=%s",
                                kind, _mask_proxy(proxy_url), body[:180])
                    if kind == "IP_FLAGGED":
                        return None, "ip_flagged"
                    if kind == "IP_QUOTA":
                        return None, "quota_exceeded"
                    return None, "captcha_expired"
                elif resp.status_code == 429:
                    delay = parse_retry_after(getattr(resp, "headers", None), resp.text or "")
                    log.warning("[tls_client] 429 — cho %.0fs roi thu lai", delay)
                    return None, "rate_limited:%d" % int(delay)
                elif resp.status_code == 422:
                    try:
                        err_body = resp.json()
                        detail = err_body.get("detail", "")
                        if isinstance(detail, list):
                            detail = "; ".join([d.get("msg", str(d)) for d in detail])
                        elif isinstance(detail, dict):
                            detail = detail.get("message", str(detail))
                    except Exception:
                        detail = resp.text[:300]
                    log.error("[tls_client] 422: %s", detail)
                    if is_policy_error(detail):
                        return None, "policy_blocked"
                    return None, f"validation_error:{detail}"
                else:
                    log.error("[tls_client] HTTP %d: %s", resp.status_code, resp.text[:200])
                    return None, f"http_{resp.status_code}"

            except Exception as e:
                log.warning("[tls_client] Error: %s — falling back to httpx", str(e)[:80])

        # === Fallback: httpx (JA3 không đúng nhưng vẫn thử) ===
        transport = None
        if proxy:
            transport = httpx.HTTPTransport(proxy=proxy)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(30.0, connect=10.0),
                transport=transport,
                verify=False,
                http2=True,
            ) as client:
                resp = client.post(url, json=payload, headers=headers)
                log.info("[httpx fallback] TTS status: %d", resp.status_code)

                if resp.status_code == 200:
                    audio_data = self._parse_streaming_response(resp.text)
                    if audio_data:
                        log.info("✅ TTS OK (httpx): %d chars → %d bytes",
                                 len(text), len(audio_data))
                        return audio_data, None
                    return None, "empty_audio_response"
                elif resp.status_code == 401:
                    body = resp.text or ""
                    kind = classify_401(resp.headers, body)
                    log.warning("[401-MARK] kind=%s proxy=%s body=%s",
                                kind, _mask_proxy(proxy_url), body[:180])
                    if kind == "IP_FLAGGED":
                        return None, "ip_flagged"
                    if kind == "IP_QUOTA":
                        return None, "quota_exceeded"
                    return None, "captcha_expired"
                elif resp.status_code == 429:
                    delay = parse_retry_after(resp.headers, resp.text or "")
                    log.warning("[httpx] 429 — cho %.0fs roi thu lai", delay)
                    return None, "rate_limited:%d" % int(delay)
                elif resp.status_code == 422:
                    try:
                        err_body = resp.json()
                        log.error("422: %s", json.dumps(err_body, ensure_ascii=False)[:400])
                        detail = err_body.get("detail", "")
                        if isinstance(detail, list):
                            detail = "; ".join([d.get("msg", str(d)) for d in detail])
                        elif isinstance(detail, dict):
                            detail = detail.get("message", str(detail))
                    except Exception:
                        detail = resp.text[:300]
                    if is_policy_error(detail):
                        return None, "policy_blocked"
                    return None, f"validation_error:{detail}"
                else:
                    log.error("[httpx] HTTP %d: %s", resp.status_code, resp.text[:200])
                    return None, f"http_{resp.status_code}"

        except httpx.TimeoutException:
            return None, "timeout"
        except httpx.ConnectError:
            return None, "connection_error"
        except Exception as e:
            log.error("TTS request failed: %s", e)
            return None, f"error:{e}"

    def _parse_streaming_response(self, body: str) -> Optional[bytes]:
        """Parse streaming JSON response containing audio_base64 chunks."""
        audio_chunks: List[bytes] = []
        for line in body.split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                b64 = obj.get("audio_base64", "")
                if b64:
                    audio_chunks.append(base64.b64decode(b64))
            except (json.JSONDecodeError, Exception):
                continue

        if not audio_chunks:
            try:
                obj = json.loads(body)
                b64 = obj.get("audio_base64", "")
                if b64:
                    return base64.b64decode(b64)
            except Exception:
                pass
            return None
        return b''.join(audio_chunks)

    def test_connection(self, proxy_url: Optional[str] = None) -> Dict:
        """Test connectivity to ElevenLabs API."""
        proxy = proxy_url or self._proxy
        transport = None
        if proxy:
            transport = httpx.HTTPTransport(proxy=proxy)
        try:
            start = time.time()
            with httpx.Client(timeout=10, transport=transport, verify=False) as client:
                resp = client.get(f"{cfg.ELEVENLABS_API_BASE}/v1/voices")
                latency = (time.time() - start) * 1000
                return {"ok": resp.status_code == 200, "latency_ms": round(latency), "status": resp.status_code}
        except Exception as e:
            return {"ok": False, "latency_ms": 0, "error": str(e)}
