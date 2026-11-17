from types import SimpleNamespace

import pytest

from airpods_transparency import setup

MAC = "00:11:22:33:44:55"
LINUX = "usb:v1D6Bp0246d0540"


def test_active_identity_needs_no_authorization(monkeypatch):
    monkeypatch.setattr(setup, "configured_identity", lambda: True)
    monkeypatch.setattr(setup, "host_modalias", lambda mac: setup.APPLE_MODALIAS.upper())
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected pkexec"))
    assert not setup.enable_advanced(MAC)["changed"]


def test_enable_waits_for_live_adapter(monkeypatch):
    monkeypatch.setattr(setup, "configured_identity", lambda: False)
    identities = iter([LINUX, "", setup.APPLE_MODALIAS])
    monkeypatch.setattr(setup, "host_modalias", lambda mac: next(identities))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="backup")
    monkeypatch.setattr(setup.subprocess, "run", run)
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    assert setup.enable_advanced(MAC)["changed"]
    assert len(calls) == 1
    assert calls[0][-2:] == ["--enable", "--restart"]


def test_cancelled_auth_is_reported(monkeypatch):
    monkeypatch.setattr(setup, "host_modalias", lambda mac: LINUX)
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=126))
    with pytest.raises(RuntimeError, match="未授权"):
        setup.enable_advanced(MAC)
