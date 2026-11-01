import httpx
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Check /v1/voices (default pre-made voices)
r = httpx.get("https://api.elevenlabs.io/v1/voices")
if r.status_code == 200:
    voices = r.json().get("voices", [])
    print(f"--- PREMADE VOICES ({len(voices)}) ---")
    for v in voices:
        labels = v.get("labels", {})
        gender = labels.get("gender")
        accent = labels.get("accent")
        des = labels.get("description", "")
        use = labels.get("use_case", "")
        if gender == "male":
            print(f"{v['voice_id']} | {v['name']} | {accent} | {des} | {use}")

# Search for vietnamese in shared voices
print("\n--- SEARCHING VIETNAMESE VOICES ---")
r2 = httpx.get("https://api.elevenlabs.io/v1/shared-voices?search=vietnamese&page_size=50", headers={"User-Agent": "Mozilla/5.0"})
if r2.status_code == 200:
    shared = r2.json().get("voices", [])
    print(f"Found {len(shared)} shared voices for 'vietnamese'")
    for v in shared:
        print(f"{v.get('voice_id')} | {v.get('name')} | {v.get('gender')} | {v.get('accent')} | {v.get('category')}")

r3 = httpx.get("https://api.elevenlabs.io/v1/shared-voices?search=vietnam&page_size=50", headers={"User-Agent": "Mozilla/5.0"})
if r3.status_code == 200:
    shared3 = r3.json().get("voices", [])
    print(f"Found {len(shared3)} shared voices for 'vietnam'")
    for v in shared3:
        print(f"{v.get('voice_id')} | {v.get('name')} | {v.get('gender')} | {v.get('accent')} | {v.get('category')}")
