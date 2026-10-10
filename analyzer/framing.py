"""RRF v5 record framing; the encrypted payload length is in the RRF envelope.

Unknown game opcodes never change the outer record boundary. Corrupt envelopes
are only resynchronised when two adjacent supported records validate; guessing
from an arbitrary opcode in ciphertext is deliberately avoided.
"""
import struct
from collections import deque
from dataclasses import dataclass

from analyzer.packets import registry


@dataclass(frozen=True)
class Frame:
    offset: int
    end_offset: int
    timestamp: int
    packet: bytes
    record_id: int


class PacketFramer:
    def __init__(self, crypt, offset=0, profile='kro_2026'):
        self.crypt = crypt
        self.offset = offset
        self.read_offset = offset
        self.pending = bytearray()
        self.defs = registry(profile, True)
        self.incomplete = self.rejected = self.resync = self.duplicates = 0
        self.diagnostics = deque(maxlen=200)
        self.corrupt = False

    def _candidate(self, pos):
        if pos + 10 > len(self.pending):
            return None
        rid, tm, length = struct.unpack_from('<iiH', self.pending, pos)
        end = pos + 10 + length
        if length < 2 or tm < 0 or rid < 0 or end > len(self.pending):
            return None
        p = self.crypt(self.pending[pos + 10:end])
        d = self.defs.get(int.from_bytes(p[:2], 'little'))
        if d is None:
            return None
        if (d.length is not None and len(p) != d.length) or (d.length is None and
                (len(p) < 4 or int.from_bytes(p[2:4], 'little') != len(p))):
            return None
        try:
            if d.decode:
                d.decode(p)
        except Exception:
            return None
        return end

    def feed(self, data, offset=None):
        offset = self.read_offset if offset is None else offset
        if offset > self.read_offset:
            raise ValueError('Non-contiguous input: bytes are missing')
        overlap = self.read_offset - offset
        if overlap:
            self.duplicates += 1
            data = data[overlap:]
        self.read_offset += len(data)
        self.pending.extend(data)
        out, q = [], 0
        while q + 10 <= len(self.pending):
            rid, tm, length = struct.unpack_from('<iiH', self.pending, q)
            if length < 2 or rid < 0 or tm < 0 or self.corrupt:
                self.corrupt = True
                found = None
                for j in range(q + 1, min(len(self.pending) - 9, q + 65546)):
                    end = self._candidate(j)
                    if end is not None and self._candidate(end) is not None:
                        found = j
                        break
                if found is None:
                    # Bound corrupt-tail memory. Keep one maximum record plus lookahead.
                    if len(self.pending) - q > 131100:
                        drop = len(self.pending) - q - 131100
                        self.diagnostics.append(dict(offset=self.offset + q, rejected_bytes=drop))
                        self.rejected += drop
                        q += drop
                    break
                self.diagnostics.append(dict(offset=self.offset + q, rejected_bytes=found - q))
                self.rejected += found - q
                self.resync += 1
                self.corrupt = False
                q = found
                continue
            end = q + 10 + length
            if end > len(self.pending):
                break
            p = self.crypt(self.pending[q + 10:end])
            out.append(Frame(self.offset + q, self.offset + end, tm, p, rid))
            q = end
        if q:
            del self.pending[:q]
            self.offset += q
        if self.pending:
            self.incomplete += 1
        return out
