import asyncio
import sys
import os
import time

sys.path.insert(0, r"D:\2026\VOICE")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from playwright.async_api import async_playwright
from core.hsw_pool import HswPool
from test_anon_tts import call_tts_with_tls_client

TEST_DIR = r"C:\Users\USA\Downloads\vox\voice_samples_elevenlabs"
os.makedirs(TEST_DIR, exist_ok=True)

SAMPLE_TEXT = (
    "Đi làm sợ nhất không phải là việc nhiều, mà là thật thà quá hóa ra thành kẻ ngốc để người khác dẫm lên! "
    "Bạn có nhận ra: Càng biết điều, người ta lại càng xem sự hy sinh của bạn là điều hiển nhiên? "
    "Người khôn ngoan nắm chắc ba quy tắc thép: Mềm mỏng ở nụ cười nhưng thép ở ranh giới."
)

VOICES = [
    ("EL_06_MyDuyen_MienNam.mp3", "DVQIYWzpAqd5qcoIlirg", "My Duyen (Nu am ap mien Nam)"),
    ("EL_07_Ngan.mp3", "a3AkyqGG4v8Pg7SWQ0Y3", "Ngan (Nu tre de thuong)"),
    ("EL_08_AnhDuc.mp3", "jBpfAFnYqbzQVoiCAGJB", "Anh Duc (Nam tre nang dong)"),
    ("EL_09_River.mp3", "SAz9YHcvj6GT2YYXdXww", "River (Nu binh tinh thong tin)"),
]

async def main():
    print("Khởi động HswPool round 2...")
    pw = await async_playwright().start()
    pool = HswPool(headless=True, num_contexts=1)
    await pool.start(pw)

    for filename, voice_id, voice_name in VOICES:
        print(f"=== Đang xử lý: {voice_name} (ID: {voice_id}) ===")
        t0 = time.time()
        token, reason = await pool.solve()
        if not token:
            await asyncio.sleep(2)
            token, reason = await pool.solve()
            
        if not token:
            print(f"Bỏ qua {voice_name}.\n")
            continue
            
        audio = call_tts_with_tls_client(token, SAMPLE_TEXT, voice_id)
        if audio and len(audio) > 500:
            out_path = os.path.join(TEST_DIR, filename)
            with open(out_path, "wb") as f:
                f.write(audio)
            print(f"✓ THÀNH CÔNG: {voice_name} -> {filename} ({len(audio)} bytes)\n")
        else:
            print(f"✗ Thất bại cho {voice_name}\n")
            
        await asyncio.sleep(2)

    await pool.close()
    await pw.stop()
    print("Hoàn tất round 2!")

if __name__ == "__main__":
    asyncio.run(main())
