"""분석 엔진: ParsedReplay + 구간 → Report(dict, JSON 직렬화 가능)."""
import datetime

from analyzer.analyzers import buff, combat, exp_zeny, loot, monster, skill, timeline, tracking, vitals
from analyzer.analyzers.context import Context, per_hour
from analyzer.events import EventType as E
from analyzer.session import build_session

ROLLING = (1, 5, 10, 30)


def default_window(session):
    """가장 긴 사냥 구간, 없으면 전체."""
    hunts = [s for s in session.segments if s.is_hunt]
    if hunts:
        s = max(hunts, key=lambda s: s.duration_ms)
        return s.start_ms, s.end_ms, '구간 %d · %s' % (s.index + 1, s.map_name or '?')
    return 0, session.duration_ms, '전체'


def analyze(pr, gamedata, settings, price_lookup, session_id, start_ms=None, end_ms=None, label=None):
    session = build_session(pr, gamedata, session_id)
    if start_ms is None:
        start_ms, end_ms, label = default_window(session)
    ctx = Context.build(pr, gamedata, settings, start_ms, end_ms)
    basis = settings.get('profit_basis') or settings.get('price_basis', 'lowest')

    cb = combat.analyze(ctx)
    sk = skill.analyze(ctx)
    pts = [(e.timestamp_ms, e.data['amount']) for e in ctx.of(E.DAMAGE)
           if e.actor_id == pr.player_aid and not e.data.get('miss')]
    bf = buff.analyze(ctx, pts)
    vt = vitals.analyze(ctx)
    mo = monster.analyze(ctx)
    ex = exp_zeny.analyze_exp(ctx)
    zn = exp_zeny.analyze_zeny(ctx)
    lt = loot.analyze(ctx, price_lookup, basis)
    pf = loot.profit(ctx, lt, zn)
    mv = timeline.movement(ctx)
    dur = ctx.duration_ms
    kpi = dict(
        combat_dps=cb['dps_combat'], total_dps=cb['dps_total'], kills=cb['kills'] if cb['available'] else None,
        kills_per_hour=per_hour(cb['kills'], dur) if cb['available'] else None,
        base_exp_per_hour=ex.get('base_per_hour'), job_exp_per_hour=ex.get('job_per_hour'),
        profit=pf.get('profit'), profit_per_hour=pf.get('per_hour'), zeny_net=zn.get('net'),
        loot_value=lt.get('loot_value'), consumable_cost=lt.get('consumable_cost'),
        consumable_per_hour=per_hour(lt.get('consumable_cost'), dur) if lt.get('available') else None,
    )
    rep = dict(
        meta=dict(session_id=session_id, replay_file=pr.path, character=pr.character, job=session.job,
                  job_id=pr.job_id, level=pr.base_level, job_level=pr.job_level, server=pr.server,
                  start_time=pr.recorded_at.isoformat(), end_time=session.end_time.isoformat(),
                  duration_ms=pr.duration_ms, maps=session.maps,
                  map_names=[gamedata.maps.name(m) for m in session.maps], packet_ver=pr.packet_ver,
                  experimental=bool(settings.get('experimental_packets')), price_basis=basis,
                  analyzed_at=datetime.datetime.now().isoformat(timespec='seconds')),
        window=dict(label=label, start_ms=start_ms, end_ms=end_ms, duration_ms=dur,
                    map=_window_map(session, start_ms, end_ms)),
        segments=[dict(index=s.index, map=s.map_name, map_name=gamedata.maps.name(s.map_name), start_ms=s.start_ms,
                       end_ms=s.end_ms, duration_ms=s.duration_ms, is_hunt=s.is_hunt, reason=s.hunt_reason)
                  for s in session.segments],
        kpi=kpi, combat=cb, skills=sk, buffs=bf, vitals=vt, monsters=mo, exp=ex, zeny=zn, loot=lt, profit=pf,
        tracking=dict(drops=tracking.drops(ctx), quests=tracking.quests(ctx, getattr(gamedata, 'quest_title', None)),
                      stores=tracking.stores(ctx), item_use=tracking.item_use(ctx)),
        movement=dict(distance_cells=mv['distance_cells'], idle_ms=mv['idle_ms'], points=len(mv['points']),
                      monster_spawns=len(mv['monster_spawns']), note=mv['note']),
        rolling_dps={str(b): timeline.rolling_dps(pts, start_ms, end_ms, b) for b in ROLLING},
        parser=dict(events=len(pr.events), packets=len(pr.packets),
                    decoded={'0x%04x' % k: v for k, v in pr.decoded_counts.most_common()},
                    unknown={'0x%04x' % k: v for k, v in pr.unknown_counts.most_common()},
                    unsupported={'0x%04x' % k: v for k, v in pr.unsupported_counts.most_common()}),
    )
    rep['summary_text'] = summary_text(rep)
    return rep, session, ctx


