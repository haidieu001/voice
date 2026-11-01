"""
gen_full_800_voice.py — Generate complete audio for "800 Cách Đối Nhân Xử Thế" via ElevenLabs Anonymous TTS
"""
import asyncio
import sys
import os
import time
import argparse

sys.path.insert(0, r"D:\2026\VOICE")
from playwright.async_api import async_playwright
from core.hsw_pool import HswPool
from test_anon_tts import call_tts_with_tls_client

CHUNKS_800 = [
    # Chunk 1: Hook
    "Đi làm sợ nhất không phải là việc nhiều, mà là thật thà quá hóa ra thành kẻ ngốc để người khác dẫm lên! Bạn có nhận ra: Càng biết điều, người ta lại càng xem sự hy sinh của bạn là điều hiển nhiên?",
    # Chunk 2: Nỗi đau văn phòng & quan hệ
    "Ai nhờ gì cũng cặm cụi gật đầu làm hộ, để rồi làm sai thì gánh tội, làm đúng chẳng ai cảm ơn. Bị nói móc thì tức tối cãi tay đôi, bị mượn tiền thì ngại ngùng cho vay để rồi mất cả tiền lẫn bạn!",
    # Chunk 3: 3 Quy tắc thép
    "Người khôn ngoan không bao giờ sống bằng sự cả nể. Họ nắm chắc ba quy tắc thép. Thứ nhất: Mềm mỏng ở nụ cười nhưng thép ở ranh giới. Thứ hai: Im lặng đúng lúc để nhìn thấu bụng dạ đối phương. Và thứ ba: Chỉ cần một câu hỏi ngược tinh tế là khiến kẻ vô duyên tự thấy xấu hổ!",
    # Chunk 4: Giới thiệu 800 tình huống
    "Không có ai sinh ra đã khéo ăn khéo nói, tất cả là do rèn luyện. Tám trăm tình huống ứng biến thực chiến – từ cách từ chối không mất lòng, cách ứng xử với sếp và đồng nghiệp, đến nghệ thuật làm chủ mọi cuộc đối thoại – đều được gom trọn trong cuốn sách này!",
    # Chunk 5: Triết lý & CTA giỏ hàng
    "Học cách đối nhân xử thế không phải để xảo quyệt hơn người, mà là để không ai có thể làm tổn thương bạn. Ấn bản chuẩn xịn em ghim ở giỏ hàng góc trái màn hình, rinh ngay hôm nay để nhận trợ giá tốt nhất nhé!"
]

VOICE_MAP = {
    "sontran": ("JxmKvRaNYFidf0N27Vng", "Sơn Trần ST3G (Nam đọc sách/audiobook truyền cảm)"),
    "minhtrung": ("FTYCiQT21H9XQvhRu0ch", "Minh Trung (Nam trầm ấm, chia sẻ triết lý)"),
    "phuong": ("deC6NEXcbavaVWbzjgzb", "Phương (Nữ mượt mà, sâu lắng)"),
    "charlotte": ("XB0fDUnXU5powFXDhCwa", "Charlotte (Nữ audiobook đĩnh đạc)"),
}

async def generate(voice_key="sontran", output_file=r"D:\2026\CENTER\vox-remotion\public\voice_800_cach_full.mp3"):
    voice_id, voice_name = VOICE_MAP.get(voice_key.lower(), VOICE_MAP["sontran"])
    print(f"\n==================================================")
    print(f"BẮT ĐẦU TẠO VOICE ELEVENLABS CHO SÁCH 800 CÁCH")
    print(f"Giọng chọn: {voice_name}")
    print(f"Voice ID: {voice_id}")
    print(f"Số chunks: {len(CHUNKS_800)}")
    print(f"==================================================\n")

    os.makedirs(r"D:\2026\VOICE\output", exist_ok=True)
    temp_dir = r"D:\2026\VOICE\output\sach800_temp"
    os.makedirs(temp_dir, exist_ok=True)

    pw = await async_playwright().start()
    pool = HswPool(headless=True, num_contexts=1)
    await pool.start(pw)

    audio_parts = []

    for idx, text in enumerate(CHUNKS_800):
        print(f"\n--- [Chunk {idx + 1}/{len(CHUNKS_800)}] ({len(text)} ký tự) ---")
        t0 = time.time()
        token, reason = await pool.solve()
        if not token:
            print(f"Thử lại giải captcha lần 2 cho chunk {idx + 1}...")
            await asyncio.sleep(2)
            token, reason = await pool.solve()

        if not token:
            print(f"Lỗi: Không giải được captcha cho chunk {idx + 1}")
            break

        print(f"Đã giải captcha trong {time.time() - t0:.1f}s. Đang gọi ElevenLabs...")
        audio = call_tts_with_tls_client(token, text, voice_id)
        if audio and len(audio) > 1000:
            part_path = os.path.join(temp_dir, f"part_{idx + 1}.mp3")
            with open(part_path, "wb") as f:
                f.write(audio)
            audio_parts.append(part_path)
            print(f"✓ Chunk {idx + 1} thành công: {len(audio)} bytes -> {part_path}")
        else:
            print(f"✗ Chunk {idx + 1} lỗi TTS!")
            break

        await asyncio.sleep(3)

    await pool.close()
    await pw.stop()

    if len(audio_parts) == len(CHUNKS_800):
        print(f"\nĐã hoàn thành sinh {len(audio_parts)} parts! Đang ghép audio...")
        concat_txt = os.path.join(temp_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for p in audio_parts:
                f.write(f"file '{p}'\n")

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        # Sử dụng ffmpeg concat filter với re-encode aac/mp3 để âm thanh nối mượt mà không khựng
        cmd = f'ffmpeg -y -f concat -safe 0 -i "{concat_txt}" -c:a libmp3lame -b:a 192k "{output_file}"'
        res = os.system(cmd)
        if res == 0 and os.path.exists(output_file):
            print(f"\n==================================================")
            print(f"✓ THÀNH CÔNG: Đã ghép trọn vẹn audio master!")
            print(f"File đích: {output_file}")
            print(f"==================================================")
            return output_file
        else:
            print(f"Lỗi khi ghép audio bằng ffmpeg.")
            return None
    else:
        print(f"Chỉ sinh được {len(audio_parts)}/{len(CHUNKS_800)} chunks.")
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default="sontran", choices=["sontran", "minhtrung", "phuong", "charlotte"])
    parser.add_argument("--out", default=r"D:\2026\CENTER\vox-remotion\public\voice_800_cach_full.mp3")
    args = parser.parse_args()

    asyncio.run(generate(args.voice, args.out))
