"""Transparency layout derived from LibrePods Transparency.kt (GPL-3.0-or-later).

Only the 100/104-byte transparency layout is supported. Unedited fields, including
the two EQ arrays, are retained byte for byte; the hearing-aid handle is never used.
"""
import math
import struct
from dataclasses import dataclass


FIELDS = {
    "enabled": (0, 0, 1, True),
    "left_amplification": (36, -1, 1, False),
    "left_tone": (40, -1, 1, False),
    "left_conversation_boost": (44, 0, 1, True),
    "left_noise_reduction": (48, 0, 1, False),
    "right_amplification": (84, -1, 1, False),
    "right_tone": (88, -1, 1, False),
    "right_conversation_boost": (92, 0, 1, True),
    "right_noise_reduction": (96, 0, 1, False),
    "own_voice": (100, 0, 1, False),
}


@dataclass(frozen=True)
class Settings:
    raw: bytes

    def __post_init__(self):
        if len(self.raw) not in (100, 104):
            raise ValueError(f"Unknown transparency layout: {len(self.raw)} bytes; refusing to edit")
        values = struct.unpack(f"<{len(self.raw) // 4}f", self.raw)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite values in transparency payload")
        for name, (offset, lo, hi, boolean) in FIELDS.items():
            if offset >= len(self.raw):
                continue
            v = struct.unpack_from("<f", self.raw, offset)[0]
            if not lo <= v <= hi or (boolean and v not in (0.0, 1.0)):
                raise ValueError(f"Unexpected {name} value {v}; unsupported layout")

    def values(self):
        result = {}
        for name, (offset, _, _, boolean) in FIELDS.items():
            if offset < len(self.raw):
                v = struct.unpack_from("<f", self.raw, offset)[0]
                result[name] = bool(v) if boolean else v
        return result

    def edited(self, changes):
        buf = bytearray(self.raw)
        for name, value in changes.items():
            if name not in FIELDS:
                raise ValueError(f"Unknown field: {name}")
            offset, lo, hi, boolean = FIELDS[name]
            if offset >= len(buf):
                raise ValueError(f"{name} is unavailable on this payload")
            if boolean and not isinstance(value, bool):
                raise ValueError(f"{name} requires a boolean")
            v = float(value)
            if not math.isfinite(v) or not lo <= v <= hi:
                raise ValueError(f"{name} must be between {lo} and {hi}")
            struct.pack_into("<f", buf, offset, v)
        return Settings(bytes(buf))