def _window_map(session, a, b):
    ms = [s.map_name for s in session.segments if s.start_ms < b and s.end_ms > a and s.map_name]
    return ms[0] if len(set(ms)) == 1 else (', '.join(dict.fromkeys(ms)) if ms else None)


# ---------------------------------------------------------------- 요약 문장(규칙 기반)
def fmt_num(v, digits=2):
    if v is None:
        return '-'
    a = abs(v)
    for unit, div in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if a >= div:
            return ('%.' + str(digits) + 'f%s') % (v / div, unit)
    return '{:,}'.format(int(round(v)))


def summary_text(r):
    lines = []
    m, w, cb, kpi = r['meta'], r['window'], r['combat'], r['kpi']
    lines.append('%s (%s Lv.%s) · %s · 분석 구간 %s (%s)' % (
        m['character'] or '?', m['job'], m['level'] or '?', w.get('map') or '-', w['label'],
        _dur(w['duration_ms'])))
    if cb['available'] and cb['attack_count']:
        lines.append('총 %s의 피해를 주었고 Combat DPS는 %s입니다 (전투시간 %s).' % (
            fmt_num(cb['total_damage']), fmt_num(cb['dps_combat']), _dur(cb['combat_ms'])))
        if cb['kills']:
            lines.append('몬스터 %s마리를 처치했습니다 (평균 처치시간 %.2f초).' % (
                '{:,}'.format(cb['kills']), cb['kill_time_avg'] or 0))
    else:
        lines.append('이 구간에는 전투(피해) 기록이 없습니다.')
    sk = [s for s in r['skills']['skills'] if s['share']]
    if sk:
        lines.append('가장 높은 피해 비중 스킬은 %s이며 전체 피해의 %.1f%%입니다.' % (sk[0]['name'], sk[0]['share']))
    elif r['skills']['skills']:
        top = r['skills']['skills'][0]
        lines.append('가장 많이 사용한 스킬은 %s (%d회)입니다.' % (top['name'], top['uses']))
    bf = r['buffs']['buffs']
    if bf:
        lines.append('%s 가동률은 %.1f%%입니다.' % (bf[0]['name'], bf[0]['uptime'] or 0))
    if r['exp']['available']:
        lines.append('Base EXP %s (시간당 %s).' % (fmt_num(r['exp']['base_total']), fmt_num(r['exp']['base_per_hour'])))
    if r['profit'].get('available'):
        lines.append('예상 순이익은 %s Z (시간당 %s Z)입니다.' % (fmt_num(r['profit']['profit']),
                                                         fmt_num(r['profit']['per_hour'])))
        for it in r['loot'].get('rare', [])[:3]:
            lines.append('★ %s 획득 — 예상가치 %s Z' % (it['name'], '{:,}'.format(it['value'] or 0)))
    elif r['zeny'].get('available'):
        lines.append('Zeny 순변화: %s Z.' % '{:+,}'.format(r['zeny']['net']))
    return lines


