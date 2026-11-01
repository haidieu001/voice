"""
audio_utils.py — FFmpeg wrapper for audio merging, conversion, and normalization.
"""

import os
import subprocess
import shutil
import tempfile
import base64
import logging
from typing import List, Optional

log = logging.getLogger(__name__)


def find_ffmpeg() -> Optional[str]:
    """Find ffmpeg binary in PATH or common locations."""
    # Check PATH first
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    # Check common Windows locations
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffmpeg", "ffmpeg.exe"),
    ]
    for p in common_paths:
        if os.path.isfile(p):
            return p

    return None


def decode_audio_base64(b64_data: str) -> bytes:
    """Decode base64-encoded audio data."""
    return base64.b64decode(b64_data)


def merge_audio_chunks(chunks: List[bytes], output_path: str,
                       output_format: str = "mp3") -> bool:
    """Merge multiple audio byte chunks into a single output file.

    Args:
        chunks: List of raw audio bytes (MP3 segments).
        output_path: Path for the merged output file.
        output_format: Target format (mp3, wav, ogg, flac).

    Returns:
        True on success, False on failure.
    """
    if not chunks:
        log.error("No audio chunks to merge")
        return False

    ffmpeg = find_ffmpeg()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if ffmpeg is None:
        # Fallback: simple concatenation for MP3 (works for same-format chunks)
        log.warning("FFmpeg not found, using raw concatenation")
        try:
            with open(output_path, 'wb') as out:
                for chunk in chunks:
                    out.write(chunk)
            return True
        except IOError as e:
            log.error("Raw concatenation failed: %s", e)
            return False

    # Use FFmpeg concat demuxer
    tmpdir = tempfile.mkdtemp(prefix="elvp_")
    try:
        # Write chunks to temp files
        chunk_files = []
        for i, chunk in enumerate(chunks):
            tmp_path = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")
            with open(tmp_path, 'wb') as f:
                f.write(chunk)
            chunk_files.append(tmp_path)

        # Create concat list file
        list_path = os.path.join(tmpdir, "concat_list.txt")
        with open(list_path, 'w', encoding='utf-8') as f:
            for cf in chunk_files:
                safe = cf.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        # Run FFmpeg
        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
        ]

        if output_format != "mp3":
            # Re-encode to target format
            cmd.extend(["-c:a", _get_codec(output_format)])
        else:
            cmd.extend(["-c", "copy"])

        cmd.append(output_path)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            log.error("FFmpeg merge failed: %s", result.stderr)
            return False
        return True

    except Exception as e:
        log.error("Merge failed: %s", e)
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def convert_audio(input_path: str, output_path: str,
                  output_format: str = "wav") -> bool:
    """Convert audio file to a different format using FFmpeg."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        log.error("FFmpeg not found — cannot convert audio")
        return False

    try:
        cmd = [
            ffmpeg, "-y",
            "-i", input_path,
            "-c:a", _get_codec(output_format),
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception as e:
        log.error("Convert failed: %s", e)
        return False


def normalize_audio(input_path: str, output_path: str,
                    target_lufs: float = -16.0) -> bool:
    """Normalize audio loudness using FFmpeg loudnorm filter."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False

    try:
        cmd = [
            ffmpeg, "-y",
            "-i", input_path,
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception as e:
        log.error("Normalize failed: %s", e)
        return False


def get_audio_duration(filepath: str) -> float:
    """Get audio duration in seconds using FFprobe."""
    ffprobe = find_ffmpeg()
    if ffprobe:
        ffprobe = ffprobe.replace("ffmpeg", "ffprobe")

    if not ffprobe or not os.path.isfile(ffprobe):
        return 0.0

    try:
        cmd = [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _get_codec(fmt: str) -> str:
    """Map format name to FFmpeg codec."""
    codecs = {
        "mp3": "libmp3lame",
        "wav": "pcm_s16le",
        "ogg": "libvorbis",
        "flac": "flac",
        "aac": "aac",
        "opus": "libopus",
    }
    return codecs.get(fmt, "libmp3lame")
