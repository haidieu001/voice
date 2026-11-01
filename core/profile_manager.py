"""
profile_manager.py — Voice profile CRUD: save, load, import, export.
Each profile stores a voice_id + voice_settings + model_id + language.
"""

import os
import json
import time
import logging
from typing import List, Optional, Dict

log = logging.getLogger(__name__)

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "profiles")


class VoiceProfile:
    """A saved TTS voice configuration."""

    def __init__(self, name: str, voice_id: str, voice_name: str = "",
                 model_id: str = "eleven_v3", language: str = "vi",
                 stability: float = 0.75, similarity_boost: float = 0.80,
                 style: float = 0.30, speed: float = 1.0,
                 use_speaker_boost: bool = True,
                 created_at: float = 0.0, profile_id: str = ""):
        self.profile_id = profile_id or f"p_{int(time.time() * 1000)}"
        self.name = name
        self.voice_id = voice_id
        self.voice_name = voice_name
        self.model_id = model_id
        self.language = language
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.style = style
        self.speed = speed
        self.use_speaker_boost = use_speaker_boost
        self.created_at = created_at or time.time()

    def to_dict(self) -> Dict:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "voice_id": self.voice_id,
            "voice_name": self.voice_name,
            "model_id": self.model_id,
            "language": self.language,
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "style": self.style,
            "speed": self.speed,
            "use_speaker_boost": self.use_speaker_boost,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "VoiceProfile":
        return cls(
            profile_id=data.get("profile_id", ""),
            name=data.get("name", "Unnamed"),
            voice_id=data.get("voice_id", ""),
            voice_name=data.get("voice_name", ""),
            model_id=data.get("model_id", "eleven_v3"),
            language=data.get("language", "vi"),
            stability=data.get("stability", 0.75),
            similarity_boost=data.get("similarity_boost", 0.80),
            style=data.get("style", 0.30),
            speed=data.get("speed", 1.0),
            use_speaker_boost=data.get("use_speaker_boost", True),
            created_at=data.get("created_at", 0.0),
        )


class ProfileManager:
    """Manages voice profiles on disk."""

    def __init__(self, profiles_dir: str = ""):
        self.profiles_dir = profiles_dir or PROFILES_DIR
        os.makedirs(self.profiles_dir, exist_ok=True)
        self._profiles: Dict[str, VoiceProfile] = {}
        self._load_all()

    def _profiles_file(self) -> str:
        return os.path.join(self.profiles_dir, "profiles.json")

    def _load_all(self):
        """Load all profiles from disk."""
        path = self._profiles_file()
        if not os.path.isfile(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for item in data:
                p = VoiceProfile.from_dict(item)
                self._profiles[p.profile_id] = p
            log.info("Loaded %d profiles", len(self._profiles))
        except Exception as e:
            log.error("Failed to load profiles: %s", e)

    def _save_all(self):
        """Persist all profiles to disk."""
        try:
            data = [p.to_dict() for p in self._profiles.values()]
            with open(self._profiles_file(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error("Failed to save profiles: %s", e)

    def list_profiles(self) -> List[Dict]:
        """Return all profiles as dicts."""
        return [p.to_dict() for p in sorted(
            self._profiles.values(), key=lambda x: x.created_at, reverse=True
        )]

    def get_profile(self, profile_id: str) -> Optional[Dict]:
        """Get a single profile by ID."""
        p = self._profiles.get(profile_id)
        return p.to_dict() if p else None

    def add_profile(self, data: Dict) -> Dict:
        """Create a new profile."""
        p = VoiceProfile.from_dict(data)
        self._profiles[p.profile_id] = p
        self._save_all()
        return p.to_dict()

    def update_profile(self, profile_id: str, data: Dict) -> Optional[Dict]:
        """Update an existing profile."""
        if profile_id not in self._profiles:
            return None
        existing = self._profiles[profile_id]
        for key in ("name", "voice_id", "voice_name", "model_id", "language",
                     "stability", "similarity_boost", "style", "speed",
                     "use_speaker_boost"):
            if key in data:
                setattr(existing, key, data[key])
        self._save_all()
        return existing.to_dict()

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile."""
        if profile_id in self._profiles:
            del self._profiles[profile_id]
            self._save_all()
            return True
        return False

    def export_profiles(self, filepath: str) -> bool:
        """Export all profiles to a JSON file."""
        try:
            data = [p.to_dict() for p in self._profiles.values()]
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            log.error("Export failed: %s", e)
            return False

    def import_profiles(self, filepath: str) -> int:
        """Import profiles from a JSON file. Returns count of imported profiles."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            count = 0
            for item in data:
                p = VoiceProfile.from_dict(item)
                p.profile_id = f"p_{int(time.time() * 1000)}_{count}"
                self._profiles[p.profile_id] = p
                count += 1
            self._save_all()
            return count
        except Exception as e:
            log.error("Import failed: %s", e)
            return 0
