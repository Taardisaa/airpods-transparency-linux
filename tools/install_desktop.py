#!/usr/bin/env python3
"""Register the installed Python environment in this user's Linux app menu."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APP_ID = "io.github.Taardisaa.AirPodsControl"


def quote_exec(value):
    value = str(value).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def main():
    import airpods_transparency
    package = Path(airpods_transparency.__file__).parent
    icon = package / "assets/airpods-control.svg"
    if not icon.is_file():
        raise SystemExit("App icon is missing; reinstall the package first")
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    applications = data / "applications"
    icons = data / "icons/hicolor/scalable/apps"
    applications.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon, icons / f"{APP_ID}.svg")
    desktop = applications / f"{APP_ID}.desktop"
    contents = "\n".join([
        "[Desktop Entry]", "Type=Application", "Version=1.0",
        "Name=AirPods Control", "Name[zh_CN]=AirPods 控制中心",
        "Comment=Battery, listening modes and sound settings for AirPods",
        f"Exec={quote_exec(sys.executable)} -m airpods_transparency gui",
        f"Icon={APP_ID}", "Terminal=false", "Categories=AudioVideo;Audio;Settings;",
        "Keywords=AirPods;Bluetooth;Headphones;Noise;Transparency;",
        f"StartupWMClass={APP_ID}", "StartupNotify=true", "",
    ])
    fd, temporary = tempfile.mkstemp(suffix=".desktop", dir=applications)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(contents)
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", temporary], check=True)
        os.chmod(temporary, 0o644)
        os.replace(temporary, desktop)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(applications)], check=True)
    if shutil.which("gtk-update-icon-cache"):
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(data / "icons/hicolor")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Registered AirPods Control: {desktop}")


if __name__ == "__main__":
    main()
