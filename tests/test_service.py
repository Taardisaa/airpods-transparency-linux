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


def test_reconnect_closes_control_session_before_reset_and_does_not_write(tmp_path, monkeypatch):
    from airpods_transparency import bluez
    from airpods_transparency.profiles import ProfileStore
    calls = []
    mac = "AA:BB:CC:DD:EE:FF"
    class Session:
        def close(self):
            calls.append("close")
    engine = service.Engine(profile_store=ProfileStore(tmp_path))
    engine.sessions[mac] = Session()
    def audio(address, rebuild_settings):
        assert rebuild_settings
        calls.append(address)
        return {"bluetooth_connected": True}
    monkeypatch.setattr(bluez, "reconnect_audio", audio)
    def handoff(address):
        import struct
        from airpods_transparency.settings import Settings
        calls.append("handoff")
        return Settings(struct.pack("<26f", *([1] + [0] * 25)))
    monkeypatch.setattr(engine, "handoff_settings", handoff)
    monkeypatch.setattr(service, "Controller", lambda *args, **kwargs: pytest.fail("must not write settings"))
    result = engine.dispatch({"action": "reconnect", "mac": mac, "rebuild_settings": True})
    assert result["bluetooth_connected"] and result["settings_ready"]
    assert result["backend"] == "bluez" and len(bytes.fromhex(result["raw_hex"])) == 104
    assert calls == ["close", mac, "handoff"]
    assert not engine.sessions


def test_direct_settings_channel_survives_requests_and_is_dropped_on_failure(tmp_path, monkeypatch):
    import struct
    from airpods_transparency import bluez
    from airpods_transparency.profiles import ProfileStore
    raw = struct.pack("<26f", *([1] + [0] * 25))
    opened, closed = [], []
    class Client:
        backend = "att"
        failed = False
        def __init__(self, *args):
            opened.append(self)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            closed.append(self)
        def read(self):
            if self.failed:
                raise ConnectionResetError("link dropped")
            return raw
    monkeypatch.setattr(bluez, "SettingsClient", Client)
    monkeypatch.setattr(bluez, "device_connected", lambda mac: True)
    engine = service.Engine(profile_store=ProfileStore(tmp_path))
    request = {"action": "read", "mac": "AA:BB:CC:DD:EE:FF"}
    assert engine.dispatch(request)["raw_hex"] == raw.hex()
    assert engine.dispatch(request)["raw_hex"] == raw.hex()
    assert len(opened) == 1 and not closed
    opened[0].failed = True
    with pytest.raises(ConnectionResetError):
        engine.dispatch(request)
    assert closed == opened and not engine.settings_clients
    engine.dispatch(request)
    assert len(opened) == 2
    engine.close()
    assert closed == opened


def test_offline_read_never_opens_bluetooth_connection(tmp_path, monkeypatch):
    from airpods_transparency import bluez
    from airpods_transparency.profiles import ProfileStore
    monkeypatch.setattr(bluez, "device_connected", lambda mac: False)
    monkeypatch.setattr(bluez, "SettingsClient", lambda *args: pytest.fail("must not connect offline device"))
    engine = service.Engine(profile_store=ProfileStore(tmp_path))
    with pytest.raises(ConnectionError, match="disconnected"):
        engine.dispatch({"action": "read", "mac": "AA:BB:CC:DD:EE:FF"})


def test_reconnect_default_linux_host_leaves_advanced_channel_closed(monkeypatch):
    from airpods_transparency import bluez
    engine = service.Engine()
    def healthy(mac, rebuild_settings):
        assert not rebuild_settings
        return {"bluetooth_connected": True, "linux_device_id": True}
    monkeypatch.setattr(bluez, "reconnect_audio", healthy)
    monkeypatch.setattr(engine, "settings_channel", lambda *args: pytest.fail("unexpected ATT probe"))
    result = engine.dispatch({"action": "reconnect", "mac": "AA:BB:CC:DD:EE:FF"})
    assert result["settings_ready"] is None
    assert result["settings_skipped"] == "linux_device_id"


def test_handoff_releases_raw_before_bluez_read_and_reuses_bluez(monkeypatch):
    import struct
    from airpods_transparency import bluez
    mac = "AA:BB:CC:DD:EE:FF"
    events = []
    class Client:
        def __init__(self, address, transport):
            self.backend = transport
        def __enter__(self):
            events.append(("open", self.backend))
            return self
        def read(self):
            events.append(("read", self.backend))
            return struct.pack("<26f", *([1] + [0] * 25))
        def __exit__(self, *args):
            events.append(("close", self.backend))
    monkeypatch.setattr(bluez, "SettingsClient", Client)
    monkeypatch.setattr(bluez, "device_connected", lambda address: True)
    engine = service.Engine()
    initial = engine.handoff_settings(mac)
    assert engine.dispatch({"action": "read", "mac": mac})["raw_hex"] == initial.raw.hex()
    engine.close()
    assert events == [("open", "att"), ("read", "att"), ("close", "att"),
                      ("open", "bluez"), ("read", "bluez"), ("read", "bluez"), ("close", "bluez")]
