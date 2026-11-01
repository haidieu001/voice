import httpx
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

# 1. Search shared-voices with language=vi
url = "https://api.elevenlabs.io/v1/shared-voices"
params_list = [
    {"language": "vi", "page_size": 100},
    {"language": "vietnamese", "page_size": 100},
    {"search": "vietnam", "page_size": 100},
    {"search": "vietnamese", "page_size": 100},
    {"gender": "male", "page_size": 100},
]

for p in params_list:
    try:
        r = httpx.get(url, params=p, headers=headers, timeout=10)
        if r.status_code == 200:
            voices = r.json().get("voices", [])
            print(f"Params {p} -> {len(voices)} voices")
            for v in voices:
                if v.get("gender") == "male" or "nam" in v.get("name", "").lower():
                    print(f"  [MALE] {v.get('voice_id')} | {v.get('name')} | {v.get('accent')} | {v.get('category')} | {v.get('description', '')[:50]}")
        else:
            print(f"Params {p} -> Status {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"Error {p}: {e}")
