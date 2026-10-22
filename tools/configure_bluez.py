#!/usr/bin/env python3
"""Back up and edit only BlueZ's General/DeviceID; no service or pairing changes."""
import argparse
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone

CONFIG = Path("/etc/bluetooth/main.conf")
VALUE = "DeviceID = bluetooth:004C:0000:0000\n"


def edit(text):
    lines = text.splitlines(keepends=True)
    section = None
    result = []
    in_general = False
    saw_general = False
    written = False
    for line in lines:
        match = re.match(r"\s*\[([^]]+)\]", line)
        if match:
            if in_general and not written:
                result.append(VALUE)
                written = True
            section = match.group(1).strip().lower()
            in_general = section == "general"
            saw_general |= in_general
        if in_general and re.match(r"\s*DeviceID\s*=", line, re.I):
            if not written:
                result.append(VALUE)
                written = True
            continue
        result.append(line if line.endswith("\n") else line + "\n")
    if not saw_general:
        result.extend(["\n[General]\n", VALUE])
    elif in_general and not written:
        result.append(VALUE)
    return "".join(result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    options = p.add_mutually_exclusive_group(required=True)
    options.add_argument("--enable", action="store_true")
    options.add_argument("--restore", type=Path)
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("Run this configuration helper with sudo; run the app as your normal user")
    if CONFIG.is_symlink() or not CONFIG.is_file():
        p.error(f"Expected a regular non-symlink configuration file: {CONFIG}")
    metadata = CONFIG.stat()
    before = CONFIG.read_bytes()
    after = args.restore.read_bytes() if args.restore else edit(before.decode()).encode()
    if before == after:
        print("Configuration already matches. No changes.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = CONFIG.with_name(CONFIG.name + ".airpods-" + stamp)
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(before)
        file.flush()
        os.fsync(file.fileno())
    temp_fd, temp_name = tempfile.mkstemp(prefix=".main.conf-", dir=CONFIG.parent)
    try:
        with os.fdopen(temp_fd, "wb") as file:
            os.fchmod(file.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fchown(file.fileno(), metadata.st_uid, metadata.st_gid)
            file.write(after)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, CONFIG)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"Original configuration backup: {backup}")
    print("Configuration updated. Restart when ready: sudo systemctl restart bluetooth")


if __name__ == "__main__":
    main()
