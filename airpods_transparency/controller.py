"""Backup-before-write and readback verification shared by CLI and GUI."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .settings import Settings
from .transport import ATTClient, normalize_mac


def backup_directory():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "airpods-transparency/backups"


def save_backup(mac, settings, directory=None):
    directory = Path(directory) if directory is not None else backup_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(timezone.utc).isoformat()
    path = directory / (timestamp.replace(":", "-") + ".json")
    payload = {"schema": 1, "mac": normalize_mac(mac), "created_at": timestamp,
               "handle": "0x0018", "raw_hex": settings.raw.hex(), "values": settings.values()}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    return path


def load_backup(path, mac):
    payload = json.loads(Path(path).read_text())
    if payload.get("schema") != 1 or payload.get("handle") != "0x0018":
        raise ValueError("Unsupported backup format")
    if payload.get("mac") != normalize_mac(mac):
        raise ValueError("Backup belongs to a different Bluetooth device")
    return Settings(bytes.fromhex(payload["raw_hex"]))


class Controller:
    def __init__(self, mac, backup_dir=None, factory=ATTClient):
        self.mac = normalize_mac(mac)
        self.backup_dir = backup_dir
        self.factory = factory

    def read(self):
        with self.factory(self.mac) as client:
            return Settings(client.read())

    def apply(self, changes=None, restore=None, expected=None):
        with self.factory(self.mac) as client:
            current = Settings(client.read())
            if expected is not None and current.raw != expected.raw:
                raise RuntimeError("Settings changed since loading. Reload before applying.")
            target = load_backup(restore, self.mac) if restore else current.edited(changes or {})
            if len(target.raw) != len(current.raw):
                raise ValueError("Backup layout differs from current firmware")
            if target.raw == current.raw:
                return current, None
            backup = save_backup(self.mac, current, self.backup_dir)
            try:
                client.write(target.raw)
                observed = Settings(client.read())
                if observed.raw != target.raw:
                    raise RuntimeError("Readback differs from requested settings")
            except Exception as exc:
                raise RuntimeError(f"Write or verification failed: {exc}. Device may have changed; "
                                   f"reload before retrying. Original backup: {backup}") from exc
            return observed, backup
