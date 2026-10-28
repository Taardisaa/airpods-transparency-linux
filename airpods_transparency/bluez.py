"""Use BlueZ's existing ATT connection instead of competing with it.

Uses busctl JSON output (systemd) to avoid another Python dependency. The
transparency UUID was observed on AirPods Pro 3 and is selected by UUID/device,
never by the generated object-path suffix or the cached Value property.
"""
import json
import subprocess
import re
import xml.etree.ElementTree as ET

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
        raise RuntimeError(f"BlueZ {method}: {result.stderr.strip() or 'D-Bus call failed'} (exit {result.returncode})")
    return json.loads(result.stdout) if result.stdout.strip() else None


def child_paths(path):
    result = subprocess.run(
        ["busctl", "--system", "--timeout=8", "--xml-interface", "introspect", "org.bluez", path],
        capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(f"BlueZ introspection {path}: {result.stderr.strip() or 'failed'} (exit {result.returncode})")
    return [path.rstrip("/") + "/" + node.attrib["name"]
            for node in ET.fromstring(result.stdout).findall("node")
            if re.fullmatch(r"[A-Za-z0-9_]+", node.get("name", ""))]


def property_value(path, interface, name):
    result = subprocess.run(
        ["busctl", "--system", "--timeout=8", "--json=short", "get-property",
         "org.bluez", path, interface, name], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(f"BlueZ {name}: {result.stderr.strip() or 'property read failed'} (exit {result.returncode})")
    return json.loads(result.stdout)["data"]


def device_paths(mac):
    name = "dev_" + normalize_mac(mac).replace(":", "_")
    paths = []
    for adapter in child_paths("/org/bluez"):
        if re.fullmatch(r"hci\d+", adapter.rsplit("/", 1)[-1]):
            paths.extend(p for p in child_paths(adapter) if p.rsplit("/", 1)[-1] == name)
    return paths


def device_connected(mac):
    # Query only this device. A bad or unserializable property on any other
    # nearby device must not make a connected AirPods disappear from the UI.
    return any(property_value(path, "org.bluez.Device1", "Connected") for path in device_paths(mac))


def reconnect_audio(mac):
    """Reset this device's channels, then connect its A2DP sink profile only.

    Explicit user action: audio pauses briefly. Never called as a side effect of
    reading settings, and never removes pairing or restarts the adapter.
    """
    paths = device_paths(mac)
    if len(paths) != 1:
        raise ValueError("Expected one paired AirPods device on one adapter")
    path = paths[0]
    if property_value(path, "org.bluez.Device1", "Connected"):
        bus_call(path, "org.bluez.Device1", "Disconnect")
    bus_call(path, "org.bluez.Device1", "ConnectProfile", "s",
             "0000110b-0000-1000-8000-00805f9b34fb")
    return {"bluetooth_connected": device_connected(mac), "profile": "a2dp_sink"}


def find_transparency_targeted(mac):
    candidates = []
    for device in device_paths(mac):
        for service in child_paths(device):
            if not service.rsplit("/", 1)[-1].startswith("service"):
                continue
            for path in child_paths(service):
                if not path.rsplit("/", 1)[-1].startswith("char"):
                    continue
                if property_value(path, CHARACTERISTIC, "UUID").lower() == TRANSPARENCY_UUID:
                    candidates.append((path, property_value(path, CHARACTERISTIC, "Flags")))
    return unique_characteristic(candidates)


def unique_characteristic(candidates):
    if len(candidates) > 1:
        raise ValueError("Multiple transparency characteristics found; select a single connected adapter")
    return candidates[0] if candidates else None


def find_transparency(mac):
    device_suffix = "/dev_" + normalize_mac(mac).replace(":", "_")
    try:
        response = bus_call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return find_transparency_targeted(mac)
    candidates = []
    for path, interfaces in response["data"][0].items():
        # Device identity is an entire path component; supports non-hci0 adapters.
        if device_suffix + "/" not in path:
            continue
        props = interfaces.get(CHARACTERISTIC, {})
        if props.get("UUID", {}).get("data", "").lower() == TRANSPARENCY_UUID:
            candidates.append((path, props.get("Flags", {}).get("data", [])))
    return unique_characteristic(candidates)


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
        # Cached characteristic objects do not prove BlueZ has a usable GATT
        # link. The direct BR/EDR channel can work while ReadValue fails.
        if self.transport != "bluez":
            self.client = ATTClient(self.mac)
            try:
                self.client.__enter__()
            except ConnectionRefusedError as original:
                if self.transport == "att":
                    raise
                characteristic = find_transparency(self.mac)
                if not characteristic:
                    raise ConnectionRefusedError(
                        "ATT connection refused and BlueZ has no discovered transparency service. "
                        "Use reconnect --mac ADDRESS to reconnect the audio profile, then retry."
                    ) from original
            else:
                self.backend = "att"
                return self
        else:
            characteristic = find_transparency(self.mac)
        self.client = BlueZClient(self.mac, characteristic)
        self.client.__enter__()
        self.backend = "bluez"
        return self

    def __exit__(self, *args):
        if self.client:
            return self.client.__exit__(*args)

    def read(self):
        return self.client.read()

    def write(self, value):
        return self.client.write(value)
