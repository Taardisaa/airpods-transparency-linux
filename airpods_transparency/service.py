"""Single-user JSON-lines IPC service owning the persistent AACP connection."""
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time

from .aacp import AACPClient
from .controller import Controller, save_backup
from .profiles import ProfileStore
from .settings import Settings
from .transport import normalize_mac

MAX_REQUEST = 65536
logger = logging.getLogger("airpods-control")


def log_path():
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "airpods-transparency"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / "service.log"


def socket_path():
    root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/airpods-user-{os.getuid()}"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root_info = root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
        raise PermissionError("Runtime root must be owned by this user with mode 0700")
    directory = root / "airpods-transparency"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if directory.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("Service directory must be owned by this user with mode 0700")
    return directory / "control.sock"


class Session:
    def __init__(self, mac):
        self.mac = mac
        self.client = AACPClient(mac)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        delay = 1
        while not self.stop.is_set():
            try:
                with self.lock:
                    if not self.client.sock:
                        # Avoid initiating a Bluetooth connection or stealing audio
                        # from a phone. AACP is opened only for an existing link.
                        from .bluez import bus_call
                        objects = bus_call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
                        suffix = "/dev_" + self.mac.replace(":", "_")
                        connected = any(p.endswith(suffix) and
                            interfaces.get("org.bluez.Device1", {}).get("Connected", {}).get("data")
                            for p, interfaces in objects["data"][0].items())
                        if not connected:
                            raise ConnectionError("AirPods are not connected in BlueZ")
                        self.client.connect()
                        self.client.pump(1, lambda: bool(self.client.state["battery"]) and
                                         self.client.state["mode"] is not None)
                    self.client.pump(0.2)
                    self.error = None
                    delay = 1
                    self.ready.set()
                self.stop.wait(0.05)
            except Exception as exc:
                with self.lock:
                    self.client.close()
                    if self.error != str(exc):
                        logger.warning("Control connection unavailable: %s", exc)
                    self.error = str(exc)
                    self.ready.set()
                self.stop.wait(delay)
                delay = min(delay * 2, 30)
        with self.lock:
            self.client.close()

    def snapshot(self):
        self.ready.wait(6)
        with self.lock:
            state = self.client.snapshot()
            state.update(mac=self.mac, error=self.error)
            return state

    def mode(self, mode):
        self.ready.wait(6)
        with self.lock:
            return self.client.set_mode(mode)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=6)


