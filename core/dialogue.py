"""Kich ban hoi thoai nhieu nhan vat: phan tich, gan giong, ghep audio.

Module nay khong tu goi TTS. No lo hai viec de kiem thu duoc doc lap:

1. Doc kich ban thanh danh sach luot noi (ai noi, noi gi).
2. Ghep cac doan audio da tao lai, chen khoang lang giua cac luot.

Phan goi TTS nam o ``app.py`` vi con phu thuoc phien MiniMax / captcha
ElevenLabs.
"""

import logging
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .audio_utils import find_ffmpeg

log = logging.getLogger(__name__)

# Khoang lang mac dinh (ms). Doi nguoi noi thi nghi lau hon mot chut, nghe
# tu nhien hon han so voi mot khoang cach co dinh.
GAP_SAME_SPEAKER_MS = 250
GAP_SPEAKER_CHANGE_MS = 550

# Nhan dang nhan nguoi noi. Ho tro cac kieu hay gap:
#   [Chu tieu] loi thoai        Chu tieu: loi thoai
#   - Chu tieu: loi thoai       A: loi thoai
_LABEL_BRACKET = re.compile(r"^\s*[\[\(]\s*([^\]\)]{1,40}?)\s*[\]\)]\s*(.*)$")
_LABEL_COLON = re.compile(r"^\s*[-–—•*]?\s*([^:：]{1,40}?)\s*[:：]\s*(.*)$")

# Cau bi hieu nham thanh nhan nguoi noi neu chi dua vao dau hai cham.
# Nhan that thuong ngan va khong chua dau cau cuoi cau.
_NOT_A_LABEL = re.compile(r"[.!?…;]|\bhttps?\b", re.I)


@dataclass
class Turn:
    speaker: str
    text: str

    def to_dict(self) -> Dict:
        return {"speaker": self.speaker, "text": self.text}


def normalize_name(name: str) -> str:
    """Bo dau, ha chu thuong, gom khoang trang — de khop ten nguoi noi.

    Nho vay "Chú tiểu", "CHU TIEU" va "chu  tieu" deu tro ve mot khoa.
    """
    s = unicodedata.normalize("NFD", str(name or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_script(text: str, known_speakers: Optional[List[str]] = None) -> List[Turn]:
    """Doc kich ban thanh cac luot noi.

    Dong khong co nhan duoc gop vao luot truoc do — nho vay loi thoai dai
    xuong dong van thuoc ve dung nguoi noi.

    ``known_speakers``: khi biet truoc danh sach nhan vat (UI da gan giong),
    nhan kieu ``Ten:`` chi duoc chap nhan neu khop danh sach do. Khong co no
    thi cau nhu "Thay noi: dong nuoc mem" bi hieu nham thanh nhan vat moi.
    Kieu ngoac ``[Ten]`` thi luon duoc chap nhan.
    """
    known = {normalize_name(x) for x in (known_speakers or []) if x}
    turns: List[Turn] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        speaker, content = None, line
        m = _LABEL_BRACKET.match(line)
        if m:
            speaker, content = m.group(1).strip(), m.group(2).strip()
        else:
            m = _LABEL_COLON.match(line)
            if m:
                candidate = m.group(1).strip()
                ok = (bool(candidate) and len(candidate) <= 40
                      and not _NOT_A_LABEL.search(candidate))
                if ok and known:
                    ok = normalize_name(candidate) in known
                if ok:
                    speaker, content = candidate, m.group(2).strip()

        if speaker:
            if content:
                turns.append(Turn(speaker=speaker, text=content))
            else:
                # Nhan dung mot minh: loi thoai nam o dong sau.
                turns.append(Turn(speaker=speaker, text=""))
        elif turns:
            turns[-1].text = (turns[-1].text + " " + line).strip()
        else:
            turns.append(Turn(speaker="", text=line))

    return [t for t in turns if t.text]


def list_speakers(turns: List[Turn]) -> List[str]:
    """Ten nguoi noi theo thu tu xuat hien lan dau."""
    seen, out = set(), []
    for t in turns:
        key = normalize_name(t.speaker)
        if key and key not in seen:
            seen.add(key)
            out.append(t.speaker)
    return out


def resolve_voice(speaker: str, mapping: Dict[str, Dict],
                  order: List[str]) -> Optional[Dict]:
    """Tim cau hinh giong cho mot nguoi noi.

    Khop khong phan biet hoa thuong va dau. Neu kich ban dung ten khac voi
    luc gan giong, roi ve gan theo thu tu xuat hien de van chay duoc.
    """
    norm = {normalize_name(k): v for k, v in (mapping or {}).items()}
    hit = norm.get(normalize_name(speaker))
    if hit:
        return hit
    if speaker in order:
        idx = order.index(speaker)
        values = list((mapping or {}).values())
        if idx < len(values):
            return values[idx]
    return None


# ── Ghep audio ──────────────────────────────────────────────────────────


def _silence_file(ffmpeg: str, ms: int, workdir: str) -> Optional[str]:
    path = os.path.join(workdir, "sil_%d.mp3" % ms)
    if os.path.isfile(path):
        return path
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i",
           "anullsrc=channel_layout=stereo:sample_rate=44100",
           "-t", "%.3f" % (ms / 1000.0), "-c:a", "libmp3lame", "-b:a", "192k", path]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
        return path
    except Exception as e:
        log.warning("[Dialogue] Tao khoang lang loi: %s", str(e)[:90])
        return None


def merge_turns(segments: List[Tuple[str, bytes]], output_path: str,
                gap_same_ms: int = GAP_SAME_SPEAKER_MS,
                gap_change_ms: int = GAP_SPEAKER_CHANGE_MS) -> bool:
    """Ghep cac doan audio theo thu tu, chen khoang lang giua cac luot.

    ``segments`` la danh sach ``(ten_nguoi_noi, audio_bytes)``.
    """
    if not segments:
        return False
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        log.error("[Dialogue] Khong tim thay ffmpeg")
        return False

    workdir = tempfile.mkdtemp(prefix="dlg_")
    try:
        entries: List[str] = []
        prev_speaker = None
        for i, (speaker, audio) in enumerate(segments):
            if i > 0:
                gap = gap_change_ms if normalize_name(speaker) != prev_speaker else gap_same_ms
                sil = _silence_file(ffmpeg, gap, workdir) if gap > 0 else None
                if sil:
                    entries.append(sil)
            seg_path = os.path.join(workdir, "seg_%04d.mp3" % i)
            with open(seg_path, "wb") as f:
                f.write(audio)
            entries.append(seg_path)
            prev_speaker = normalize_name(speaker)

        list_path = os.path.join(workdir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in entries:
                f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        # Ma hoa lai thay vi -c copy: doan tu MiniMax va khoang lang tu ffmpeg
        # co the khac thong so, copy thang se ra file loi nhip.
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
               "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", output_path]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            log.error("[Dialogue] ffmpeg loi: %s", r.stderr.decode("utf-8", "replace")[:300])
            return False
        log.info("[Dialogue] Da ghep %d luot -> %s", len(segments), output_path)
        return True
    finally:
        try:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass
