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
    monkeypatch.setattr("airpods_transparency.controller.Controller.read", fail)
    assert main(["read", "--mac", "AA:BB:CC:DD:EE:FF"]) == 1
    assert "blocked" in capsys.readouterr().err


def test_json_error_and_bad_arguments(monkeypatch, capsys):
    def fail(self):
        raise ConnectionRefusedError(111, "refused")
    monkeypatch.setattr("airpods_transparency.controller.Controller.read", fail)
    assert main(["read", "--mac", "AA:BB:CC:DD:EE:FF", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"]["errno"] == 111
    assert main(["--json", "read", "--mac", "invalid"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
