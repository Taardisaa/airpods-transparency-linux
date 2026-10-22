"""AACP battery and listening modes, based on LibrePods' GPL protocol docs."""
import copy
import select
import socket
import time

from .transport import normalize_mac

MODES = {"off": 1, "anc": 2, "transparency": 3, "adaptive": 4}
MODE_NAMES = {value: key for key, value in MODES.items()}
PARTS = {4: "left", 2: "right", 8: "case"}
PREFIX = bytes.fromhex("04000400")


class AACPClient:
    def __init__(self, mac):
        self.mac = normalize_mac(mac)
        self.sock = None
        self.state = {"connected": False, "mode": None, "battery": {}, "ears": None,
                      "name": None, "model": None, "firmware": None, "updated_at": None}

    def connect(self):
        self.close()
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        sock.settimeout(4)
        try:
            sock.connect((self.mac, 0x1001))
            for packet in ("00000400010002000000000000000000",
                           "040004004d00ff00000000000000", "040004000f00ffffffff"):
                sock.sendall(bytes.fromhex(packet))
        except BaseException:
            sock.close()
            raise
        self.sock = sock
        # Old values must never be presented as fresh after a reconnect.
        self.state.update(connected=True, mode=None, battery={}, ears=None, updated_at=None)

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
        self.state["connected"] = False

    def parse(self, packet):
        changed = False
        if packet.startswith(PREFIX + b"\x09\x00\x0d") and len(packet) >= 11:
            mode = MODE_NAMES.get(packet[7])
            if mode:
                self.state["mode"] = mode
                changed = True
        elif packet.startswith(PREFIX + b"\x04\x00") and len(packet) >= 7:
            count = packet[6]
            if count > 3 or len(packet) != 7 + count * 5:
                return
            battery = {}
            for offset in range(7, len(packet), 5):
                part, marker, level, status, end = packet[offset:offset + 5]
                if part not in PARTS or marker != 1 or end != 1 or level > 100:
                    return
                available = status in (1, 2)
                battery[PARTS[part]] = {"percent": level if available else None,
                                      "charging": status == 1 if available else None,
                                      "available": available}
            self.state["battery"].update(battery)
            changed = True
        elif packet.startswith(PREFIX + b"\x06\x00") and len(packet) >= 8:
            labels = {0: "in_ear", 1: "out_of_ear", 2: "in_case"}
            self.state["ears"] = [labels.get(v, "unknown") for v in packet[6:8]]
            changed = True  # Primary/secondary, not necessarily left/right.
        elif packet.startswith(PREFIX + b"\x1d\x00") and len(packet) > 11:
            fields = packet[11:].split(b"\0")
            if len(fields) >= 5:
                # Deliberately omit serial numbers and raw metadata from status.
                for name, index in (("name", 0), ("model", 1), ("firmware", 4)):
                    self.state[name] = fields[index].decode("utf-8", "replace")[:128]
                changed = True
        if changed:
            self.state["updated_at"] = time.time()

    def pump(self, seconds, until=None):
        deadline = time.monotonic() + seconds
        while self.sock and time.monotonic() < deadline:
            if until and until():
                return True
            ready, _, _ = select.select([self.sock], [], [], max(0, deadline - time.monotonic()))
            if ready:
                packet = self.sock.recv(2048)
                if not packet:
                    raise ConnectionError("AirPods closed the AACP connection")
                self.parse(packet)
        return bool(until and until())

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(f"Unsupported mode: {mode}")
        if not self.sock:
            raise ConnectionError("AirPods control channel is disconnected")
        if self.state["mode"] == mode:
            return self.snapshot()
        if mode == "off":
            self.sock.sendall(PREFIX + bytes.fromhex("09003401000000"))
        self.sock.sendall(PREFIX + bytes([9, 0, 0x0D, MODES[mode], 0, 0, 0]))
        if not self.pump(4, lambda: self.state["mode"] == mode):
            raise RuntimeError(f"Mode {mode!r} was not acknowledged; current mode: {self.state['mode']}")
        return self.snapshot()

    def snapshot(self):
        return copy.deepcopy(self.state)
