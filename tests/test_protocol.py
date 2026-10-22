import math
import struct

import pytest

from airpods_transparency.controller import Controller, load_backup, save_backup
from airpods_transparency.settings import Settings
from airpods_transparency.transport import ATTClient, ATTError, ProtocolError, normalize_mac

MAC = "AA:BB:CC:DD:EE:FF"


def payload(size=104):
    data = bytearray(size)
    struct.pack_into("<f", data, 0, 1)
    for i in range(8):
        struct.pack_into("<f", data, 4 + i * 4, i * 1.125)
        struct.pack_into("<f", data, 52 + i * 4, i * 0.75)
    return bytes(data)


@pytest.mark.parametrize("size", [100, 104])
def test_patch_preserves_every_unedited_byte(size):
    raw = payload(size)
    result = Settings(raw).edited({"left_amplification": -0.4, "enabled": False})
    assert result.values()["left_amplification"] == pytest.approx(-0.4)
    expected = bytearray(raw)
    struct.pack_into("<f", expected, 0, 0)
    struct.pack_into("<f", expected, 36, -0.4)
    assert result.raw == bytes(expected)
    assert ("own_voice" in result.values()) == (size == 104)


@pytest.mark.parametrize("size", [0, 22, 99, 101, 108])
def test_reject_unknown_layout(size):
    with pytest.raises(ValueError):
        Settings(bytes(size))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 1.01, -1.01])
def test_invalid_edit(value):
    with pytest.raises(ValueError):
        Settings(payload()).edited({"left_amplification": value})


def test_nonfinite_raw_and_invalid_bool():
    for v in (math.nan, 0.5):
        data = bytearray(payload())
        struct.pack_into("<f", data, 0, v)
        with pytest.raises(ValueError):
            Settings(bytes(data))
    with pytest.raises(ValueError):
        Settings(payload()).edited({"enabled": 1})
    with pytest.raises(ValueError):
        Settings(payload(100)).edited({"own_voice": 0.5})


class Socket:
    def __init__(self, incoming):
        self.incoming = iter(incoming)
        self.sent = []

    def settimeout(self, value):
        pass

    def send(self, value):
        self.sent.append(value)
        return len(value)

    def recv(self, size):
        return next(self.incoming)


def test_notifications_cannot_be_mistaken_for_read_response():
    sock = Socket([b"\x1b\x18\x00notification", b"\x1d\x18\x00indication", b"\x0b" + payload()])
    client = ATTClient(MAC, sock=sock)
    assert client.read() == payload()
    assert sock.sent == [b"\x0a\x18\x00", b"\x1e"]


def test_att_error_is_not_treated_as_a_payload():
    client = ATTClient(MAC, sock=Socket([bytes.fromhex("010a18000e")]))
    with pytest.raises(ATTError, match="0x0e") as exc:
        client.read()
    assert exc.value.code == 14


def test_error_for_another_handle_is_not_accepted():
    client = ATTClient(MAC, sock=Socket([bytes.fromhex("010a2a000e")]))
    with pytest.raises(ProtocolError, match="unrelated handle"):
        client.read()


@pytest.mark.parametrize("response", [b"", b"\x13", b"\x01\x0a", b"\x1b"])
def test_broken_responses_fail(response):
    client = ATTClient(MAC, sock=Socket([response]))
    with pytest.raises((ConnectionError, ProtocolError)):
        client.read()


def test_write_uses_transparency_handle_only():
    sock = Socket([b"\x13"])
    ATTClient(MAC, sock=sock).write(payload())
    assert sock.sent == [b"\x12\x18\x00" + payload()]


def test_backup_roundtrip_and_device_binding(tmp_path):
    settings = Settings(payload())
    path = save_backup(MAC, settings, tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_backup(path, MAC) == settings
    with pytest.raises(ValueError, match="different"):
        load_backup(path, "11:22:33:44:55:66")


class FakeClient:
    def __init__(self, raw, backup_dir, mismatch=False, fail=False):
        self.raw = raw
        self.backup_dir = backup_dir
        self.mismatch = mismatch
        self.fail = fail
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.raw

    def write(self, raw):
        # Backup must be durable before the first device write.
        assert list(self.backup_dir.glob("*.json"))
        self.writes.append(raw)
        if self.fail:
            raise TimeoutError("device timed out")
        if not self.mismatch:
            self.raw = raw


def test_apply_backup_and_restore(tmp_path):
    original = payload()
    client = FakeClient(original, tmp_path)
    controller = Controller(MAC, tmp_path, factory=lambda mac: client)
    observed, backup = controller.apply({"left_amplification": -0.2})
    assert observed.values()["left_amplification"] == pytest.approx(-0.2)
    assert load_backup(backup, MAC).raw == original
    restored, second_backup = controller.apply(restore=backup)
    assert restored.raw == original
    assert second_backup != backup
    assert load_backup(second_backup, MAC) == observed


@pytest.mark.parametrize("mismatch,fail", [(True, False), (False, True)])
def test_failed_verification_keeps_backup(tmp_path, mismatch, fail):
    client = FakeClient(payload(), tmp_path, mismatch=mismatch, fail=fail)
    controller = Controller(MAC, tmp_path, factory=lambda mac: client)
    with pytest.raises(RuntimeError, match="Original backup"):
        controller.apply({"enabled": False})
    assert len(client.writes) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_noop_and_stale_gui_never_write(tmp_path):
    client = FakeClient(payload(), tmp_path)
    controller = Controller(MAC, tmp_path, factory=lambda mac: client)
    assert controller.apply({"enabled": True})[1] is None
    stale = Settings(payload()).edited({"enabled": False})
    with pytest.raises(RuntimeError, match="changed since"):
        controller.apply({"left_tone": 0.1}, expected=stale)
    assert not client.writes
    assert not list(tmp_path.glob("*.json"))


def test_address_validation():
    assert normalize_mac(MAC.lower()) == MAC
    for bad in ("--help", "AA:BB", MAC + "; cmd", "AA-BB-CC-DD-EE-FF"):
        with pytest.raises(ValueError):
            normalize_mac(bad)
