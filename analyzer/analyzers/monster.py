"""MonsterAnalyzer: 몬스터 종류별 조우/처치/피해/처치시간."""
import collections

from analyzer.analyzers.context import NO_DATA
from analyzer.events import EventType as E
from analyzer.normalizer import OBJ_MOB


def analyze(ctx):
    me, actors = ctx.player, ctx.pr.actors
    mob = lambda aid: actors.get(aid, {}).get('obj_type') == OBJ_MOB
    cls = lambda aid: actors.get(aid, {}).get('class_id')
    st = collections.defaultdict(lambda: dict(encounters=set(), kills=0, damage=0, hits=0, kill_times=[], taken=0,
                                              name=''))
    first_hit = {}
    for e in ctx.events:
        et = e.event_type
        if et == E.MAP_CHANGE:
            first_hit.clear()
        elif et == E.UNIT_VANISH:
            first_hit.pop(e.actor_id, None)
        elif et in (E.MONSTER_SPAWN, E.MONSTER_MOVE):
            r = st[e.data['class_id']]
            r['encounters'].add(e.actor_id)
            r['name'] = r['name'] or e.data.get('name', '')
        elif et == E.DAMAGE:
            if e.actor_id == me and mob(e.target_id) and not e.data.get('miss'):
                r = st[cls(e.target_id)]
                r['damage'] += e.data['amount']
                r['hits'] += 1
                r['encounters'].add(e.target_id)
                first_hit.setdefault(e.target_id, e.timestamp_ms)
            elif e.target_id == me and mob(e.actor_id) and not e.data.get('miss'):
                st[cls(e.actor_id)]['taken'] += e.data['amount']
        elif et == E.MONSTER_DEATH and e.actor_id in first_hit:
            r = st[e.data.get('class_id') or cls(e.actor_id)]
            r['kills'] += 1
            r['kill_times'].append(e.timestamp_ms - first_hit.pop(e.actor_id))
    names = {c: r['name'] for c, r in st.items() if c is not None and r['name']}
    ctx.gamedata.monsters.observe(names)
    rows = []
    for c, r in st.items():
        if c is None:
            continue
        kt = r['kill_times']
        rows.append(dict(class_id=c, name=r['name'] or ctx.gamedata.monsters.name(c), encounters=len(r['encounters']),
                         kills=r['kills'], damage=r['damage'], avg_damage=r['damage'] / r['hits'] if r['hits'] else None,
                         kill_time_avg=sum(kt) / len(kt) / 1000 if kt else None,
                         kill_time_min=min(kt) / 1000 if kt else None, kill_time_max=max(kt) / 1000 if kt else None,
                         taken=r['taken'], exp=None))
    rows.sort(key=lambda r: (-r['kills'], -r['encounters']))
    return dict(available=bool(rows), note='' if rows else NO_DATA + ' (몬스터 등장 0건)', monsters=rows,
                exp_note='몬스터별 EXP: EXP 패킷 미검증으로 미지원')
