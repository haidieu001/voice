"""
voice_search.py — Search and browse ElevenLabs voice library.
Uses /v1/voices endpoint (public, no auth required) with client-side filtering.
"""

import logging
import random
import threading
from typing import Dict, List, Optional

import httpx

from . import engine_config as cfg

log = logging.getLogger(__name__)

# Curated Vietnamese community voices (publicly shared, work with anonymous endpoint)
VIETNAMESE_VOICES = [
    {"voice_id": "JxmKvRaNYFidf0N27Vng", "name": "Son Tran ST3G", "gender": "male", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nam tre, doc sach, voiceover", "use_case": "narration", "category": "shared"},
    {"voice_id": "foH7s9fX31wFFH2yqrFa", "name": "Huyen", "gender": "female", "age": "middle aged", "accent": "vietnamese", "language": "vi", "description": "Giong nu binh tinh, than thien, da nang", "use_case": "narration", "category": "shared"},
    {"voice_id": "a3AkyqGG4v8Pg7SWQ0Y3", "name": "Ngan", "gender": "female", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nu tre, de thuong, vui tuoi", "use_case": "social media", "category": "shared"},
    {"voice_id": "deC6NEXcbavaVWbzjgzb", "name": "Phuong", "gender": "female", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nu muot ma, nhe nhang", "use_case": "narration", "category": "shared"},
    {"voice_id": "DVQIYWzpAqd5qcoIlirg", "name": "MyDuyen", "gender": "female", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nu sang, ro rang, am ap mien Nam", "use_case": "narration", "category": "shared"},
    {"voice_id": "FTYCiQT21H9XQvhRu0ch", "name": "MinhTrung", "gender": "male", "age": "middle aged", "accent": "vietnamese", "language": "vi", "description": "Giong nam truyen cam, doc tin tuc", "use_case": "news", "category": "shared"},
    {"voice_id": "pFZP5JQG7iQjIQuC4Bku", "name": "Lily - Vietnamese", "gender": "female", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nu chuyen nghiep, phat am chuan", "use_case": "narration", "category": "shared"},
    {"voice_id": "jBpfAFnYqbzQVoiCAGJB", "name": "Anh Duc", "gender": "male", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nam tre, nang dong", "use_case": "social media", "category": "shared"},
    {"voice_id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte - Vietnamese", "gender": "female", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nu trang trong, doc audiobook", "use_case": "audiobook", "category": "shared"},
    {"voice_id": "SAz9YHcvj6GT2YYXdXww", "name": "River - Vietnamese", "gender": "female", "age": "young", "accent": "vietnamese", "language": "vi", "description": "Giong nu binh tinh, thong tin", "use_case": "informative", "category": "shared"},
]


# Voice Library: ElevenLabs chi tra ve toi da 3 giong va cam moi bo loc neu
# request khong kem thong tin dang nhap. Co API key cua chinh nguoi dung thi
# moi lay duoc ca thu vien.
SHARED_VOICES_PAGE_SIZE = 100
SHARED_VOICES_FIRST_PAGES = 3    # nap nhanh luc khoi dong, phan con lai chay nen
SHARED_VOICES_MAX = 20000        # tran an toan


class VoiceSearcher:
    """Search ElevenLabs public voice library."""

    def __init__(self):
        self._cache: Dict[str, Dict] = {}
        self._all_voices: List[Dict] = []
        self._loaded = False
        self._api_key = ""
        self._library_loaded = False
        self._library_complete = False
        self._library_error = "no_api_key"
        self._library_total = 0
        self._next_page = 0
        self._lock = threading.Lock()

    # ── API key ────────────────────────────────────────────────

    def set_api_key(self, key: str) -> None:
        """Gan API key cua nguoi dung. Doi key thi nap lai toan bo danh sach."""
        key = (key or "").strip()
        if key == self._api_key:
            return
        self._api_key = key
        self._loaded = False
        self._library_loaded = False
        self._library_complete = False
        self._next_page = 0
        self._library_error = "" if key else "no_api_key"
        self._all_voices.clear()
        self._cache.clear()
        log.info("[VoiceLib] API key %s - se nap lai danh sach giong",
                 "da dat" if key else "da xoa")

    def library_status(self) -> Dict:
        return {
            "loaded": self._library_loaded,
            "complete": self._library_complete,
            "has_api_key": bool(self._api_key),
            "error": self._library_error,
            "count": self._library_total,
            "cached": len(self._all_voices),
        }

    def _headers(self, with_key: bool = True) -> Dict:
        h = {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://elevenlabs.io",
            "Referer": "https://elevenlabs.io/",
            "User-Agent": random.choice(cfg.USER_AGENTS),
        }
        if with_key and self._api_key:
            h["xi-api-key"] = self._api_key
        return h

    def _add_voices(self, raw_list, src: str = "library") -> List[Dict]:
        """Chuan hoa va them vao danh sach. Tra ve cac giong cua lo nay."""
        out = []
        with self._lock:
            for v in raw_list or []:
                voice = self._normalize_voice(v)
                vid = voice.get("voice_id")
                if not vid:
                    continue
                if vid in self._cache:
                    out.append(self._cache[vid])
                    continue
                voice["src"] = src
                self._all_voices.append(voice)
                self._cache[vid] = voice
                out.append(voice)
        return out

    def _local_special(self) -> List[Dict]:
        """Giong premade, giong cua tai khoan va giong Viet tuyen chon.

        Nhung giong nay khong nam trong Voice Library nen phai ghep tay vao
        dau trang dau, khong thi duyet thang tu server se lam mat chung.
        """
        with self._lock:
            return [v for v in self._all_voices
                    if v.get("src") in ("premade", "account", "curated")]

    def _fetch_my_voices(self) -> int:
        """Giong cua chinh tai khoan, ke ca giong clone. Bat buoc co API key."""
        if not self._api_key:
            return 0
        try:
            with httpx.Client(timeout=20, verify=False) as client:
                resp = client.get(
                    f"{cfg.ELEVENLABS_API_BASE}{cfg.ELEVENLABS_VOICES_V2}",
                    params={"page_size": 100},
                    headers=self._headers(),
                )
            if resp.status_code != 200:
                log.warning("[VoiceLib] /v2/voices tra ve %d", resp.status_code)
                return 0
            n = len(self._add_voices(resp.json().get("voices", []), src="account"))
            if n:
                log.info("[VoiceLib] %d giong cua tai khoan", n)
            return n
        except Exception as e:
            log.warning("[VoiceLib] /v2/voices loi: %s", str(e)[:90])
            return 0

    def _fetch_shared(self, params: Dict):
        """Goi /v1/shared-voices. Tra ve (voices, has_more, total, loi)."""
        try:
            with httpx.Client(timeout=25, verify=False) as client:
                resp = client.get(
                    f"{cfg.ELEVENLABS_API_BASE}{cfg.ELEVENLABS_SHARED_VOICES}",
                    params=params,
                    headers=self._headers(),
                )
            if resp.status_code == 401:
                return [], False, 0, "unauthorized"
            if resp.status_code != 200:
                return [], False, 0, "http_%d" % resp.status_code
            data = resp.json()
            return (data.get("voices", []), bool(data.get("has_more")),
                    int(data.get("total_count") or 0), "")
        except Exception as e:
            return [], False, 0, str(e)[:90]

    def _fetch_library(self, max_pages: Optional[int] = None) -> int:
        """Lat tung trang Voice Library, tiep tuc tu trang con dang do."""
        if not self._api_key:
            self._library_error = "no_api_key"
            return 0

        added = 0
        pages = 0
        while len(self._all_voices) < SHARED_VOICES_MAX:
            if max_pages is not None and pages >= max_pages:
                break
            voices, has_more, total, err = self._fetch_shared(
                {"page_size": SHARED_VOICES_PAGE_SIZE, "page": self._next_page}
            )
            if err:
                self._library_error = err
                log.warning("[VoiceLib] dung o trang %d: %s", self._next_page, err)
                break
            if not voices:
                self._library_complete = True
                break
            added += len(self._add_voices(voices))
            if total:
                self._library_total = total
            self._next_page += 1
            pages += 1
            if not has_more:
                self._library_complete = True
                break

        if added:
            self._library_error = ""
            self._library_loaded = True
            log.info("[VoiceLib] +%d giong (%d trang, dang giu %d/%s%s)",
                     added, pages, len(self._all_voices),
                     self._library_total or "?",
                     ", da het" if self._library_complete else "")
        return added

    def _ensure_loaded(self):
        """Load all voices once from the public endpoint + curated Vietnamese voices."""
        if self._loaded and self._all_voices:
            return
        url = f"{cfg.ELEVENLABS_API_BASE}/v1/voices"
        try:
            with httpx.Client(timeout=15, verify=False) as client:
                resp = client.get(url, headers=self._headers(with_key=False))
                resp.raise_for_status()
                data = resp.json()

            self._all_voices = []
            premade = len(self._add_voices(data.get("voices", []), src="premade"))

            # Co API key thi lay them giong cua tai khoan va ca Voice Library.
            # Khong co key, ElevenLabs chan o 3 giong nen bo qua.
            own = self._fetch_my_voices()
            # Chi lay vai trang dau lam von; viec duyet va tim deu hoi thang
            # server nen khong can keo ve du 16k giong.
            lib = self._fetch_library(max_pages=SHARED_VOICES_FIRST_PAGES)
            if not self._api_key:
                log.info("[VoiceLib] Chua co API key - chi co %d giong premade. "
                         "Nhap API key de lay toan bo Voice Library.", premade)

            # Add curated Vietnamese voices (skip duplicates)
            existing_ids = {v["voice_id"] for v in self._all_voices}
            vi_count = 0
            for vi in VIETNAMESE_VOICES:
                if vi["voice_id"] not in existing_ids:
                    voice = {
                        "voice_id": vi["voice_id"],
                        "name": vi["name"],
                        "category": vi.get("category", "shared"),
                        "description": vi.get("description", ""),
                        "preview_url": "",
                        "gender": vi.get("gender", ""),
                        "age": vi.get("age", ""),
                        "accent": vi.get("accent", "vietnamese"),
                        "language": vi.get("language", "vi"),
                        "use_case": vi.get("use_case", ""),
                        "sample_rate": [],
                        "cloned": False,
                        "src": "curated",
                    }
                    self._all_voices.append(voice)
                    self._cache[voice["voice_id"]] = voice
                    vi_count += 1

            self._loaded = True
            log.info("Loaded %d voices (%d premade + %d tai khoan + %d thu vien + %d Vietnamese)",
                     len(self._all_voices), premade, own, lib, vi_count)
        except Exception as e:
            log.error("Failed to load voices: %s", e)
            
        if not self._loaded and not self._all_voices:
            # Fallback to curated voices if API failed
            for vi in VIETNAMESE_VOICES:
                voice = {
                    "voice_id": vi["voice_id"],
                    "name": vi["name"],
                    "category": vi.get("category", "shared"),
                    "description": vi.get("description", ""),
                    "preview_url": "",
                    "gender": vi.get("gender", ""),
                    "age": vi.get("age", ""),
                    "accent": vi.get("accent", "vietnamese"),
                    "language": vi.get("language", "vi"),
                    "use_case": vi.get("use_case", ""),
                    "sample_rate": [],
                    "cloned": False,
                    "src": "curated",
                }
                self._all_voices.append(voice)
                self._cache[voice["voice_id"]] = voice
            self._loaded = True
            log.info("Fallback: Loaded %d curated Vietnamese voices", len(self._all_voices))

    def search(self, query: str = "", language: str = "",
               gender: str = "", page: int = 0,
               page_size: int = 30) -> Dict:
        """Search voices with client-side filtering.

        Args:
            query: Search text (name, description, etc.)
            language: Language code filter (e.g., 'vi', 'en')
            gender: 'male' or 'female' or ''
            page: Page number (0-based)
            page_size: Results per page

        Returns:
            Dict with keys: voices (list), has_more (bool), total (int)
        """
        self._ensure_loaded()

        # Chup ban sao: luong nen van dang them giong vao danh sach.
        with self._lock:
            filtered = list(self._all_voices)
        if query:
            q = query.lower()
            filtered = [
                v for v in filtered
                if q in v["name"].lower()
                or q in v.get("description", "").lower()
                or q in v.get("accent", "").lower()
                or q in v.get("language", "").lower()
                or q in v.get("use_case", "").lower()
                or q in v.get("category", "").lower()
            ]
        if language:
            lang = language.lower()
            # Note: ElevenLabs v3 model supports ALL languages with any voice.
            # Premade voices are labeled 'en' but work perfectly with Vietnamese, etc.
            # Only filter if there are voices explicitly tagged with the language.
            lang_voices = [
                v for v in filtered
                if lang in v.get("language", "").lower()
            ]
            # If no voices match the language filter, show all (they all work via v3)
            if lang_voices:
                filtered = lang_voices
        if gender:
            g = gender.lower()
            filtered = [v for v in filtered if v.get("gender", "").lower() == g]

        # Co API key thi MOI luot duyet va tim deu hoi thang Voice Library.
        # Loc trong bo nho chi thay phan da tai nen se bao thieu (410 thay vi
        # 16.703) va khong lat duoc sang cac trang sau.
        if self._api_key and not self._library_complete:
            params = {"page_size": page_size, "page": page}
            if query:
                params["search"] = query
            if language:
                params["language"] = language
            if gender:
                params["gender"] = gender
            remote, has_more, total_count, err = self._fetch_shared(params)
            if remote:
                voices = self._add_voices(remote)
                extra = []
                if page == 0 and not (query or language or gender):
                    seen = {v["voice_id"] for v in voices}
                    extra = [v for v in self._local_special()
                             if v["voice_id"] not in seen]
                log.info("[VoiceLib] Server: %d ket qua (+%d cuc bo) / tong %s%s",
                         len(voices), len(extra), total_count or "?",
                         " cho %r" % (query or language or gender)
                         if (query or language or gender) else "")
                return {
                    "voices": extra + voices,
                    "has_more": has_more,
                    "total": (total_count or len(voices)) + len(extra),
                    "page": page,
                    "source": "library",
                    "library": self.library_status(),
                }
            if err:
                log.warning("[VoiceLib] Goi server loi: %s - dung ket qua cuc bo", err)

        total = len(filtered)
        start = page * page_size
        end = start + page_size
        page_voices = filtered[start:end]

        return {
            "voices": page_voices,
            "has_more": end < total,
            "total": total,
            "page": page,
            "source": "local",
            "library": self.library_status(),
        }

    def get_voice_detail(self, voice_id: str) -> Optional[Dict]:
        """Get detailed info about a specific voice."""
        self._ensure_loaded()
        return self._cache.get(voice_id)

    def get_preview_url(self, voice_id: str) -> Optional[str]:
        """Get the preview audio URL for a voice."""
        voice = self.get_voice_detail(voice_id)
        if voice:
            return voice.get("preview_url")
        return None

    def _normalize_voice(self, v: Dict) -> Dict:
        """Chuan hoa mot giong tu API.

        Hai endpoint tra ve hai dang khac nhau: /v1/voices va /v2/voices giau
        nhan trong ``labels``, con /v1/shared-voices de thang o cap ngoai. Doc
        ca hai, khong thi Voice Library se mat het gender/accent/use_case.
        """
        labels = v.get("labels") or {}

        def pick(key, *alts):
            for src in (labels, v):
                val = src.get(key)
                if val:
                    return val
            for alt in alts:
                val = labels.get(alt) or v.get(alt)
                if val:
                    return val
            return ""

        return {
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", "Unknown"),
            "category": v.get("category", ""),
            "description": v.get("description") or v.get("descriptive", ""),
            "preview_url": v.get("preview_url", ""),
            "gender": pick("gender"),
            "age": pick("age"),
            "accent": pick("accent"),
            "language": pick("language", "locale"),
            "use_case": pick("use_case", "descriptive"),
            "sample_rate": v.get("high_quality_base_model_ids", []),
            "cloned": v.get("category", "") == "cloned",
        }

    def is_valid_voice_id(self, voice_id: str) -> bool:
        """Check if a voice_id looks valid."""
        return bool(voice_id and len(voice_id) >= 10)

    def get_popular_voices(self, language: str = "vi") -> List[Dict]:
        """Get popular/featured voices for a language."""
        result = self.search(language=language, page_size=20)
        return result.get("voices", [])

    def reload(self):
        """Force reload voice list from API."""
        self._loaded = False
        self._all_voices.clear()
        self._cache.clear()
        self._ensure_loaded()
