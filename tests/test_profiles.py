import json

import pytest

from airpods_transparency.profiles import ProfileStore, validate_profile


def test_preset_roundtrip_delete_and_permissions(tmp_path):
    store = ProfileStore(tmp_path)
    profile = store.save("办公室", {"enabled": True, "left_amplification": -0.2}, "transparency")
    assert store.get("办公室") == profile
    assert (tmp_path / "presets.json").stat().st_mode & 0o777 == 0o600
    assert "mac" not in json.dumps(store.list())
    store.delete("办公室")
    assert store.list() == {}
    with pytest.raises(ValueError):
        store.get("办公室")


@pytest.mark.parametrize("profile", [None, {"schema": 2}, {"schema": 1, "values": {}},
    {"schema": 1, "values": {"enabled": 1}},
    {"schema": 1, "values": {"left_tone": 3}},
    {"schema": 1, "values": {"enabled": True}, "mode": "invalid"}])
def test_bad_profiles_rejected(profile):
    with pytest.raises(ValueError):
        validate_profile(profile)


def test_failed_preset_does_not_damage_existing_file(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("good", {"enabled": True})
    before = (tmp_path / "presets.json").read_bytes()
    with pytest.raises(ValueError):
        store.save("bad", {"left_tone": float("nan")})
    assert (tmp_path / "presets.json").read_bytes() == before
