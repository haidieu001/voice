"""
engine_config.py - Default configuration constants for ElevenLabs Voice Pro.
All endpoints, model IDs, headers, and voice-setting defaults live here.
"""

# ─── ElevenLabs API ──────────────────────────────────────────────
ELEVENLABS_API_BASE = "https://api.elevenlabs.io"
ELEVENLABS_ANON_ENDPOINT = "/v1/text-to-speech/{voice_id}/stream/with-timestamps/anonymous"
ELEVENLABS_SHARED_VOICES = "/v1/shared-voices"
ELEVENLABS_VOICES_V2 = "/v2/voices"

# ─── hCaptcha ────────────────────────────────────────────────────
HCAPTCHA_API = "https://api2.hcaptcha.com"
HCAPTCHA_HOST = "elevenlabs.io"
HCAPTCHA_SITEKEY = "8e58fe8c-1a48-4f94-88ae-8e90b586a192"  # NEXT_PUBLIC_HCAPTCHA_SITEKEY_ANON_REQUEST
CAPTCHA_TIMEOUT = 120

# ─── Defaults ────────────────────────────────────────────────────
DEFAULT_MODEL_ID = "eleven_v3"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel
DEFAULT_LANGUAGE = "vi"
DEFAULT_SPEED = 1.0

VOICE_SETTINGS = {
    "stability": 0.75,
    "similarity_boost": 0.80,
    "style": 0.30,
    "use_speaker_boost": True,
    "speed": 1.0,
}

# ─── HTTP ────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

EL_ORIGIN = "https://elevenlabs.io"

# Headers chính xác từ v4.1 binary (WIRE format)
ELEVENLABS_HEADERS = {
    "Accept": "audio/mpeg",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Origin": "https://elevenlabs.io",
    "Referer": "https://elevenlabs.io/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Priority": "u=4",
    "TE": "trailers",
    "Connection": "keep-alive",
}

# ─── Workers ─────────────────────────────────────────────────────
DEFAULT_WORKER_COUNT = 3
MAX_WORKER_COUNT = 10
RETRY_MAX = 3
RETRY_ON_CAPTCHA_DEMOTE = True

# ─── Audio ───────────────────────────────────────────────────────
SUPPORTED_OUTPUT_FORMATS = ["mp3", "wav", "ogg", "flac"]
DEFAULT_OUTPUT_FORMAT = "mp3"
DEFAULT_OUTPUT_DIR = "./output"

# ─── Text processing ────────────────────────────────────────────
MAX_CHARS_PER_REQUEST = 5000
TEXT_SPLIT_MODES = ["paragraph", "sentence", "fixed"]
DEFAULT_SPLIT_MODE = "paragraph"

# ─── Runtime rieng: Camoufox + uBO di kem app ────────────────────
# Layout (giong ban v4.1):
#   runtime/config.json                 {"active_version": "browsers/official/_r1"}
#   runtime/browsers/official/_r1/camoufox.exe
#   runtime/addons/_a1/manifest.json    (uBlock Origin da giai nen)
#
# Doc them tu bien moi truong de tro sang thu muc co san ma khong phai copy:
#   VOICE_CAMOUFOX_DIR  -> thu muc chua camoufox.exe
#   VOICE_UBO_DIR       -> thu muc uBO da giai nen (co manifest.json)
import os as _os
import json as _json

APP_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
RUNTIME_DIR = _os.path.join(APP_DIR, "runtime")
RUNTIME_CONFIG = _os.path.join(RUNTIME_DIR, "config.json")

UBO_EXTENSION_ID = "uBlock0@raymondhill.net"
_CAMOUFOX_BIN = "camoufox.exe" if _os.name == "nt" else "camoufox"


