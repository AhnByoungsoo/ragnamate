"""BuffAnalyzer: 플레이어 버프 가동률/공백/재사용, 버프 유무별 DPS."""
import collections

from analyzer.analyzers.context import NO_DATA
from analyzer.events import EventType as E

MIN_GAP_MS = 1000               # 이보다 짧은 공백은 갱신(재시전)으로 본다
MIN_SAMPLE_MS = 30_000          # 버프 유무 DPS 비교 최소 전투 표본


def buff_events(ctx):
    """{efst: [event]} 플레이어 버프 이벤트(전체 Replay, 한 번 순회)."""
    out = collections.defaultdict(list)
    for e in ctx.pr.events:
        if e.actor_id == ctx.player and e.event_type in (E.BUFF_START, E.BUFF_END):
            out[e.data['efst']].append(e)
    return out


def intervals(ctx, events):
    """[(start, end)] 해당 버프 활성 구간(분석 구간으로 잘라냄). 종료 패킷이 없으면 Replay 끝까지."""
    out, cur = [], None
    for e in events:
        if e.event_type == E.BUFF_START:
            if cur is None:
                cur = e.timestamp_ms
        elif cur is not None:
            out.append((cur, e.timestamp_ms))
            cur = None
    if cur is not None:
        out.append((cur, ctx.pr.duration_ms))
    clipped = []
    for a, b in out:
        a, b = max(a, ctx.start_ms), min(b, ctx.end_ms)
        if b > a:
            clipped.append((a, b))
    return clipped


def analyze(ctx, damage_points=None):
    me, dur = ctx.player, ctx.duration_ms
    rows = []
    for efst, evs in buff_events(ctx).items():
        iv = intervals(ctx, evs)
        if not iv:
            continue
        active = sum(b - a for a, b in iv)
        gaps = [(a2 - b1) for (a1, b1), (a2, b2) in zip(iv, iv[1:]) if a2 - b1 >= MIN_GAP_MS]
        gap_list = [dict(start_ms=b1, end_ms=a2, sec=(a2 - b1) / 1000)
                    for (a1, b1), (a2, b2) in zip(iv, iv[1:]) if a2 - b1 >= MIN_GAP_MS]
        starts = sum(1 for e in evs if e.event_type == E.BUFF_START and ctx.start_ms <= e.timestamp_ms <= ctx.end_ms)
        row = dict(efst=efst, name=ctx.gamedata.buffs.name(efst), first_start_ms=iv[0][0], last_end_ms=iv[-1][1],
                   active_ms=active, uptime=active / dur * 100 if dur else None, gaps=gap_list,
                   gap_total_ms=sum(gaps), recasts=starts, dps_on=None, dps_off=None, dps_note='')
        if damage_points:
            on = off = 0
            on_ms = active
            off_ms = dur - active
            for t, a in damage_points:
                if any(x <= t < y for x, y in iv):
                    on += a
                else:
                    off += a
            if on_ms >= MIN_SAMPLE_MS and off_ms >= MIN_SAMPLE_MS:
                row['dps_on'], row['dps_off'] = on * 1000 / on_ms, off * 1000 / off_ms
            else:
                row['dps_note'] = '데이터 부족'
        rows.append(row)
    rows.sort(key=lambda r: -(r['uptime'] or 0))
    return dict(available=bool(rows), note='' if rows else NO_DATA + ' (플레이어 버프 패킷 0건)', buffs=rows)
