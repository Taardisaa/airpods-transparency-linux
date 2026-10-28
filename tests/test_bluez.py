import json
import struct
from types import SimpleNamespace

import pytest

from airpods_transparency import bluez

MAC = "AA:BB:CC:DD:EE:FF"
PATH = "/org/bluez/hci1/dev_AA_BB_CC_DD_EE_FF/service0012/char0017"
RAW = struct.pack("<26f", *([1] + [0] * 25))


def objects(path=PATH, uuid=bluez.TRANSPARENCY_UUID):
    return {"data": [{path: {bluez.CHARACTERISTIC: {
        "UUID": {"data": uuid}, "Flags": {"data": ["read", "write-without-response"]},
        "Value": {"data": [99]},  # Must not use this cached value.
    }}}]}


def test_discover_by_device_and_uuid_not_path_number(monkeypatch):
    monkeypatch.setattr(bluez, "bus_call", lambda *args: objects())
    assert bluez.find_transparency(MAC)[0] == PATH
    assert bluez.find_transparency("11:22:33:44:55:66") is None
    monkeypatch.setattr(bluez, "bus_call", lambda *args: objects(uuid="wrong"))
    assert bluez.find_transparency(MAC) is None


def test_auto_prefers_existing_bluez_connection_and_no_cached_read(monkeypatch):
    calls = []
    def call(path, interface, method, *args):
        calls.append((path, method, args))
        return objects() if method == "GetManagedObjects" else {"type": "ay", "data": [list(RAW)]}
    monkeypatch.setattr(bluez, "bus_call", call)
    with bluez.SettingsClient(MAC) as client:
        assert client.backend == "bluez"
        assert client.read() == RAW
    assert calls[-1] == (PATH, "ReadValue", ("a{sv}", 0))


@pytest.mark.parametrize("flags,kind", [(["write"], "request"), (["write-without-response"], "command")])
def test_bluez_write_respects_characteristic_properties(monkeypatch, flags, kind):
    calls = []
    monkeypatch.setattr(bluez, "bus_call", lambda *args: calls.append(args))
    with bluez.BlueZClient(MAC, (PATH, flags)) as client:
        client.write(RAW)
    args = calls[0]
    assert args[:5] == (PATH, bluez.CHARACTERISTIC, "WriteValue", "aya{sv}", 104)
    assert bytes(args[5:109]) == RAW
    assert args[109:] == (1, "type", "s", kind)


def test_read_only_characteristic_never_written(monkeypatch):
    monkeypatch.setattr(bluez, "bus_call", lambda *args: pytest.fail("must not write"))
    with bluez.BlueZClient(MAC, (PATH, ["read"])) as client:
        with pytest.raises(RuntimeError, match="not writable"):
            client.write(RAW)


def test_att_fallback_and_discovery_race(monkeypatch):
    discovered = iter([None, (PATH, ["read"])])
    monkeypatch.setattr(bluez, "find_transparency", lambda mac: next(discovered))
    class Refused:
        def __init__(self, mac):
            pass
        def __enter__(self):
            raise ConnectionRefusedError(111, "refused")
    monkeypatch.setattr(bluez, "ATTClient", Refused)
    with bluez.SettingsClient(MAC) as client:
        assert client.backend == "bluez"


def test_busctl_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(bluez.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stderr="org.bluez.Error.NotAuthorized", stdout=""))
    with pytest.raises(RuntimeError, match="NotAuthorized"):
        bluez.bus_call(PATH, bluez.CHARACTERISTIC, "ReadValue")


def test_multiple_adapters_fail_closed(monkeypatch):
    response = objects()
    response["data"][0].update(objects(PATH.replace("hci1", "hci0"))["data"][0])
    monkeypatch.setattr(bluez, "bus_call", lambda *args: response)
    with pytest.raises(ValueError, match="Multiple"):
        with bluez.SettingsClient(MAC):
            pytest.fail("should not select an arbitrary adapter")


def test_global_bluez_failure_does_not_hide_selected_device(monkeypatch):
    device = PATH.split('/service')[0]
    service = PATH.rsplit('/', 1)[0]
    tree = {'/org/bluez': ['/org/bluez/hci1'], '/org/bluez/hci1': [device],
            device: [service], service: [PATH]}
    def fail(*args):
        raise RuntimeError('GetManagedObjects failed with no stderr')
    calls = []
    def prop(path, interface, name):
        calls.append((path, name))
        return {'Connected': True, 'UUID': bluez.TRANSPARENCY_UUID,
                'Flags': ['read', 'write-without-response']}[name]
    monkeypatch.setattr(bluez, 'bus_call', fail)
    monkeypatch.setattr(bluez, 'child_paths', lambda path: tree[path])
    monkeypatch.setattr(bluez, 'property_value', prop)
    assert bluez.device_connected(MAC) is True
    assert bluez.find_transparency(MAC) == (PATH, ['read', 'write-without-response'])
    assert calls == [(device, 'Connected'), (PATH, 'UUID'), (PATH, 'Flags')]


def test_targeted_connection_check_reports_disconnected(monkeypatch):
    monkeypatch.setattr(bluez, 'device_paths', lambda mac: [PATH.split('/service')[0]])
    monkeypatch.setattr(bluez, 'property_value', lambda *args: False)
    assert bluez.device_connected(MAC) is False
