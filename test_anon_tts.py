"""
test_anon_tts.py — Test thử toàn bộ luồng anonymous ElevenLabs TTS.

Bước 1: Khởi động HswPool headless → giải captcha lấy token P1_...
Bước 2: Dùng token gọi TTS anonymous endpoint → lấy audio MP3
Bước 3: Lưu file MP3 ra output/

Chạy:  python test_anon_tts.py
"""

import asyncio
import json
import base64
import time
import os
import sys
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("test_anon_tts")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import engine_config as cfg


def call_tts_with_tls_client(token: str, text: str, voice_id: str) -> bytes:
    """Gọi TTS anonymous endpoint bằng tls_client (giống v4.1)."""
    import tls_client

    session = tls_client.Session(
        client_identifier="firefox_120",
        random_tls_extension_order=True,
    )

    endpoint = cfg.ELEVENLABS_ANON_ENDPOINT.format(voice_id=voice_id)
    url = f"{cfg.ELEVENLABS_API_BASE}{endpoint}"

    payload = {
        "text": text,
        "model_id": cfg.DEFAULT_MODEL_ID,
        "language_code": cfg.DEFAULT_LANGUAGE,
        "voice_settings": {
            "stability": 0.75,
            "similarity_boost": 0.80,
            "style": 0.30,
            "use_speaker_boost": True,
            "speed": 1.0,
        },
        "hcaptcha_token": token,
    }

    headers = dict(cfg.ELEVENLABS_HEADERS)
    headers["User-Agent"] = cfg.USER_AGENTS[1]  # Firefox 120

    log.info("[TTS] POST %s", url)
    log.info("[TTS] token_len=%d, text_len=%d", len(token), len(text))

    resp = session.post(
        url,
        json=payload,
        headers=headers,
        timeout_seconds=60,
    )

    log.info("[TTS] HTTP %d, content_len=%d", resp.status_code, len(resp.text or ""))

    if resp.status_code == 401:
        log.error("[TTS] 401 Unauthorized — token hết hạn hoặc IP bị chặn")
        log.error("[TTS] Body: %s", resp.text[:300])
        return b""

    if resp.status_code == 429:
        log.error("[TTS] 429 Rate limited")
        return b""

    if resp.status_code != 200:
        log.error("[TTS] HTTP %d: %s", resp.status_code, resp.text[:300])
        return b""

    # Parse JSON streaming response
    audio_chunks = []
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            b64 = obj.get("audio_base64", "")
            if b64:
                audio_chunks.append(base64.b64decode(b64))
        except (json.JSONDecodeError, Exception) as e:
            # Có thể là raw audio
            pass

    if audio_chunks:
        audio_data = b"".join(audio_chunks)
        log.info("[TTS] Parsed %d JSON chunks → %d bytes audio", len(audio_chunks), len(audio_data))
        return audio_data

    # Fallback: raw binary response
    raw = resp.content if hasattr(resp, 'content') else resp.text.encode('latin-1')
    if raw and len(raw) > 100:
        log.info("[TTS] Raw binary response: %d bytes", len(raw))
        return raw

    log.error("[TTS] Không có audio data trong response")
    return b""


async def test_solve_and_tts():
    """Test flow: HswPool solve → TTS."""
    from playwright.async_api import async_playwright
    from core.hsw_pool import HswPool

    log.info("=" * 60)
    log.info("TEST: Anonymous ElevenLabs TTS via HswPool")
    log.info("=" * 60)

    pw = await async_playwright().start()
    pool = HswPool(headless=True, num_contexts=1)

    try:
        log.info("[1/3] Khởi động HswPool (1 context, headless)...")
        await pool.start(pw)
        log.info("[1/3] HswPool sẵn sàng!")

        # Solve 3 lần để test
        for i in range(3):
            log.info("")
            log.info("=" * 50)
            log.info("  ROUND %d/3", i + 1)
            log.info("=" * 50)

            log.info("[2/3] Giải captcha...")
            t0 = time.time()
            token, reason = await pool.solve()
            elapsed = time.time() - t0

            if not token or reason != "ok":
                log.error("[2/3] THẤT BẠI! reason=%s (%.1fs)", reason, elapsed)
                if reason == "demoted":
                    log.error("       IP bị demote → cần đổi IP (bật VPN)")
                continue

            log.info("[2/3] TOKEN OK! len=%d, reason=%s (%.1fs)", len(token), reason, elapsed)
            log.info("[2/3] Token prefix: %s...", token[:30])

            # Gọi TTS
            text = f"Xin chào, đây là bài test số {i + 1}. Ứng dụng voice đang hoạt động tốt."
            voice_id = cfg.DEFAULT_VOICE_ID  # Rachel

            log.info("[3/3] Gọi TTS anonymous...")
            t1 = time.time()
            audio = call_tts_with_tls_client(token, text, voice_id)
            tts_elapsed = time.time() - t1

            if audio and len(audio) > 100:
                os.makedirs("output", exist_ok=True)
                fname = f"output/test_round_{i + 1}_{int(time.time())}.mp3"
                with open(fname, "wb") as f:
                    f.write(audio)
                log.info("[3/3] THÀNH CÔNG! %d bytes → %s (%.1fs)", len(audio), fname, tts_elapsed)
            else:
                log.error("[3/3] TTS thất bại — không có audio (%.1fs)", tts_elapsed)

            # Chờ giữa các round
            if i < 2:
                log.info("Chờ 5s trước round tiếp...")
                await asyncio.sleep(5)

    except Exception as e:
        log.error("EXCEPTION: %s", e, exc_info=True)
    finally:
        log.info("Đóng HswPool...")
        await pool.close()
        await pw.stop()
        log.info("Done.")


if __name__ == "__main__":
    asyncio.run(test_solve_and_tts())
