"""CombatAnalyzer: 피해/DPS/처치/받은 피해."""
from analyzer.analyzers.context import NO_DATA, per_sec
from analyzer.events import CALC, MEASURED, EventType as E

COMBAT_GAP_MS = 3000         # 이 간격 이상 피해가 없으면 비전투로 간주
BURST_WINDOWS = (1, 3, 5, 10)


def combat_time_ms(times, gap_ms=COMBAT_GAP_MS):
    """피해 이벤트 시각 목록 → 실제 전투시간(연속 간격을 gap으로 자른 합, 최소 1초)."""
    if not times:
        return 0
    ts = sorted(times)
    total = sum(min(b - a, gap_ms) for a, b in zip(ts, ts[1:]))
    return max(total, 1000)


def max_window_sum(points, window_ms):
    """[(t, amount)] 에서 길이 window_ms 구간 합의 최댓값과 시작시각."""
    pts = sorted(points)
    best, best_t, s, j = 0, None, 0, 0
    for i, (t, a) in enumerate(pts):
        s += a
        while pts[j][0] <= t - window_ms:
            s -= pts[j][1]
            j += 1
        if s > best:
            best, best_t = s, pts[j][0]
    return best, best_t


def analyze(ctx):
    me = ctx.player
    dmg = ctx.of(E.DAMAGE)
    dealt = [e for e in dmg if e.actor_id == me]
    taken = [e for e in dmg if e.target_id == me]
    hits = [e for e in dealt if not e.data.get('miss')]
    amounts = [e.data['amount'] for e in hits]
    total = sum(amounts)
    ctime = combat_time_ms([e.timestamp_ms for e in dealt], ctx.settings.get('combat_gap_ms', COMBAT_GAP_MS))

    # 처치: 플레이어가 피해를 준 몬스터가 사망(0x0080 type=1)
    first_hit = {}
    alive = {}
    kills, kill_times = 0, []
    for e in ctx.events:
        if e.event_type == E.MAP_CHANGE:
            first_hit.clear()
            alive.clear()
        elif e.event_type == E.MONSTER_SPAWN:
            if e.actor_id not in alive or alive[e.actor_id] != e.data.get('class_id'):
                first_hit.pop(e.actor_id, None)
            alive[e.actor_id] = e.data.get('class_id')
        elif e.event_type == E.DAMAGE and e.actor_id == me and not e.data.get('miss'):
            first_hit.setdefault(e.target_id, e.timestamp_ms)
        elif e.event_type == E.MONSTER_DEATH:
            alive.pop(e.actor_id, None)
            first = first_hit.pop(e.actor_id, None)
            if first is not None:
                kills += 1
                kill_times.append(e.timestamp_ms - first)
        elif e.event_type == E.UNIT_VANISH:
            first_hit.pop(e.actor_id, None)
            alive.pop(e.actor_id, None)
    deaths_in_view = len(ctx.of(E.MONSTER_DEATH))

    heal = 0
    last_hp = None
    for e in ctx.of(E.HP_CHANGE):
        v = e.data.get('value')
        if v is None:
            continue
        if last_hp is not None and v > last_hp:
            heal += v - last_hp
        last_hp = v

    burst = {}
    pts = [(e.timestamp_ms, e.data['amount']) for e in hits]
    for w in BURST_WINDOWS:
        s, t = max_window_sum(pts, w * 1000) if pts else (0, None)
        burst[w] = dict(dps=s / w if pts else None, at_ms=t)

    taken_amounts = [e.data['amount'] for e in taken if not e.data.get('miss')]
    res = dict(
        available=bool(dealt or taken),
        note='' if (dealt or taken) else NO_DATA + ' (피해 패킷 0건)',
        total_damage=total,
        hit_count=len(hits),
        attack_count=len(dealt),
        normal_attacks=sum(1 for e in dealt if e.data.get('skill') is None),
        skill_hits=sum(1 for e in dealt if e.data.get('skill') is not None),
        miss=sum(1 for e in dealt if e.data.get('miss')),
        critical=sum(1 for e in hits if e.data.get('hit') == 'critical'),
        avg_hit=total / len(amounts) if amounts else None,
        max_hit=max(amounts) if amounts else None,
        min_hit=min(amounts) if amounts else None,
        duration_ms=ctx.duration_ms,
        combat_ms=ctime if dealt else 0,
        dps_total=per_sec(total, ctx.duration_ms) if dealt else None,
        dps_combat=per_sec(total, ctime) if dealt else None,
        burst=burst,
        kills=kills,
        monster_deaths_in_view=deaths_in_view,
        kill_time_avg=sum(kill_times) / len(kill_times) / 1000 if kill_times else None,
        kill_time_min=min(kill_times) / 1000 if kill_times else None,
        kill_time_max=max(kill_times) / 1000 if kill_times else None,
        taken_total=sum(taken_amounts),
        taken_count=len(taken),
        taken_avg=sum(taken_amounts) / len(taken_amounts) if taken_amounts else None,
        taken_max=max(taken_amounts) if taken_amounts else None,
        taken_miss=sum(1 for e in taken if e.data.get('miss')),
        hp_recovered=heal,
        conf=dict(total_damage=MEASURED, dps_combat=CALC, dps_total=CALC, kills=MEASURED, kill_time_avg=CALC,
                  hp_recovered=CALC, taken_total=MEASURED),
    )
    if any(e.confidence != MEASURED for e in dealt):
        res['note'] = '일부 피해가 미검증 후보 패킷에서 계산됨(△)'
    return res