def _runtime_cfg() -> dict:
    try:
        with open(RUNTIME_CONFIG, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_camoufox_exe() -> str:
    """Tra ve duong dan camoufox.exe dung duoc, hoac "" de Camoufox tu tai.

    Thu tu uu tien: env -> runtime/config.json -> runtime/browsers/official/*.
    """
    env = (_os.environ.get("VOICE_CAMOUFOX_DIR") or "").strip()
    cands = []
    if env:
        cands.append(env)

    cfg = _runtime_cfg()
    for key in ("camoufox_dir", "active_version"):
        val = (cfg.get(key) or "").strip() if isinstance(cfg.get(key), str) else ""
        if val:
            cands.append(val if _os.path.isabs(val) else _os.path.join(RUNTIME_DIR, val))

    official = _os.path.join(RUNTIME_DIR, "browsers", "official")
    if _os.path.isdir(official):
        for name in sorted(_os.listdir(official)):
            cands.append(_os.path.join(official, name))

    for d in cands:
        exe = d if d.lower().endswith(_CAMOUFOX_BIN) else _os.path.join(d, _CAMOUFOX_BIN)
        if _os.path.isfile(exe):
            return exe
    return ""


def resolve_camoufox_ff_version(exe_path: str = "") -> int:
    """Doc major version tu application.ini canh camoufox.exe.

    Bat buoc khi dung binary ngoai: khong truyen ``ff_version`` thi Camoufox
    van di tra ban cai trong cache cua no va bao "official/stable is not
    installed" du binary da nam san trong tay.
    """
    exe = exe_path or resolve_camoufox_exe()
    if not exe:
        return 0
    ini = _os.path.join(_os.path.dirname(exe), "application.ini")
    try:
        with open(ini, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("Version="):
                    return int(line.split("=", 1)[1].strip().split(".", 1)[0])
    except Exception:
        pass
    return 0


def resolve_ubo_dir() -> str:
    """Tra ve thu muc uBO da giai nen (co manifest.json), hoac "" neu khong co.

    Thu muc thieu manifest.json bi coi la hong va bo qua - day chinh la loi
    "manifest.json is missing" khi cache cua Camoufox bi xoa mat file.
    """
    cands = []
    env = (_os.environ.get("VOICE_UBO_DIR") or "").strip()
    if env:
        cands.append(env)
    cfg = _runtime_cfg()
    val = cfg.get("ubo_dir")
    if isinstance(val, str) and val.strip():
        v = val.strip()
        cands.append(v if _os.path.isabs(v) else _os.path.join(RUNTIME_DIR, v))
    addons = _os.path.join(RUNTIME_DIR, "addons")
    if _os.path.isdir(addons):
        for name in sorted(_os.listdir(addons)):
            cands.append(_os.path.join(addons, name))

    for d in cands:
        if _os.path.isfile(_os.path.join(d, "manifest.json")):
            return d
    return ""


# ─── HSW context pool (gia tri lay tu binary v4.1) ───────────────
HSW_CONTEXTS = 6                       # so context giai captcha song song
HTTP_TIMEOUT = 90                      # giay - httpx/tls_client
HSW_ACQUIRE_TIMEOUT_SEC = 30           # cho toi da de muon duoc mot slot ranh
HSW_RECYCLE_AFTER_SOLVES = 12          # thay context sau bao nhieu lan solve
HSW_RECYCLE_AFTER_AGE_SEC = 900        # ...hoac sau bao lau ke tu khi tao
HSW_ZOMBIE_SWEEP_INTERVAL_SEC = 30     # chu ky quet slot treo
HSW_ZOMBIE_STUCK_THRESHOLD_SEC = 120   # busy lau hon nay -> coi la zombie
HSW_CB_ZOMBIE_WINDOW_SEC = 300         # cua so dem zombie cho circuit breaker
HSW_CB_ZOMBIE_THRESHOLD = 3            # du zombie trong cua so -> mo circuit
HSW_MAX_CONCURRENT_RECYCLE = 3         # gioi han so context recycle cung luc
HSW_BROWSER_RESTART_AFTER_SOLVES = 120  # restart browser -> fingerprint moi
HSW_BROWSER_RESTART_COOLDOWN_SEC = 300  # khoang cach toi thieu giua 2 lan restart
HSW_RECYCLE_ON_401 = True              # 401 -> recycle ngay slot vua dung
HSW_401_STREAK_TO_RESTART = 3          # so 401 lien tiep de restart browser
HSW_401_STREAK_WINDOW_SEC = 120        # cua so dem chuoi 401
FIFO_GATE_MIN_SEC = 3.0                # khoang cach toi thieu giua 2 lan solve
MIN_SOLVE_INTERVAL = FIFO_GATE_MIN_SEC

# ─── Timeout / backoff cho worker ───────────────────────────────
CAPTCHA_SOLVE_TIMEOUT = 150            # giay - cho mot lan solve hoan tat
TTS_TIMEOUT = 90                       # giay - cho mot request TTS
MAX_NET_BACKOFF = 30.0                 # tran backoff khi loi mang
IP_ROTATIONS_PER_TASK = 3              # so lan doi proxy toi da cho 1 doan

# ─── Ho so header (FF120 / FF152) ───────────────────────────────
UA_FF152 = USER_AGENTS[0]
UA_FF120 = USER_AGENTS[1]
EL_PROFILE = (_os.environ.get("VOICE_EL_PROFILE") or "FF120").strip().upper()


def build_el_headers(profile: str = "") -> dict:
    """Header goi endpoint anonymous, khop voi UA cua profile duoc chon."""
    prof = (profile or EL_PROFILE).strip().upper()
    ua = UA_FF152 if prof == "FF152" else UA_FF120
    headers = dict(ELEVENLABS_HEADERS)
    headers["User-Agent"] = ua
    return headers


# ─── Minimax API ─────────────────────────────────────────────────
MINIMAX_DEFAULT_MODEL = "speech-01-turbo"
MINIMAX_DEFAULT_VOICE = "male-qn-qingse"
MINIMAX_VOICE_SETTINGS = {
    "speed": 1.0,
    "vol": 1.0,
    "pitch": 0,
}

# Danh sách giọng Minimax mặc định (phổ biến)
MINIMAX_POPULAR_VOICES = [
    {"voice_id": "male-qn-qingse", "name": "Nam Thanh Niên", "gender": "male", "language": "vi"},
    {"voice_id": "female-shaonv", "name": "Nữ Thiếu Nữ", "gender": "female", "language": "vi"},
    {"voice_id": "female-yujie", "name": "Nữ Trưởng Thành", "gender": "female", "language": "vi"},
    {"voice_id": "male-播音", "name": "Nam Phát Thanh", "gender": "male", "language": "vi"},
]
