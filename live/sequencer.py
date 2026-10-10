"""Bounded timestamp reorder queue with offset identity, never semantic dedup."""
import heapq


class EventSequencer:
    def __init__(self, window_ms=250, limit=20000):
        self.window_ms, self.limit = max(0, window_ms), limit
        self.queue = []
        self.max_timestamp = None
        self.high_offset = -1
        self.confirmed_timestamp = -1
        self.duplicates = self.late = self.disorder = self.pressure = 0

    def offer(self, frame):
        if frame.offset <= self.high_offset:
            self.duplicates += 1
            return []
        self.high_offset = frame.offset
        if self.max_timestamp is not None and frame.timestamp < self.max_timestamp:
            self.disorder += 1
        self.max_timestamp = max(frame.timestamp, self.max_timestamp or 0)
        heapq.heappush(self.queue, (frame.timestamp, frame.offset, frame))
        out = self.drain(self.max_timestamp - self.window_ms)
        if len(self.queue) > self.limit:
            self.pressure += 1
            out.extend(self.drain(self.queue[0][0]))
        return out

    def drain(self, watermark=None, final=False):
        out = []
        while self.queue and (final or self.queue[0][0] <= watermark):
            tm, _, frame = heapq.heappop(self.queue)
            if tm < self.confirmed_timestamp:
                self.late += 1  # quarantine; FINAL offline analysis reconciles late state
                continue
            self.confirmed_timestamp = tm
            out.append(frame)
        return out
