"""Use BlueZ's existing ATT connection instead of competing with it.

Uses busctl JSON output (systemd) to avoid another Python dependency. The
transparency UUID was observed on AirPods Pro 3 and is selected by UUID/device,
never by the generated object-path suffix or the cached Value property.
"""
import json
import subprocess

from .transport import ATTClient, normalize_mac

TRANSPARENCY_UUID = "d5621cc1-f7ab-45db-9403-9eaf744d5390"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"


def bus_call(path, interface, method, *arguments):
    result = subprocess.run(
        ["busctl", "--system", "--json=short", "--timeout=8", "call", "org.bluez",
         path, interface, method, *map(str, arguments)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        raise RuntimeError(f"BlueZ {method}: {result.stderr.strip() or 'D-Bus call failed'}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def find_transparency(mac):
    device_suffix = "/dev_" + normalize_mac(mac).replace(":", "_")
    response = bus_call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    candidates = []
    for path, interfaces in response["data"][0].items():
        # Device identity is an entire path component; supports non-hci0 adapters.
        if device_suffix + "/" not in path:
            continue
        props = interfaces.get(CHARACTERISTIC, {})
        if props.get("UUID", {}).get("data", "").lower() == TRANSPARENCY_UUID:
            candidates.append((path, props.get("Flags", {}).get("data", [])))
    if len(candidates) > 1:
        raise ValueError("Multiple transparency characteristics found; select a single connected adapter")
    return candidates[0] if candidates else None


class BlueZClient:
    backend = "bluez"

    def __init__(self, mac, characteristic=None):
        self.mac = normalize_mac(mac)
        self.characteristic = characteristic

    def __enter__(self):
        if self.characteristic is None:
            self.characteristic = find_transparency(self.mac)
        if self.characteristic is None:
            raise RuntimeError("BlueZ has no discovered transparency characteristic for this device")
        self.path, self.flags = self.characteristic
        return self

    def __exit__(self, *args):
        pass  # BlueZ owns the connection; do not disconnect the user's audio.

    def read(self):
        result = bus_call(self.path, CHARACTERISTIC, "ReadValue", "a{sv}", 0)
        if not result or result.get("type") != "ay":
            raise RuntimeError("Unexpected BlueZ ReadValue response")
        return bytes(result["data"][0])

    def write(self, value):
        from .settings import Settings
        Settings(value)
        if "write" in self.flags:
            kind = "request"
        elif "write-without-response" in self.flags:
            kind = "command"
        else:
            raise RuntimeError("BlueZ transparency characteristic is not writable")
        bus_call(self.path, CHARACTERISTIC, "WriteValue", "aya{sv}", len(value),
                 *value, 1, "type", "s", kind)


class SettingsClient:
    def __init__(self, mac, transport="auto"):
        self.mac = normalize_mac(mac)
        if transport not in ("auto", "att", "bluez"):
            raise ValueError("Unknown transport")
        self.transport = transport
        self.client = None
        self.backend = None

    def __enter__(self):
        characteristic = None
        if self.transport != "att":
            try:
                characteristic = find_transparency(self.mac)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                if self.transport == "bluez":
                    raise
        if characteristic or self.transport == "bluez":
            self.client = BlueZClient(self.mac, characteristic)
            self.backend = "bluez"
        else:
            self.client = ATTClient(self.mac)
            self.backend = "att"
        try:
            self.client.__enter__()
        except ConnectionRefusedError as original:
            # Services can appear between discovery and connect. Retry discovery,
            # not a write; never retry writes through a second transport.
            if self.transport != "auto":
                raise
            characteristic = find_transparency(self.mac)
            if not characteristic:
                raise ConnectionRefusedError(
                    "ATT connection refused and BlueZ has no discovered transparency service. "
                    "Reconnect AirPods, then retry; use doctor --json for diagnostics."
                ) from original
            self.client = BlueZClient(self.mac, characteristic)
            self.backend = "bluez"
            self.client.__enter__()
        return self

    def __exit__(self, *args):
        if self.client:
            return self.client.__exit__(*args)

    def read(self):
        return self.client.read()

    def write(self, value):
        return self.client.write(value)
