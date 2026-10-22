"""Portable scalar presets; no addresses, EQ arrays or device serial numbers."""
import fcntl
import json
import os
import struct
import tempfile
from pathlib import Path

from .aacp import MODES
from .settings import Settings


def config_directory():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "airpods-transparency"


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get("schema") != 1:
        raise ValueError("Unsupported preset schema")
    values = profile.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("Preset must contain transparency settings")
    Settings(struct.pack("<26f", *([0] * 26))).edited(values)
    mode = profile.get("mode")
    if mode is not None and mode not in MODES:
        raise ValueError("Unsupported preset listening mode")
    return profile


class ProfileStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else config_directory()

    def operation(self, update=None):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / "presets.json"
        fd = os.open(self.directory / "presets.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            profiles = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(profiles, dict):
                raise ValueError("Invalid presets file")
            if update:
                update(profiles)
                temp_fd, temp = tempfile.mkstemp(dir=self.directory, prefix=".presets-")
                try:
                    with os.fdopen(temp_fd, "w") as file:
                        json.dump(profiles, file, indent=2, ensure_ascii=False)
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(temp, path)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
            return profiles

    def list(self):
        return self.operation()

    def get(self, name):
        profiles = self.list()
        if name not in profiles:
            raise ValueError(f"Preset not found: {name}")
        return validate_profile(profiles[name])

    def save(self, name, values, mode=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Preset name must contain 1–80 characters")
        name = name.strip()
        profile = validate_profile({"schema": 1, "values": values, "mode": mode})
        self.operation(lambda profiles: profiles.update({name: profile}))
        return profile

    def delete(self, name):
        def remove(profiles):
            if name not in profiles:
                raise ValueError(f"Preset not found: {name}")
            del profiles[name]
        self.operation(remove)
