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
    ("EL_01_SonTran_ST3G.mp3", "JxmKvRaNYFidf0N27Vng", "Son Tran ST3G (Nam doc sach)"),
    ("EL_02_MinhTrung.mp3", "FTYCiQT21H9XQvhRu0ch", "Minh Trung (Nam truyen cam)"),
    ("EL_03_Phuong.mp3", "deC6NEXcbavaVWbzjgzb", "Phuong (Nu muot ma nhe nhang)"),
    ("EL_04_Huyen.mp3", "foH7s9fX31wFFH2yqrFa", "Huyen (Nu binh tinh truyen cam)"),
    ("EL_05_Charlotte.mp3", "XB0fDUnXU5powFXDhCwa", "Charlotte (Nu doc audiobook)"),
]

async def main():
    print("Khởi động Playwright & HswPool...")
    pw = await async_playwright().start()
    pool = HswPool(headless=True, num_contexts=1)
    await pool.start(pw)
    print("HswPool sẵn sàng!\n")

    for filename, voice_id, voice_name in VOICES:
        print(f"=== Đang xử lý: {voice_name} (ID: {voice_id}) ===")
        t0 = time.time()
        token, reason = await pool.solve()
        if not token:
            print(f"Lỗi: Không lấy được token captcha cho {voice_name}, thử lại lần 2...")
            await asyncio.sleep(2)
            token, reason = await pool.solve()
            
        if not token:
            print(f"Bỏ qua {voice_name} do không giải được captcha.\n")
            continue
            
        print(f"Đã giải captcha trong {time.time() - t0:.1f}s. Đang gọi ElevenLabs TTS...")
        audio = call_tts_with_tls_client(token, SAMPLE_TEXT, voice_id)
        if audio and len(audio) > 500:
            out_path = os.path.join(TEST_DIR, filename)
            with open(out_path, "wb") as f:
                f.write(audio)
            print(f"✓ THÀNH CÔNG: {voice_name} -> {filename} ({len(audio)} bytes)\n")
        else:
            print(f"✗ Thất bại: Không nhận được audio cho {voice_name}\n")
            
        await asyncio.sleep(3)

    print("Đóng pool...")
    await pool.close()
    await pw.stop()
    print("Hoàn tất test ElevenLabs!")

if __name__ == "__main__":
    asyncio.run(main())
