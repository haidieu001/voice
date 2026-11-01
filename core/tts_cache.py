"""Cache ket qua TTS de khong tra tien lai cho doan van khong doi.

Thuc te ton credit nhat khong phai lan tao dau tien, ma la sua mot chu roi
tao lai ca bai: moi chunk khong lien quan cung bi doc lai va tinh tien lai.
Cache theo tung chunk nen chi doan nao that su thay doi moi phai goi API.

Khoa cache = bam cua (nha cung cap, voice_id, model, ngon ngu, tham so giong,
noi dung chunk). Doi bat ky thu gi trong do la ra khoa khac, nen khong bao gio
tra ve am thanh sai.
"""

import hashlib
import json
import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_BASE, "output", ".tts_cache")

# Chi cac tham so THUC SU doi am thanh moi duoc dua vao khoa. Them tham so
# thua vao day se lam cache truot oan.
_KEY_FIELDS = (
    "provider", "voice_id", "model_id", "language_code",
    "speed", "pitch", "vol", "stability", "similarity_boost",
    "style", "use_speaker_boost",
)

_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0, "saved_chars": 0}

enabled = True


def _norm(value) -> str:
    """Chuan hoa gia tri de 1 va 1.0 khong tao ra hai khoa khac nhau."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return "%.4f" % float(value)
    return str(value).strip()


def make_key(text: str, params: Dict) -> str:
    parts = [_norm(params.get(f)) for f in _KEY_FIELDS]
    parts.append(text or "")
    raw = "\x1f".join(parts).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()


def _path(key: str, ext: str = "mp3") -> str:
    # Chia thu muc con theo 2 ky tu dau: tranh mot thu muc hang chuc nghin file.
    return os.path.join(CACHE_DIR, key[:2], "%s.%s" % (key, ext))


def get(text: str, params: Dict) -> Optional[bytes]:
    """Tra ve audio da cache, hoac None."""
    if not enabled:
        return None
    key = make_key(text, params)
    path = _path(key)
    try:
        if not os.path.isfile(path):
            with _lock:
                _stats["misses"] += 1
            return None
        with open(path, "rb") as f:
            data = f.read()
        if not data:
            return None
        os.utime(path, None)   # danh dau vua dung, phuc vu don dep theo tuoi
        with _lock:
            _stats["hits"] += 1
            _stats["saved_chars"] += len(text or "")
        log.info("[Cache] Dung lai %d byte cho %d ky tu (khong goi API)",
                 len(data), len(text or ""))
        return data
    except Exception as e:
        log.warning("[Cache] Doc loi: %s", str(e)[:90])
        return None


def put(text: str, params: Dict, audio: bytes) -> None:
    """Luu audio vua tao."""
    if not enabled or not audio:
        return
    key = make_key(text, params)
    path = _path(key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(audio)
        os.replace(tmp, path)   # ghi nguyen tu: khong de lai file cut nua chung
    except Exception as e:
        log.warning("[Cache] Ghi loi: %s", str(e)[:90])


def stats() -> Dict:
    with _lock:
        s = dict(_stats)
    files, size = 0, 0
    try:
        for root, _dirs, names in os.walk(CACHE_DIR):
            for n in names:
                if n.endswith(".mp3"):
                    files += 1
                    try:
                        size += os.path.getsize(os.path.join(root, n))
                    except OSError:
                        pass
    except Exception:
        pass
    total = s["hits"] + s["misses"]
    s.update({
        "enabled": enabled,
        "files": files,
        "size_mb": round(size / 1048576.0, 2),
        "hit_rate": round(100.0 * s["hits"] / total, 1) if total else 0.0,
        "dir": CACHE_DIR,
    })
    return s


def clear(older_than_days: float = 0) -> Tuple[int, int]:
    """Xoa cache. ``older_than_days=0`` la xoa het. Tra ve (so file, so byte)."""
    removed, freed = 0, 0
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    try:
        for root, _dirs, names in os.walk(CACHE_DIR):
            for n in names:
                p = os.path.join(root, n)
                try:
                    if cutoff and os.path.getmtime(p) > cutoff:
                        continue
                    sz = os.path.getsize(p)
                    os.remove(p)
                    removed += 1
                    freed += sz
                except OSError:
                    continue
    except Exception as e:
        log.warning("[Cache] Xoa loi: %s", str(e)[:90])
    log.info("[Cache] Da xoa %d file (%.1f MB)", removed, freed / 1048576.0)
    return removed, freed
