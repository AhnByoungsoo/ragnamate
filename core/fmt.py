"""숫자·시간 표시 형식 한 곳. UI(표·카드)와 Overlay/HUD가 같은 함수를 쓴다(Qt 비의존)."""


def comma(v, digits=0):
    if v is None or v == '':
        return '-'
    if isinstance(v, float) and digits:
        return '{:,.{}f}'.format(v, digits)
    return '{:,}'.format(int(round(v)))


def unit(v, digits=2):
    """19,421,382 → 19.42 M"""
    if v is None:
        return '-'
    a = abs(v)
    for u, d in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if a >= d:
            return ('{:.%df} %s' % (digits, u)).format(v / d)
    return comma(v)


def sec(v):
    return '-' if v is None else '%.2f초' % v


def hms(ms):
    """밀리초 → 00:12:34"""
    if ms is None:
        return '-'
    s = int(ms // 1000)
    return '%02d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60)


def hms_sec(s):
    """초 → 0:12:34 (세션 시계용)"""
    return None if s is None else '%d:%02d:%02d' % (int(s) // 3600, int(s) // 60 % 60, int(s) % 60)


def mmss(s):
    """남은 시간 → 12:34 (1시간 이상이면 1:02:03). 모르면 '?'."""
    if s is None:
        return '?'
    s = int(round(s))
    return '%d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60) if s >= 3600 else '%d:%02d' % (s // 60, s % 60)


def pct(v):
    return '-' if v is None else '%.1f%%' % v
