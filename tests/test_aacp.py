import socket

import pytest

from airpods_transparency.aacp import AACPClient, PREFIX

MAC = "AA:BB:CC:DD:EE:FF"


def test_battery_unknown_case_is_not_zero_percent():
    client = AACPClient(MAC)
    client.parse(bytes.fromhex("04000400040003020159020104015602010801000401"))
    assert client.state["battery"]["left"]["percent"] == 86
    assert client.state["battery"]["right"]["percent"] == 89
    assert client.state["battery"]["case"] == {"percent": None, "charging": None, "available": False}


def test_single_right_earbud_notification_is_valid():
    client = AACPClient(MAC)
    client.parse(bytes.fromhex('040004000400010201580201'))
    assert client.state['battery']['right']['percent'] == 88
    assert 'left' not in client.state['battery']


@pytest.mark.parametrize("packet", ["04000400040003", "040004000400010201ff0201",
                                    "040004000400010202420201", "04000400040004"])
def test_malformed_battery_does_not_partially_update(packet):
    client = AACPClient(MAC)
    client.parse(bytes.fromhex(packet))
    assert client.state["battery"] == {}


def test_notifications_and_serial_number_exclusion():
    client = AACPClient(MAC)
    client.parse(bytes.fromhex("0400040009000d03000000"))
    assert client.snapshot()["mode"] == "transparency"
    client.parse(bytes.fromhex("0400040006000001"))
    assert client.state["ears"] == ["in_ear", "out_of_ear"]
    client.parse(bytes.fromhex("040004001d0002d5000400") +
                 b"AirPods\0A3063\0Apple\0PRIVATE-SERIAL\081.2\0")
    assert client.state["model"] == "A3063"
    assert client.state["firmware"] == "81.2"
    assert "PRIVATE-SERIAL" not in str(client.snapshot())


def test_mode_change_needs_acknowledgement():
    local, remote = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client = AACPClient(MAC)
    client.sock = local
    client.state["mode"] = "transparency"
    remote.send(PREFIX + bytes.fromhex("09000d02000000"))
    result = client.set_mode("anc")
    assert result["mode"] == "anc"
    assert remote.recv(64) == PREFIX + bytes.fromhex("09000d02000000")
    client.close()
    remote.close()


def test_mode_failure_is_not_reported_as_success(monkeypatch):
    class FakeSocket:
        def sendall(self, packet):
            pass
    client = AACPClient(MAC)
    client.sock = FakeSocket()
    monkeypatch.setattr(client, "pump", lambda *args: False)
    with pytest.raises(RuntimeError, match="not acknowledged"):
        client.set_mode("adaptive")
