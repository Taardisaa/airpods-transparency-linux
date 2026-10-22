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
