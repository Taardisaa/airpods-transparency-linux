import json
import socket
import threading

import pytest

from airpods_transparency import service


@pytest.fixture
def ipc(tmp_path, monkeypatch):
    class FakeEngine:
        def dispatch(self, request):
            if request["action"] == "fail":
                raise ConnectionRefusedError(111, "refused")
            return {"action": request["action"]}
    path = tmp_path / "control.sock"
    server = service.Server(path, FakeEngine())
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    monkeypatch.setattr(service, "socket_path", lambda: path)
    yield path
    server.shutdown()
    server.server_close()
    thread.join()


def test_ipc_success_structured_error_and_private_socket(ipc):
    assert ipc.stat().st_mode & 0o777 == 0o600
    assert service.rpc("status", start=False) == {"ok": True, "action": "status"}
    with pytest.raises(RuntimeError) as result:
        service.rpc("fail", start=False)
    assert result.value.remote_error["errno"] == 111


def test_bad_ipc_is_rejected(ipc):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(ipc))
        client.sendall(b"[]\n")
        result = json.loads(client.recv(4096))
        assert result["ok"] is False


def test_preset_listing_needs_no_bluetooth(tmp_path):
    from airpods_transparency.profiles import ProfileStore
    engine = service.Engine(profile_store=ProfileStore(tmp_path))
    assert engine.dispatch({"action": "preset_list"}) == {"presets": {}}
    assert engine.sessions == {}


def test_socket_directory_rejects_public_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    directory = tmp_path / "airpods-transparency"
    directory.mkdir(mode=0o755)
    with pytest.raises(PermissionError):
        service.socket_path()


def test_preset_reports_partial_failure_with_backup(tmp_path, monkeypatch):
    import struct
    from airpods_transparency.settings import Settings
    from airpods_transparency.profiles import ProfileStore
    class FakeController:
        backend = "fake"
        def __init__(self, *args, **kwargs):
            pass
        def apply(self, **kwargs):
            assert kwargs["changes"] == {"enabled": True}
            return Settings(struct.pack("<26f", *([1] + [0] * 25))), Path("original.json")
    from pathlib import Path
    class FakeSession:
        def mode(self, mode):
            raise RuntimeError("not acknowledged")
    store = ProfileStore(tmp_path)
    store.save("Office", {"enabled": True}, "anc")
    engine = service.Engine(profile_store=store)
    monkeypatch.setattr(service, "Controller", FakeController)
    monkeypatch.setattr(engine, "session", lambda mac: FakeSession())
    with pytest.raises(RuntimeError, match="Transparency settings applied.*original.json"):
        engine.dispatch({"action": "preset_apply", "mac": "AA:BB:CC:DD:EE:FF", "name": "Office"})
