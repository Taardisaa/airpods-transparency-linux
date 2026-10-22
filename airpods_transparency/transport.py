"""Serialized ATT over BR/EDR L2CAP, following LibrePods ATTManager.kt.

The peer's read response must contain the full supported layout. Truncated or new
layouts are rejected by Settings. No CCCD or hearing-aid writes are performed.
"""
import re
import socket
import struct
import time

TRANSPARENCY_HANDLE = 0x18
ATT_PSM = 0x1F


def normalize_mac(mac):
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
        raise ValueError("Expected a Bluetooth address such as AA:BB:CC:DD:EE:FF")
    return mac.upper()


class ProtocolError(RuntimeError):
    pass


class ATTError(ProtocolError):
    def __init__(self, opcode, handle, code):
        self.code = code
        names = {1: "invalid handle", 2: "read not permitted", 3: "write not permitted",
                 5: "insufficient authentication", 6: "request not supported",
                 8: "insufficient authorization", 14: "unlikely error",
                 15: "insufficient encryption"}
        super().__init__(f"ATT error 0x{code:02x} ({names.get(code, 'device error')}) "
                         f"for request 0x{opcode:02x}, handle 0x{handle:04x}. "
                         "Check pairing, connection, and Apple DeviceID setup; see README.")


class ATTClient:
    def __init__(self, mac, timeout=5, sock=None):
        self.mac = normalize_mac(mac)
        self.timeout = timeout
        self.sock = sock

    def __enter__(self):
        if self.sock is None:
            self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                                      socket.BTPROTO_L2CAP)
            self.sock.settimeout(self.timeout)
            try:
                self.sock.connect((self.mac, ATT_PSM))
            except BaseException:
                self.sock.close()
                self.sock = None
                raise
        return self

    def __exit__(self, *args):
        if self.sock:
            self.sock.close()
            self.sock = None

    def exchange(self, request, expected):
        deadline = time.monotonic() + self.timeout
        if self.sock.send(request) != len(request):
            raise ProtocolError("Incomplete ATT send")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for ATT response")
            self.sock.settimeout(remaining)
            pdu = self.sock.recv(1024)
            if not pdu:
                raise ConnectionError("AirPods closed the ATT connection")
            if pdu[0] in (0x1B, 0x1D):
                if len(pdu) < 3:
                    raise ProtocolError("Malformed ATT notification")
                if pdu[0] == 0x1D:
                    self.sock.send(b"\x1e")  # Handle Value Confirmation
                continue
            if pdu[0] == 1:
                if len(pdu) != 5 or pdu[1] != request[0]:
                    raise ProtocolError("Malformed or unrelated ATT error")
                handle = struct.unpack_from("<H", pdu, 2)[0]
                if handle != TRANSPARENCY_HANDLE:
                    raise ProtocolError("ATT error refers to an unrelated handle")
                raise ATTError(pdu[1], handle, pdu[4])
            if pdu[0] != expected:
                raise ProtocolError(f"Unexpected ATT opcode 0x{pdu[0]:02x}; expected 0x{expected:02x}")
            return pdu[1:]

    def read(self):
        return self.exchange(struct.pack("<BH", 0x0A, TRANSPARENCY_HANDLE), 0x0B)

    def write(self, value):
        from .settings import Settings
        Settings(value)
        response = self.exchange(struct.pack("<BH", 0x12, TRANSPARENCY_HANDLE) + value, 0x13)
        if response:
            raise ProtocolError("Malformed ATT write response")
