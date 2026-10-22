from airpods_transparency.cli import collect_changes, main, parser
import json


def test_common_and_per_ear_cli_precedence():
    args = parser().parse_args(["set", "--mac", "AA:BB:CC:DD:EE:FF",
                               "--amplification", "-0.2", "--right-amplification", "0.1",
                               "--enabled", "on", "--conversation-boost", "off"])
    assert collect_changes(args) == {
        "left_amplification": -0.2, "right_amplification": 0.1,
        "enabled": True, "left_conversation_boost": False, "right_conversation_boost": False,
    }


def test_transport_error_is_reported_without_traceback(monkeypatch, capsys):
    def fail(self):
        raise PermissionError("blocked")
    monkeypatch.setattr("airpods_transparency.cli.Controller.read", fail)
    assert main(["read", "--mac", "AA:BB:CC:DD:EE:FF"]) == 1
    assert "blocked" in capsys.readouterr().err


def test_json_error_and_bad_arguments(monkeypatch, capsys):
    def fail(self):
        raise ConnectionRefusedError(111, "refused")
    monkeypatch.setattr("airpods_transparency.cli.Controller.read", fail)
    assert main(["read", "--mac", "AA:BB:CC:DD:EE:FF", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"]["errno"] == 111
    assert main(["--json", "read", "--mac", "invalid"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_mode_command_uses_shared_service(monkeypatch, capsys):
    calls = []
    def rpc(action, mac, **params):
        calls.append((action, mac, params))
        return {"ok": True, "mode": params["mode"]}
    monkeypatch.setattr("airpods_transparency.cli.rpc", rpc)
    assert main(["mode", "anc", "--mac", "AA:BB:CC:DD:EE:FF", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "anc"
    assert calls == [("mode", "AA:BB:CC:DD:EE:FF", {"mode": "anc"})]


def test_preset_save_requires_device_and_watch_rejects_bad_interval(capsys):
    assert main(["preset", "save", "Office", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert main(["watch", "--mac", "AA:BB:CC:DD:EE:FF", "--interval", "0", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
