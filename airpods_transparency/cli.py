import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from .controller import save_backup
from .service import RemoteController as Controller, rpc
from .transport import normalize_mac


def devices():
    result = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True,
                            timeout=5, check=True)
    found = []
    for line in result.stdout.splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == "Device" and "airpods" in parts[2].lower():
            found.append((normalize_mac(parts[1]), parts[2]))
    return found


def boolean(value):
    return value == "on"


def parser(json_errors=False):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            if json_errors:
                raise ValueError(message)
            super().error(message)

    p = Parser(description="AirPods custom transparency on Linux")
    p.add_argument("--json", action="store_true", help="Structured JSON success and error output")
    commands = p.add_subparsers(dest="command", required=True)
    dev = commands.add_parser("devices", help="List known AirPods from BlueZ")
    dev.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    gui = commands.add_parser("gui", help="Open desktop controls")
    gui.add_argument("--mac", type=normalize_mac)
    gui.add_argument("--demo", action="store_true", help="Simulated device; no Bluetooth access")
    gui.add_argument("--transport", choices=["auto", "bluez", "att"], default="auto")
    gui.add_argument("--tray", action="store_true", help="Start minimized to the tray when available")
    for name in ("status", "watch", "mode"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--mac", required=True, type=normalize_mac)
        cmd.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "mode":
            cmd.add_argument("value", choices=["off", "anc", "transparency", "adaptive"])
        if name == "watch":
            cmd.add_argument("--interval", type=float, default=2)
    preset = commands.add_parser("preset")
    preset.add_argument("operation", choices=["list", "save", "apply", "delete"])
    preset.add_argument("name", nargs="?")
    preset.add_argument("--mac", type=normalize_mac)
    preset.add_argument("--without-mode", action="store_true")
    preset.add_argument("--transport", choices=["auto", "bluez", "att"], default="auto")
    preset.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    service = commands.add_parser("service")
    service.add_argument("operation", choices=["start", "status", "stop"])
    service.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    for name in ("doctor", "read", "set", "backup", "restore"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--mac", required=True, type=normalize_mac)
        cmd.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        cmd.add_argument("--transport", choices=["auto", "bluez", "att"], default="auto")
        if name == "set":
            for field in ("amplification", "tone", "noise-reduction", "conversation-boost"):
                args = {"type": float}
                if field == "conversation-boost":
                    args = {"choices": ["on", "off"]}
                cmd.add_argument(f"--{field}", **args)
                for ear in ("left", "right"):
                    cmd.add_argument(f"--{ear}-{field}", **args)
            cmd.add_argument("--enabled", choices=["on", "off"])
            cmd.add_argument("--own-voice", type=float)
        if name == "restore":
            cmd.add_argument("file", type=Path)
    return p


def collect_changes(args):
    changes = {}
    for field in ("amplification", "tone", "noise_reduction", "conversation_boost"):
        common = getattr(args, field)
        for ear in ("left", "right"):
            v = getattr(args, f"{ear}_{field}")
            v = common if v is None else v
            if v is not None:
                changes[f"{ear}_{field}"] = boolean(v) if field == "conversation_boost" else v
    if args.enabled is not None:
        changes["enabled"] = boolean(args.enabled)
    if args.own_voice is not None:
        changes["own_voice"] = args.own_voice
    if not changes:
        raise ValueError("Specify at least one setting; use 'read' to inspect the device")
    return changes


def doctor(mac, transport="auto"):
    info = subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True,
                          timeout=5, check=True).stdout
    diagnostics = {"mac": mac, "requested_transport": transport}
    diagnostics["device"] = {line.strip().split(":", 1)[0].lower(): line.split(":", 1)[1].strip()
                             for line in info.splitlines()
                             if any(label in line for label in ("Name:", "Paired:", "Connected:", "Trusted:"))}
    conf = Path("/etc/bluetooth/main.conf")
    try:
        configured = any(line.split("#", 1)[0].replace(" ", "").strip().lower()
                         == "deviceid=bluetooth:004c:0000:0000" for line in conf.read_text().splitlines())
        diagnostics["apple_device_id_configured"] = configured
    except OSError as exc:
        diagnostics["configuration_error"] = str(exc)
    controller = Controller(mac, transport=transport)
    try:
        diagnostics["service"] = rpc("ping")
        diagnostics["values"] = controller.read().values()
        diagnostics["backend"] = controller.backend
        diagnostics["control"] = rpc("status", mac)
    except Exception as exc:
        # Carry completed diagnostics into the structured error response.
        exc.diagnostics = diagnostics
        raise
    diagnostics["read_only"] = True
    return diagnostics


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = SimpleNamespace(json="--json" in argv)
    try:
        args = parser(json_errors=args.json).parse_args(argv)
        if args.command == "gui":
            from .gui import launch
            return launch(args.mac, args.demo, args.transport, args.tray)
        if args.command == "devices":
            found = devices()
            if args.json:
                print(json.dumps({"ok": True, "devices": [{"mac": mac, "name": name} for mac, name in found]}))
            else:
                for mac, name in found:
                    print(mac, name)
            return 0
        if args.command == "doctor":
            result = doctor(args.mac, args.transport)
            print(json.dumps({"ok": True, **result}, indent=2))
            return 0
        if args.command == "service":
            print(json.dumps(rpc("shutdown" if args.operation == "stop" else "ping",
                                 start=args.operation == "start"), indent=2))
            return 0
        if args.command in ("status", "mode"):
            print(json.dumps(rpc(args.command, args.mac, **({"mode": args.value} if args.command == "mode" else {})), indent=2))
            return 0
        if args.command == "watch":
            if not 0.2 <= args.interval <= 60:
                raise ValueError("Watch interval must be between 0.2 and 60 seconds")
            previous = None
            while True:
                result = rpc("status", args.mac)
                if result != previous:
                    print(json.dumps(result), flush=True)
                    previous = result
                time.sleep(args.interval)
        if args.command == "preset":
            if args.operation != "list" and not args.name:
                raise ValueError("Specify the preset name")
            if args.operation in ("save", "apply") and not args.mac:
                raise ValueError("Specify --mac to save or apply a device preset")
            result = rpc("preset_" + args.operation, args.mac, name=args.name,
                         include_mode=not args.without_mode, transport=args.transport)
            if "raw_hex" in result:
                from .settings import Settings
                result["values"] = Settings(bytes.fromhex(result.pop("raw_hex"))).values()
                result["verified"] = True
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        controller = Controller(args.mac, transport=args.transport)
        if args.command == "read":
            values = controller.read().values()
            print(json.dumps({"ok": True, "backend": controller.backend, "values": values}
                             if args.json else values, indent=2))
        elif args.command == "backup":
            backup = str(save_backup(args.mac, controller.read()))
            print(json.dumps({"ok": True, "backup": backup}) if args.json else backup)
        else:
            settings, backup = controller.apply(
                changes=collect_changes(args) if args.command == "set" else None,
                restore=args.file if args.command == "restore" else None)
            print(json.dumps({"ok": True, "backend": controller.backend,
                              "verified": True, "backup": str(backup) if backup else None,
                              "values": settings.values()}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": getattr(exc, "remote_error", {
                  "type": type(exc).__name__, "message": str(exc),
                  "errno": getattr(exc, "errno", None), "att_code": getattr(exc, "code", None)}),
                  "diagnostics": getattr(exc, "diagnostics", {})}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
