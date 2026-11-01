"""
generate_full_sontran.py — Generate complete voiceover for Tuc Ngu Ca Dao using Son Tran ST3G
"""
import asyncio
import sys
import os
import json
import time

sys.path.insert(0, r"D:\2026\VOICE")
from playwright.async_api import async_playwright
from core.hsw_pool import HswPool
from test_anon_tts import call_tts_with_tls_client

CHUNKS = [
    # Chunk 1: Hook
    "Người xưa từng dạy: Trong đối nhân xử thế, có ba kiểu người bạn tuyệt đối không nên kết giao, và có những người một khi gặp được, nhất định phải trân quý cả đời.",
    # Chunk 2: Kiểu người 1
    "Kiểu người thứ nhất: Là những kẻ nói năng ngọt ngào, khen ngợi bạn hết lời, nhưng sau lưng lại sẵn sàng đặt điều trục lợi. Các cụ gọi đó là: Mật ngọt chết ruồi, Khẩu phật tâm xà. Lời nói quá hoa mỹ thường ẩn giấu toan tính khó lường.",
    # Chunk 3: Kiểu người 2
    "Kiểu người thứ hai: Là những người biết im lặng và nhẫn nhịn. Sự im lặng của họ không phải là yếu đuối, mà là sự tự chủ và tôn trọng nguyên tắc. Người xưa đúc kết: Lúa chín cúi đầu, sông sâu tĩnh lặng. Người càng có bản lĩnh thì tâm thế càng bình thản, nhưng một khi chạm vào giới hạn, họ sẽ dứt khoát rời đi không bao giờ quay lại.",
    # Chunk 4: Kiểu người 3
    "Kiểu người thứ ba: Là người biết lắng nghe và thấu hiểu. Giữa cuộc đời vội vã, gặp được người lắng nghe bạn bằng chân tâm, cùng sẻ chia hoạn nạn, đó là quý nhân lớn nhất đời.",
    # Chunk 5: Cội nguồn ca dao
    "Tất cả những bài học về nhìn thấu lòng người và đạo lý làm người ấy, thực ra không ở đâu xa, mà đúc kết qua ngàn năm trong kho tàng ca dao tục ngữ dân tộc.",
    # Chunk 6: Giới thiệu sách & CTA
    "Nếu bạn muốn một cuốn cẩm nang gối đầu giường để đọc vị tâm lý và ứng xử, thì công trình đồ sộ của học giả Vũ Ngọc Phan: Tục ngữ, ca dao, dân ca Việt Nam chính là tuyệt tác. Cuốn sách hơn một ngàn trang này tập hợp trọn vẹn trí tuệ ngàn năm của cha ông. Em để ấn bản bìa cứng ở giỏ hàng góc trái cho các bác cùng tìm đọc nhé!"
]

VOICE_ID = "JxmKvRaNYFidf0N27Vng" # Son Tran ST3G

async def main():
    print(f"Starting full voice generation with Son Tran ST3G ({len(CHUNKS)} chunks)...")
    pw = await async_playwright().start()
    pool = HswPool(headless=True, num_contexts=1)
    await pool.start(pw)

    audio_parts = []
    
    for idx, text in enumerate(CHUNKS):
        print(f"\n--- Chunk {idx + 1}/{len(CHUNKS)} ({len(text)} chars) ---")
        t0 = time.time()
        token, reason = await pool.solve()
        if not token:
            print(f"Failed to get captcha token for chunk {idx + 1}, retrying...")
            await asyncio.sleep(2)
            token, reason = await pool.solve()
        
        if not token:
            print(f"Error: could not solve captcha for chunk {idx + 1}")
            break
            
        print(f"Captcha solved in {time.time() - t0:.1f}s. Calling TTS...")
        audio = call_tts_with_tls_client(token, text, VOICE_ID)
        if audio and len(audio) > 100:
            part_path = f"output/sontran_part_{idx + 1}.mp3"
            with open(part_path, "wb") as f:
                f.write(audio)
            audio_parts.append(part_path)
            print(f"Chunk {idx + 1} succeeded: {len(audio)} bytes -> {part_path}")
        else:
            print(f"Chunk {idx + 1} TTS failed!")
            
        await asyncio.sleep(2)

    await pool.close()
    await pw.stop()

    if len(audio_parts) == len(CHUNKS):
        print("\nAll chunks generated successfully! Merging audio files...")
        # Merge using ffmpeg concat
        concat_txt = "output/concat_list.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for p in audio_parts:
                f.write(f"file '{os.path.abspath(p)}'\n")
        
        merged_output = "output/sontran_full_tucngu.mp3"
        os.system(f"ffmpeg -y -f concat -safe 0 -i {concat_txt} -c copy {merged_output}")
        print(f"Merged voiceover saved to: {merged_output}")
    else:
        print(f"Only generated {len(audio_parts)}/{len(CHUNKS)} chunks.")

if __name__ == "__main__":
    asyncio.run(main())
