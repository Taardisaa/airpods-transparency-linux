"""Opt-in AirPods Pro 3 hardware check on PulseAudio. Run as the desktop user."""
import argparse
from contextlib import contextmanager
import json
import subprocess
import sys
import time

from airpods_transparency.controller import save_backup
from airpods_transparency.service import rpc
from airpods_transparency.settings import Settings
from airpods_transparency.transport import normalize_mac


def report(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


def sink_state(name):
    output = subprocess.check_output(["pactl", "list", "short", "sinks"], text=True, timeout=5)
    for line in output.splitlines():
        fields = line.split("\t")
        if fields[1] == name:
            return fields[0], fields[-1]
    return None, None


@contextmanager
def playing(name):
    deadline = time.monotonic() + 10
    while (sink_id := sink_state(name)[0]) is None:
        if time.monotonic() >= deadline:
            raise RuntimeError("PulseAudio stereo sink did not appear")
        time.sleep(0.2)
    with open("/dev/zero", "rb") as silence:
        stream = subprocess.Popen(["pacat", "--playback", "--raw", "--format=s16le", "--channels=2",
                                   "--rate=44100", "--device", name], stdin=silence)
        try:
            deadline = time.monotonic() + 5
            while sink_state(name) != (sink_id, "RUNNING"):
                if stream.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Silent stereo stream did not start")
                time.sleep(0.1)
            yield stream, sink_id
        finally:
            if stream.poll() is None:
                stream.terminate()
            stream.wait(timeout=5)


def read_settings(mac):
    result = rpc("read", mac)
    if result["backend"] not in ("bluez", "att"):
        raise RuntimeError("Settings read did not report a device transport")
    return Settings(bytes.fromhex(result["raw_hex"]))


def observe(mac, name, stream, sink_id, expected):
    start = time.monotonic()
    next_read = start
    while time.monotonic() - start < 45:
        if stream.poll() is not None or sink_state(name) != (sink_id, "RUNNING"):
            raise ConnectionError("Active stereo output changed during the test")
        if time.monotonic() >= next_read:
            if read_settings(mac) != expected:
                raise RuntimeError("Live settings differ from the expected bytes")
            next_read = time.monotonic() + 5
        time.sleep(0.5)


def cycle(mac, number, prior, prior_sink):
    result = rpc("reconnect", mac, rebuild_settings=True)
    if not result.get("settings_ready") or result.get("backend") not in ("bluez", "att"):
        raise RuntimeError(result.get("settings_error") or result.get("settings_skipped")
                           or "Settings channel did not become readable")
    original = Settings(bytes.fromhex(result["raw_hex"]))
    if prior is not None and original != prior:
        raise RuntimeError("Original settings changed across reconnect")
    values = original.values()
    changes = {k: max(-1.0, values[k] - 0.001) for k in ("left_amplification", "right_amplification")}
    target = original.edited(changes)
    if target == original:
        raise RuntimeError("Both gains are already at minimum; no attenuation test available")
    name = "bluez_sink." + mac.replace(":", "_") + ".a2dp_sink"
    with playing(name) as (stream, sink_id):
        if prior_sink == sink_id:
            raise RuntimeError("New audio connection was not observed")
        report("audio_started", cycle=number, sink_id=sink_id, backend=result["backend"])
        observe(mac, name, stream, sink_id, original)
        backup = save_backup(mac, original)
        report("backup_saved", path=str(backup))
        try:
            result = rpc("apply", mac, changes=changes, expected=original.raw.hex())
            if result["raw_hex"] != target.raw.hex():
                raise RuntimeError("Requested gain change did not match readback")
            report("write_verified", cycle=number, backend=result["backend"])
            observe(mac, name, stream, sink_id, target)
        finally:
            current = read_settings(mac)
            if current == target:
                restored = rpc("apply", mac, restore=str(backup), expected=target.raw.hex())
                if restored["raw_hex"] != original.raw.hex():
                    raise RuntimeError(f"Restore did not match the backup at {backup}")
            if read_settings(mac) != original:
                raise RuntimeError(f"Original settings could not be verified. Backup: {backup}")
        report("original_verified", cycle=number)
        if stream.poll() is not None or sink_state(name) != (sink_id, "RUNNING"):
            raise ConnectionError("Stereo output changed during restore")
    report("cycle_verified", cycle=number, sink_id=sink_id)
    return original, sink_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", required=True, type=normalize_mac)
    args = parser.parse_args()
    previous = previous_sink = None
    for number in (1, 2):
        previous, previous_sink = cycle(args.mac, number, previous, previous_sink)
    report("passed", reconnections=2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        report("failed", reason=str(exc))
        sys.exit(1)
