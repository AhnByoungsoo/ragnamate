"""HP/SP 변화 분석. AP는 varID 의미가 검증되지 않아 미지원(값은 이벤트 raw에 보존)."""
from analyzer.analyzers.context import NO_DATA
from analyzer.events import EventType as E

HP_DANGER = 0.3
SP_LOW = 0.1


def _series(ctx, et):
    cur = mx = None
    pts = []
    for e in ctx.pr.events:
        if e.event_type != et:
            continue
        if 'max' in e.data:
            mx = e.data['max']
        else:
            cur = e.data['value']
        if cur is not None and ctx.start_ms <= e.timestamp_ms <= ctx.end_ms:
            pts.append((e.timestamp_ms, cur, mx))
    return pts


def _low_periods(pts, ratio, end_ms):
    periods, start = [], None
    for i, (t, v, mx) in enumerate(pts):
        low = bool(mx) and v < mx * ratio
        if low and start is None:
            start = t
        elif not low and start is not None:
            periods.append((start, t))
            start = None
    if start is not None:
        periods.append((start, end_ms))
    return [dict(start_ms=a, end_ms=b, sec=(b - a) / 1000) for a, b in periods]


def _stats(pts, end_ms):
    if not pts:
        return None
    total_w = 0
    acc = 0
    for (t, v, _), nxt in zip(pts, pts[1:] + [(end_ms, None, None)]):
        w = max(0, nxt[0] - t)
        acc += v * w
        total_w += w
    return dict(min=min(v for _, v, _ in pts), max=max(v for _, v, _ in pts),
                avg=acc / total_w if total_w else pts[0][1], last_max=pts[-1][2])


def analyze(ctx):
    hp, sp = _series(ctx, E.HP_CHANGE), _series(ctx, E.SP_CHANGE)
    return dict(
        available=bool(hp or sp), note='' if (hp or sp) else NO_DATA,
        hp=_stats(hp, ctx.end_ms), sp=_stats(sp, ctx.end_ms),
        hp_series=[(t, v) for t, v, _ in hp], sp_series=[(t, v) for t, v, _ in sp],
        hp_danger=_low_periods(hp, HP_DANGER, ctx.end_ms), sp_low=_low_periods(sp, SP_LOW, ctx.end_ms),
        ap_note='AP: 스탯 varID 의미 미검증으로 미지원',
    )
