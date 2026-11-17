"""One-time host configuration for custom transparency."""
import configparser
import subprocess
import time
from pathlib import Path

from . import bluez

APPLE_MODALIAS = "bluetooth:v004cp0000d0000"


def configured_identity():
    config = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=("#", ";"))
    with Path("/etc/bluetooth/main.conf").open() as file:
        config.read_file(file)
    return config.get("General", "DeviceID", fallback="").lower() == "bluetooth:004c:0000:0000"


def host_modalias(mac):
    paths = bluez.device_paths(mac)
    if len(paths) != 1:
        raise ValueError("Expected one paired AirPods device on one adapter")
    adapter = paths[0].rsplit("/", 1)[0]
    return bluez.property_value(adapter, "org.bluez.Adapter1", "Modalias")


def enable_advanced(mac):
    active = host_modalias(mac).lower() == APPLE_MODALIAS
    try:
        configured = configured_identity()
    except (OSError, configparser.Error):
        configured = False
    if active and configured:
        return {"changed": False, "host_modalias": APPLE_MODALIAS}
    helper = Path(__file__).with_name("configure_bluez.py")
    result = subprocess.run(["pkexec", "/usr/bin/python3", "-I", str(helper), "--enable", "--restart"],
                            capture_output=True, text=True)
    if result.returncode in (126, 127):
        raise RuntimeError("系统未授权修改蓝牙配置。")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "BlueZ configuration failed")
    deadline = time.monotonic() + 15
    while True:
        try:
            identity = host_modalias(mac)
        except (ValueError, RuntimeError):
            identity = ""
        if identity.lower() == APPLE_MODALIAS:
            return {"changed": True, "host_modalias": identity, "configuration": result.stdout.strip()}
        if time.monotonic() >= deadline:
            raise RuntimeError("配置已写入，尚未检测到已配对耳机所在适配器启用 Apple DeviceID。")
        time.sleep(0.25)