def _dur(ms):
    s = int((ms or 0) // 1000)
    return '%02d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60)


# ---------------------------------------------------------------- A/B 비교
COMPARE_METRICS = [
    ('사냥시간(초)', lambda r: r['window']['duration_ms'] / 1000, False),
    ('총 피해', lambda r: r['combat']['total_damage'] if r['combat']['available'] else None, True),
    ('Combat DPS', lambda r: r['combat']['dps_combat'], True),
    ('전체 DPS', lambda r: r['combat']['dps_total'], True),
    ('처치 수', lambda r: r['kpi']['kills'], True),
    ('Kill/min', lambda r: r['kpi']['kills_per_hour'] / 60 if r['kpi']['kills_per_hour'] is not None else None, True),
    ('평균 Kill Time(초)', lambda r: r['combat']['kill_time_avg'], False),
    ('Base EXP/h', lambda r: r['kpi']['base_exp_per_hour'], True),
    ('Job EXP/h', lambda r: r['kpi']['job_exp_per_hour'], True),
    ('Profit/h', lambda r: r['kpi']['profit_per_hour'], True),
    ('Consumable/h', lambda r: r['kpi']['consumable_per_hour'], False),
]


def compare(reports, labels):
    """reports: [report, ...] (2개 이상). 반환: dict(rows, skill_rows, buff_rows, warnings)"""
    base = reports[0]
    rows = []
    for name, fn, higher_better in COMPARE_METRICS:
        vals = [_safe(fn, r) for r in reports]
        rows.append(dict(metric=name, values=vals, diffs=[_pct(vals[0], v) for v in vals],
                         higher_better=higher_better))
    skill_ids = []
    for r in reports:
        for s in r['skills']['skills']:
            if s['skill_id'] not in skill_ids:
                skill_ids.append(s['skill_id'])
    skill_rows = []
    for sid in skill_ids:
        per = [next((s for s in r['skills']['skills'] if s['skill_id'] == sid), None) for r in reports]
        name = next(s['name'] for s in per if s)
        dmg = [s['total_damage'] if s else None for s in per]
        skill_rows.append(dict(skill_id=sid, name=name, damage=dmg, dps=[s['dps'] if s else None for s in per],
                               uses=[s['uses'] if s else None for s in per], diffs=[_pct(dmg[0], v) for v in dmg]))
    efsts = []
    for r in reports:
        for b in r['buffs']['buffs']:
            if b['efst'] not in efsts:
                efsts.append(b['efst'])
    buff_rows = []
    for ef in efsts:
        per = [next((b for b in r['buffs']['buffs'] if b['efst'] == ef), None) for r in reports]
        buff_rows.append(dict(efst=ef, name=next(b['name'] for b in per if b),
                              uptime=[b['uptime'] if b else None for b in per]))
    return dict(labels=labels, rows=rows, skill_rows=skill_rows, buff_rows=buff_rows, warnings=warnings(reports))


def warnings(reports):
    out = []
    maps = [r['window'].get('map') for r in reports]
    if len(set(maps)) > 1:
        out.append('사냥터가 다릅니다: %s' % ' / '.join(str(m) for m in maps))
    mobsets = [set(m['class_id'] for m in r['monsters']['monsters'][:10] if m['kills'] or m['encounters'])
               for r in reports]
    for i, s in enumerate(mobsets[1:], 1):
        if s and mobsets[0]:
            j = len(s & mobsets[0]) / len(s | mobsets[0])
            if j < 0.5:
                out.append('몬스터 구성이 크게 다릅니다 (겹침 %.0f%%)' % (j * 100))
    lv = [r['meta'].get('level') for r in reports]
    if all(lv) and max(lv) - min(lv) > 5:
        out.append('캐릭터 레벨 차이가 큽니다: %s' % ' / '.join(map(str, lv)))
    jobs = [r['meta'].get('job_id') for r in reports]
    if len(set(jobs)) > 1:
        out.append('직업이 다릅니다')
    durs = [r['window']['duration_ms'] for r in reports]
    if min(durs) and max(durs) / min(durs) > 2:
        out.append('분석 시간 길이가 2배 이상 차이납니다')
    if not all(r['combat']['available'] for r in reports):
        out.append('전투 기록이 없는 Replay가 포함되어 있습니다')
    return out


def _safe(fn, r):
    try:
        return fn(r)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def _pct(a, b):
    if a in (None, 0) or b is None:
        return None
    return (b - a) / abs(a) * 100
