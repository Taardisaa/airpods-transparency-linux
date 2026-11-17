import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("configure", Path(__file__).parents[1] / "tools/configure_bluez.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


def test_config_edit_is_scoped_and_idempotent():
    original = "# comment\n[General]\n#DeviceID = old\nDeviceID = old\nDeviceID = duplicate\nName = PC\n[Policy]\nDeviceID = unrelated\n"
    edited = config.edit(original)
    assert "DeviceID = old\n" not in edited.replace("#DeviceID = old\n", "")
    assert "#DeviceID = old\n" in edited
    assert "[Policy]\nDeviceID = unrelated\n" in edited
    assert edited.count(config.VALUE) == 1
    assert config.edit(edited) == edited


def test_missing_section_and_missing_key():
    assert config.VALUE in config.edit("[Policy]\nAutoEnable=true")
    assert config.edit("[General]\n[Policy]\n").index(config.VALUE) < config.edit("[General]\n[Policy]\n").index("[Policy]")


def test_disable_removes_only_our_override_and_preserves_other_configuration():
    original = '[General]\nName = PC\nDeviceID = bluetooth:004C:0000:0000\n[Policy]\nDeviceID = bluetooth:004C:0000:0000\n'
    expected = '[General]\nName = PC\n[Policy]\nDeviceID = bluetooth:004C:0000:0000\n'
    assert config.disable(original) == expected
    assert config.disable(expected) == expected
    custom = '[General]\nDeviceID = usb:1234:5678:0000\n'
    assert config.disable(custom) == custom


def test_restart_failure_restores_original_config(monkeypatch, tmp_path):
    import subprocess
    import sys
    import pytest

    path = tmp_path / "main.conf"
    original = b"[General]\nName = Studio\n"
    path.write_bytes(original)
    monkeypatch.setattr(config, "CONFIG", path)
    monkeypatch.setattr(config.os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["configure_bluez", "--enable", "--restart"])
    observed = []
    def restart():
        observed.append(path.read_bytes())
        if len(observed) == 1:
            raise subprocess.CalledProcessError(1, ["systemctl"])
    monkeypatch.setattr(config, "restart", restart)
    with pytest.raises(subprocess.CalledProcessError):
        config.main()
    assert observed == [config.edit(original.decode()).encode(), original]
    assert path.read_bytes() == original
    backup, = tmp_path.glob("main.conf.airpods-*")
    assert backup.read_bytes() == original
