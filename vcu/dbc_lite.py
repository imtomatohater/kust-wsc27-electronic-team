"""Minimal DBC parser/codec (no external dependency).

Supports what WaveSculptor22 DBCs use: standard IDs, Intel (little-endian)
signals, unsigned/signed integers, IEEE float32 (SIG_VALTYPE_ = 1),
factor/offset, units and comments.
"""
import re
import struct

_BO = re.compile(r'^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)')
_SG = re.compile(r'^\s*SG_\s+(\w+)\s*(?:\w+\s*)?:\s*(\d+)\|(\d+)@([01])([+-])\s*'
                 r'\(([^,]+),([^)]+)\)\s*\[([^|]*)\|([^\]]*)\]\s*"([^"]*)"')
_VT = re.compile(r'^SIG_VALTYPE_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s*;')


class Signal:
    def __init__(self, name, start, length, little, signed, factor, offset, unit):
        self.name, self.start, self.length = name, start, length
        self.little, self.signed = little, signed
        self.factor, self.offset, self.unit = factor, offset, unit
        self.is_float = False

    def decode(self, raw_int):
        v = (raw_int >> self.start) & ((1 << self.length) - 1)
        if self.is_float:
            v = struct.unpack('<f', struct.pack('<I', v))[0]
        elif self.signed and v & (1 << (self.length - 1)):
            v -= 1 << self.length
        return v * self.factor + self.offset

    def encode(self, value):
        v = (value - self.offset) / self.factor
        if self.is_float:
            v = struct.unpack('<I', struct.pack('<f', float(v)))[0]
        else:
            v = int(round(v)) & ((1 << self.length) - 1)
        return v << self.start


class Message:
    def __init__(self, frame_id, name, dlc, sender):
        self.frame_id, self.name, self.dlc, self.sender = frame_id, name, dlc, sender
        self.signals = []

    def decode(self, data):
        raw = int.from_bytes(bytes(data).ljust(8, b'\0')[:8], 'little')
        return {s.name: s.decode(raw) for s in self.signals}

    def encode(self, values):
        raw = 0
        for s in self.signals:
            raw |= s.encode(values.get(s.name, 0.0))
        return raw.to_bytes(8, 'little')[:self.dlc]


class Database:
    def __init__(self):
        self.messages = {}          # frame_id -> Message
        self.by_name = {}

    def load(self, path):
        cur = None
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                m = _BO.match(line)
                if m:
                    cur = Message(int(m[1]) & 0x1FFFFFFF, m[2], int(m[3]), m[4])
                    self.messages[cur.frame_id] = cur
                    self.by_name[cur.name] = cur
                    continue
                m = _SG.match(line)
                if m and cur is not None:
                    if m[4] == '0':
                        print(f'[dbc] skip Motorola signal {m[1]} (not supported)')
                        continue
                    cur.signals.append(Signal(m[1], int(m[2]), int(m[3]), True,
                                              m[5] == '-', float(m[6]), float(m[7]), m[10]))
                    continue
                if not line.strip():
                    cur = None
                m = _VT.match(line)
                if m:
                    msg = self.messages.get(int(m[1]))
                    for s in (msg.signals if msg else []):
                        if s.name == m[2] and m[3] in ('1', '2'):
                            s.is_float = True
        return self

    def remap(self, old_base, new_base):
        """Shift all frame IDs (e.g. DBC written for base 0x80, device set to 0x400)."""
        if old_base == new_base:
            return self
        msgs = list(self.messages.values())
        self.messages = {}
        for m in msgs:
            m.frame_id = m.frame_id - old_base + new_base
            self.messages[m.frame_id] = m
        return self