class Engine:
    def __init__(self, profile_store=None):
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.settings_lock = threading.Lock()
        self.profiles = profile_store or ProfileStore()

    def session(self, mac):
        with self.session_lock:
            if mac not in self.sessions:
                self.sessions[mac] = Session(mac)
            return self.sessions[mac]

    def dispatch(self, request):
        action = request.get("action")
        if action == "ping":
            from . import __version__
            return {"protocol_version": 1, "version": __version__, "pid": os.getpid(), "log": str(log_path())}
        if action == "preset_list":
            return {"presets": self.profiles.list()}
        if action == "preset_delete":
            self.profiles.delete(request["name"])
            return {"deleted": request["name"]}
        mac = normalize_mac(request.get("mac", ""))
        if action == "status":
            return self.session(mac).snapshot()
        if action == "mode":
            return self.session(mac).mode(request["mode"])
        with self.settings_lock:
            controller = Controller(mac, transport=request.get("transport", "auto"))
            if action == "read":
                return {"raw_hex": controller.read().raw.hex(), "backend": controller.backend}
            if action == "backup":
                return {"backup": str(save_backup(mac, controller.read()))}
            if action == "preset_save":
                state = self.session(mac).snapshot()
                values = controller.read().values()
                return {"preset": self.profiles.save(request["name"], values,
                            state["mode"] if request.get("include_mode", True) and state["connected"] else None)}
            if action in ("apply", "preset_apply"):
                profile = self.profiles.get(request["name"]) if action == "preset_apply" else None
                observed, backup = controller.apply(
                    changes=profile["values"] if profile else request.get("changes"),
                    restore=request.get("restore"),
                    expected=Settings(bytes.fromhex(request["expected"])) if request.get("expected") else None)
                result = {"raw_hex": observed.raw.hex(), "backup": str(backup) if backup else None,
                          "backend": controller.backend}
                if profile and profile.get("mode"):
                    try:
                        self.session(mac).mode(profile["mode"])
                    except Exception as exc:
                        raise RuntimeError(f"Transparency settings applied, but mode switch failed: {exc}. "
                                           f"Original settings backup: {backup}") from exc
                return result
        raise ValueError(f"Unsupported service action: {action}")

    def close(self):
        for session in self.sessions.values():
            session.close()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, path, engine):
        self.engine = engine
        super().__init__(str(path), Handler)
        os.chmod(path, 0o600)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        credentials = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        if struct.unpack("3i", credentials)[1] != os.getuid():
            return
        self.request.settimeout(20)
        try:
            line = self.rfile.readline(MAX_REQUEST + 1)
            if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                raise ValueError("Invalid or oversized service request")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Service request must be an object")
            if request.get("action") == "shutdown":
                result = {"stopped": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                result = self.server.engine.dispatch(request)
            response = {"ok": True, **result}
        except Exception as exc:
            logger.warning("Request failed: %s", exc)
            response = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc),
                        "errno": getattr(exc, "errno", None), "att_code": getattr(exc, "code", None)}}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve():
    path = socket_path()
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        path.unlink(missing_ok=True)
        os.umask(0o077)
        handler = RotatingFileHandler(log_path(), maxBytes=1024 * 1024, backupCount=2)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.info("Service started")
        engine = Engine()
        try:
            with Server(path, engine) as server:
                server.serve_forever(poll_interval=0.2)
        finally:
            engine.close()
            path.unlink(missing_ok=True)
            logger.info("Service stopped")
            handler.close()


def rpc(action, mac=None, start=True, **params):
    path = socket_path()
    request = {"action": action, **params}
    if mac:
        request["mac"] = normalize_mac(mac)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(45)
    try:
        try:
            client.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            if not start:
                raise ConnectionError("AirPods background service is not running")
            subprocess.Popen([sys.executable, "-m", "airpods_transparency.service"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            deadline = time.monotonic() + 5
            while True:
                try:
                    client.connect(str(path))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() > deadline:
                        raise ConnectionError(f"AirPods service did not start; see {log_path()}")
                    time.sleep(0.05)
        credentials = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        if struct.unpack("3i", credentials)[1] != os.getuid():
            raise PermissionError("Unexpected service owner")
        # Never retry a request after any bytes have been sent.
        client.sendall(json.dumps(request).encode() + b"\n")
        with client.makefile("rb") as stream:
            line = stream.readline(MAX_REQUEST + 1)
        if not line or len(line) > MAX_REQUEST:
            raise RuntimeError("Invalid service response")
        result = json.loads(line)
        if not result.get("ok"):
            error = RuntimeError(result["error"]["message"])
            error.remote_error = result["error"]
            raise error
        return result
    finally:
        client.close()


class RemoteController:
    def __init__(self, mac, transport="auto"):
        self.mac = normalize_mac(mac)
        self.transport = transport
        self.backend = None

    def read(self):
        result = rpc("read", self.mac, transport=self.transport)
        self.backend = result["backend"]
        return Settings(bytes.fromhex(result["raw_hex"]))

    def apply(self, changes=None, restore=None, expected=None):
        result = rpc("apply", self.mac, transport=self.transport, changes=changes,
                     restore=str(restore) if restore else None, expected=expected.raw.hex() if expected else None)
        self.backend = result["backend"]
        return Settings(bytes.fromhex(result["raw_hex"])), Path(result["backup"]) if result["backup"] else None


if __name__ == "__main__":
    serve()
