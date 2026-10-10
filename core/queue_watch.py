"""인던 대기열 알림: 대기 순번이 기준 이하로 내려오는 순간 한 번 알린다.

순번은 리플레이의 인던 대기 패킷(0x02cb)에서 읽은 값이다. 알리기만 한다 — 게임에 입력을 보내지 않는다.

서버는 순번을 몇 초에 한 번, 몇 칸씩 건너뛰어 알려 준다(실측: 약 3초마다 4칸). 다음 소식을 기다리면 그만큼 늦으므로,
지금까지 줄어든 빠르기로 그 사이를 이어서 셈한다(○ 계산). 소식이 끊기면 한 번의 간격까지만 앞서 간다.
"""
import time

RECENT = 4                                           # 빠르기를 셈할 때 보는 최근 소식 수


def message(threshold):
    return '대기열이 %d 이하입니다' % threshold


class QueueWatch:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._samples = []                           # (리플레이 시계 ms, 순번) — 지금 대기의 소식들
        self._seen = 0.0                             # 마지막 소식을 받은 때(이 PC의 시계)
        self._below = False

    def estimate(self, now=None):
        """지금쯤의 순번. 소식이 하나뿐이거나 줄지 않았으면 마지막으로 들은 순번 그대로."""
        if not self._samples:
            return None
        recent = self._samples[-RECENT:]
        (t0, p0), (t1, p1) = recent[0], recent[-1]
        if len(recent) < 2 or t1 <= t0 or p1 >= p0:
            return p1
        gap = (t1 - t0) / (len(recent) - 1)
        ahead = min(((self.clock() if now is None else now) - self._seen) * 1000, gap)
        return p1 - (p0 - p1) / (t1 - t0) * max(0, ahead)

    def check(self, queue, threshold, now=None):
        """queue: dict(name, position, at) 또는 None(대기 중 아님). 알릴 때만 글을 돌려준다."""
        now = self.clock() if now is None else now
        if not queue or queue.get('position') is None:
            self._samples, self._below = [], False
            return None
        sample = (queue.get('at') or 0, queue['position'])
        if not self._samples or sample != self._samples[-1]:
            if self._samples and sample[1] > self._samples[-1][1]:       # 순번이 늘었다 = 새로 신청한 대기
                self._samples, self._below = [], False
            self._samples.append(sample)
            self._seen = now
        estimate = self.estimate(now)
        below = estimate <= threshold
        fire = below and not self._below             # 기준 위에서 아래로 내려온 순간만(같은 대기에서 되풀이하지 않는다)
        self._below = below
        if not fire:
            return None
        name = queue['name'] + ' · ' if queue.get('name') else ''
        if sample[1] <= threshold:
            return '%s (%s%d번째)' % (message(threshold), name, sample[1])
        return '%s (%s마지막 확인 %d번째, 지금쯤 %d번째로 계산)' % (message(threshold), name, sample[1], round(estimate))
