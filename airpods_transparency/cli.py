import argparse
import json
import subprocess
import sys
from pathlib import Path

from .controller import Controller, save_backup
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


def parser():
    p = argparse.ArgumentParser(description="AirPods custom transparency on Linux")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="List known AirPods from BlueZ")
    gui = commands.add_parser("gui", help="Open desktop controls")
    gui.add_argument("--mac", type=normalize_mac)
    gui.add_argument("--demo", action="store_true", help="Simulated device; no Bluetooth access")
    for name in ("doctor", "read", "set", "backup", "restore"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--mac", required=True, type=normalize_mac)
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


def doctor(mac):
    info = subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True,
                          timeout=5, check=True).stdout
    for line in info.splitlines():
        if any(label in line for label in ("Name:", "Paired:", "Connected:", "Trusted:")):
            print(line.strip())
    conf = Path("/etc/bluetooth/main.conf")
    try:
        configured = any(line.split("#", 1)[0].replace(" ", "").strip().lower()
                         == "deviceid=bluetooth:004c:0000:0000" for line in conf.read_text().splitlines())
        print("Apple DeviceID in main.conf:", "configured" if configured else "not configured")
    except OSError as exc:
        print("Cannot inspect main.conf:", exc)
    print("Testing read-only transparency access…")
    print(json.dumps(Controller(mac).read().values(), indent=2))
    print("Read succeeded. Write access and audible effect have not been tested.")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "gui":
            from .gui import launch
            return launch(args.mac, args.demo)
        if args.command == "devices":
            for mac, name in devices():
                print(mac, name)
            return 0
        if args.command == "doctor":
            doctor(args.mac)
            return 0
        controller = Controller(args.mac)
        if args.command == "read":
            print(json.dumps(controller.read().values(), indent=2))
        elif args.command == "backup":
            print(save_backup(args.mac, controller.read()))
        else:
            settings, backup = controller.apply(
                changes=collect_changes(args) if args.command == "set" else None,
                restore=args.file if args.command == "restore" else None)
            print(json.dumps({"verified": True, "backup": str(backup) if backup else None,
                              "values": settings.values()}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"GUI dependency unavailable: {exc}. Install with: pip install '.[gui]'", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
