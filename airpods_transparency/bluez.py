"""Use BlueZ's existing ATT connection instead of competing with it.

Uses busctl JSON output (systemd) to avoid another Python dependency. The
transparency UUID was observed on AirPods Pro 3 and is selected by UUID/device,
never by the generated object-path suffix or the cached Value property.
"""
import json
import subprocess
import re
import time
import xml.etree.ElementTree as ET

from .transport import ATTClient, normalize_mac

TRANSPARENCY_UUID = "d5621cc1-f7ab-45db-9403-9eaf744d5390"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"


def bus_call(path, interface, method, *arguments, timeout=8):
    result = subprocess.run(
        ["busctl", "--system", "--json=short", f"--timeout={timeout}", "call", "org.bluez",
         path, interface, method, *map(str, arguments)],
        capture_output=True, text=True, timeout=timeout + 2,
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


A2DP_SOURCE = "0000110a-0000-1000-8000-00805f9b34fb"
A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"
DEVICE = "org.bluez.Device1"


def a2dp_connected(path):
    # A configured local A2DP source transport exists even while music is idle.
    # Connected alone could also mean an ATT-only connection.
    children = child_paths(path)
    transports = [p for p in children if re.fullmatch(r"fd\d+", p.rsplit("/", 1)[-1])]
    for endpoint in children:
        if re.fullmatch(r"sep\d+", endpoint.rsplit("/", 1)[-1]):
            transports.extend(child_paths(endpoint))
    for transport in transports:
        if not re.fullmatch(r"fd\d+", transport.rsplit("/", 1)[-1]):
            continue
        try:
            uuid = property_value(transport, "org.bluez.MediaTransport1", "UUID")
        except RuntimeError:
            continue  # Transport can disappear during profile negotiation.
        if uuid.lower() == A2DP_SOURCE:
            return True
    return False


def stereo_connected(mac):
    paths = device_paths(mac)
    return len(paths) == 1 and a2dp_connected(paths[0])


def ensure_audio(path):
    if a2dp_connected(path):
        return
    for attempt in range(2):
        failure = None
        try:
            bus_call(path, DEVICE, "ConnectProfile", "s", A2DP_SINK, timeout=25)
        except RuntimeError as exc:
            # BlueZ can cancel our call while another connection is completing.
            # Reconcile with the actual transport before issuing another call.
            if not any(code in str(exc).lower() for code in
                       ("br-connection-canceled", "br-connection-unknown", "inprogress",
                        "in progress", "already connected")):
                if a2dp_connected(path):
                    return
                raise
            failure = exc
        deadline = time.monotonic() + 3
        while not a2dp_connected(path):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        else:
            return
        if attempt and failure is not None:
            raise failure
    raise ConnectionError("BlueZ did not establish a stereo audio transport")


def uses_linux_device_id(path):
    adapter = path.rsplit("/", 1)[0]
    try:
        modalias = property_value(adapter, "org.bluez.Adapter1", "Modalias")
    except RuntimeError:
        return False
    return modalias.lower().startswith("usb:v1d6bp0246")


def reconnect_audio(mac, rebuild_settings=False):
    paths = device_paths(mac)
    if len(paths) != 1:
        raise ValueError("Expected one paired AirPods device on one adapter")
    path = paths[0]
    linux_id = uses_linux_device_id(path)
    if rebuild_settings and not linux_id and property_value(path, DEVICE, "Connected"):
        bus_call(path, DEVICE, "Disconnect")
        deadline = time.monotonic() + 5
        while property_value(path, DEVICE, "Connected"):
            if time.monotonic() >= deadline:
                raise TimeoutError("AirPods disconnection did not finish")
            time.sleep(0.1)
    ensure_audio(path)
    return {"bluetooth_connected": True, "profile": "a2dp_sink", "linux_device_id": linux_id}


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
    except (RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return find_transparency_targeted(mac)
    if not response:
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
