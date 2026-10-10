"""Read only appended bytes and small fixed headers. Never modify the source.

The v5 packet container can have a zero (open) length; nonzero lengths are
refreshed because the recorder may patch its directory while recording.
"""
import hashlib
import os
import struct
import uuid
from dataclasses import dataclass

from analyzer.framing import PacketFramer
from rrf import Replay

HEADER_SIZE = 352


@dataclass(frozen=True)
class ReadBatch:
    frames: tuple
    reset: bool
    growth: bool


class IncrementalReplayReader:
    def __init__(self, path, profile='kro_2026', read_limit=256 * 1024):
        self.path, self.profile, self.read_limit = os.fspath(path), profile, read_limit
        self.identity = None
        self.session_id = str(uuid.uuid4())
        self.file_size = self.last_read_offset = self.last_confirmed_offset = 0
        self.last_confirmed_timestamp = None
        self.bytes_read = self.header_bytes_read = 0
        self.replay = self.framer = None
        self.packet_start = self.packet_end = None
        self._anchor = None
        self.signature = None

    def poll(self):
        with open(self.path, 'rb') as f:
            stat = os.fstat(f.fileno())
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            header = f.read(HEADER_SIZE)
            self.header_bytes_read += len(header)
            # Header directory lengths are mutable and excluded from identity.
            identity = (stat.st_dev, stat.st_ino,
                        hashlib.sha256(header[:112]).hexdigest() if len(header) >= 112 else None)
            reset = self.identity is not None and (stat.st_size < self.file_size or
                identity[:2] != self.identity[:2] or
                (identity[2] is not None and self.identity[2] is not None and identity[2] != self.identity[2]))
            if not reset and self._anchor:
                anchor_offset, anchor_bytes = self._anchor
                f.seek(anchor_offset)
                actual = f.read(len(anchor_bytes))
                self.header_bytes_read += len(actual)
                reset = actual != anchor_bytes
            growth = self.signature is not None and signature != self.signature
            growth = growth or stat.st_size != self.file_size
            self.signature = signature
            if reset:
                self.session_id = str(uuid.uuid4())
                self.replay = self.framer = None
                self.last_read_offset = self.last_confirmed_offset = 0
                self.last_confirmed_timestamp = None
                self.bytes_read = 0
                self._anchor = None
            self.identity, self.file_size = identity, stat.st_size
            if len(header) < HEADER_SIZE:
                return ReadBatch((), reset, growth)
            if header[100] != 5:
                raise ValueError('Unsupported RRF version: %d' % header[100])
            containers = [struct.unpack_from('<Hii', header, 112 + i * 10) for i in range(24)]
            packet = next((c for c in containers if c[0] == 1 and c[2] >= HEADER_SIZE), None)
            if packet is None:
                return ReadBatch((), reset, growth)
            _, length, start = packet
            if length < 0:
                raise ValueError('Negative packet container length')
            later = [off for typ, ln, off in containers if off > start and typ != 1]
            end = start + length if length else min(later, default=stat.st_size)
            end = min(end, min(later, default=end), stat.st_size)
            if self.replay is None:
                # Metadata must be complete before binding player identity; do not guess.
                meta_end = max([HEADER_SIZE] + [off + ln for typ, ln, off in containers
                                               if typ != 1 and off > 0 and ln > 0])
                if meta_end > stat.st_size or start > stat.st_size:
                    return ReadBatch((), reset, growth)
                if meta_end > start:
                    raise ValueError('Metadata after packet stream is not supported for LIVE')
                if start > 64 * 1024 * 1024:
                    raise ValueError('Replay metadata exceeds LIVE safety limit (64 MiB)')
                f.seek(0)
                prefix = f.read(start)
                self.bytes_read += len(prefix)
                self.replay = Replay.from_bytes(prefix)
                self.framer = PacketFramer(self.replay._crypt, start, self.profile)
                self.last_read_offset = self.last_confirmed_offset = start
                self.packet_start = start
            elif start != self.packet_start:
                raise ValueError('Packet container moved while recording')
            self.packet_end = end
            f.seek(self.last_read_offset)
            data = f.read(min(self.read_limit, max(0, end - self.last_read_offset)))
            self.bytes_read += len(data)
            frames = self.framer.feed(data, self.last_read_offset)
            self.last_read_offset += len(data)
            if frames:
                anchor_offset = max(start, self.framer.offset - 64)
                f.seek(anchor_offset)
                anchor_bytes = f.read(self.framer.offset - anchor_offset)
                self.header_bytes_read += len(anchor_bytes)
                self._anchor = (anchor_offset, anchor_bytes)
            return ReadBatch(tuple(frames), reset, growth)

    @property
    def caught_up(self):
        return self.packet_end is not None and self.last_read_offset >= self.packet_end

    def checkpoint(self):
        return dict(identity=self.identity, session_id=self.session_id,
                    confirmed_offset=self.last_confirmed_offset,
                    timestamp=self.last_confirmed_timestamp, path=self.path,
                    recovery='Replay confirmed prefix on restart; offset alone is not reducer state')
