"""SkillAnalyzer: 스킬별 사용/피해/간격/캐스팅.

이론상 최대치는 만들지 않는다. 관측된 최소 사용간격 또는 사용자가 입력한
expected_delay(초, settings['expected_delays'][skill_id])와만 비교한다.
"""
import collections

from analyzer.analyzers import tracking
from analyzer.analyzers.combat import COMBAT_GAP_MS, combat_time_ms
from analyzer.analyzers.context import NO_DATA, per_sec
from analyzer.events import EventType as E

SAME_USE_MS = 50   # 같은 스킬 피해가 이 간격 안이면 한 번의 사용(다중 타격)으로 본다


def analyze(ctx):
    me = ctx.player
    names = ctx.gamedata.skills
    expected = {int(k): float(v) for k, v in (ctx.settings.get('expected_delays') or {}).items()}
    s = collections.defaultdict(lambda: dict(uses=[], casts=[], hits=[], damage=[]))
    for e in ctx.events:
        if e.actor_id != me:
            continue
        if e.event_type == E.SKILL_USE:
            s[e.data['skill']]['uses'].append(e.timestamp_ms)
        elif e.event_type == E.SKILL_CAST_START:
            s[e.data['skill']]['casts'].append((e.timestamp_ms, e.data['cast_ms']))
        elif e.event_type == E.DAMAGE and e.data.get('skill') is not None:
            r = s[e.data['skill']]
            r['hits'].append(e.timestamp_ms)
            if not e.data.get('miss'):
                r['damage'].append(e.data['amount'])

    all_dmg = [e for e in ctx.of(E.DAMAGE) if e.actor_id == me and not e.data.get('miss')]
    total_dmg = sum(e.data['amount'] for e in all_dmg)
    ctime = combat_time_ms([e.timestamp_ms for e in all_dmg], ctx.settings.get('combat_gap_ms', COMBAT_GAP_MS))
    rows = []
    server = tracking.skills(ctx)                # 서버가 알려 준 재사용 대기·실패(패킷 0x043d / 0x0110)
    for sid, r in s.items():
        extra = server.get(sid) or {}
        # 사용 시각: 비피해 스킬 사용(0x09cb) + 피해 스킬은 가까운 타격을 묶어 1회
        use_ts = sorted(r['uses'])
        if r['hits']:
            grouped = []
            for t in sorted(r['hits']):
                if not grouped or t - grouped[-1] > SAME_USE_MS:
                    grouped.append(t)
            if len(grouped) > len(use_ts):
                use_ts = grouped
        if not use_ts and r['casts']:
            use_ts = [t for t, _ in r['casts']]
        gaps = [(b - a) / 1000 for a, b in zip(use_ts, use_ts[1:])]
        dmg = r['damage']
        avg_gap = sum(gaps) / len(gaps) if gaps else None
        min_gap = min(gaps) if gaps else None
        exp = expected.get(sid)
        rows.append(dict(
            skill_id=sid, name=names.name(sid), uses=len(use_ts), casts=len(r['casts']), hits=len(r['hits']),
            total_damage=sum(dmg), avg_damage=sum(dmg) / len(dmg) if dmg else None,
            min_damage=min(dmg) if dmg else None, max_damage=max(dmg) if dmg else None,
            share=(sum(dmg) / total_dmg * 100) if total_dmg and dmg else None,
            dps=per_sec(sum(dmg), ctime) if dmg and all_dmg else None,
            avg_interval=avg_gap, min_interval=min_gap,
            cast_ms_avg=sum(c for _, c in r['casts']) / len(r['casts']) if r['casts'] else None,
            efficiency_observed=(min_gap / avg_gap * 100) if avg_gap and min_gap is not None and len(gaps) >= 5 else None,
            expected_delay=exp,
            efficiency_expected=(exp / avg_gap * 100) if exp and avg_gap else None,
            post_delay=None, cooldown=extra.get('cooldown_ms'), failures=extra.get('failures'),
            blocked=extra.get('blocked') or None, fail_causes=extra.get('causes') or [],
        ))
    rows.sort(key=lambda r: (-(r['total_damage'] or 0), -r['uses']))
    return dict(available=bool(rows), note='' if rows else NO_DATA + ' (스킬 사용 패킷 0건)', skills=rows,
                total_damage=total_dmg,
                unsupported='재사용 대기·실패 횟수는 서버가 보낸 값(●) · 실패 사유의 이름은 서버 구현체(rAthena) 표 기준')
