"""인던 대기열 알림: 대기 순번이 기준 이하로 내려오는 순간 한 번 알린다.

순번은 리플레이의 인던 대기 패킷(0x02cb)에서 읽은 값이다. 알리기만 한다 — 게임에 입력을 보내지 않는다.
"""


def message(threshold):
    return '대기열이 %d 이하입니다' % threshold


class QueueWatch:
    def __init__(self):
        self._below = False

    def check(self, queue, threshold):
        """queue: dict(name, position) 또는 None(대기 중 아님). 알릴 때만 글을 돌려준다."""
        below = bool(queue) and queue.get('position') is not None and queue['position'] <= threshold
        fire = below and not self._below             # 기준 위에서 아래로 내려온 순간만(같은 대기에서 되풀이하지 않는다)
        self._below = below
        if not fire:
            return None
        return '%s (%s%d번째)' % (message(threshold), queue['name'] + ' · ' if queue.get('name') else '', queue['position'])
