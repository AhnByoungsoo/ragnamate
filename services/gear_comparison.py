"""리플레이 착용 스냅샷·대상별 피해 범위와 처치선을 읽기 전용으로 계산한다.

장비 설명 문자열로 완전한 교체 피해를 추정하지 않는다. 피해 범위는 기록 또는 사용자가 확인한 계산값만 쓴다.
"""
import math

from analyzer.events import MEASURED, EventType as E
from services.equipment_details import clean_description


def worn_snapshot(parsed, item_db):
    snapshots = [(tm, items) for typ, items, tm in getattr(parsed, 'inventory_lists', []) if typ == 0]
    if not snapshots:
        return dict(known=False, time_ms=None, items=[])
    tm, items = max(snapshots, key=lambda pair: pair[0])
    origin = parsed.packets[0][0] if parsed.packets else 0
    db = getattr(item_db, 'items', {}) or {}
    result = []
    for raw in items:
        # equip=True는 장비 종류만 뜻한다. 실제 착용 여부는 wear_state로 판단한다.
        if not raw.get('equip') or not raw.get('wear_state'):
            continue
        iid = raw['id']
        item = db.get(iid) or {}
        cards = [c for c in raw.get('cards', []) if c]
        result.append(dict(item_id=iid, name=item.get('name') or '아이템 #%s' % iid,
                           refine=raw.get('refine'), grade=raw.get('grade'), wear_state=raw['wear_state'],
                           index=raw.get('index'), cards=cards, options=list(raw.get('options') or []),
                           description=clean_description(item.get('description')), raw=dict(raw)))
    return dict(known=True, time_ms=max(0, tm - origin), items=result)


def damage_observations(parsed, from_ms=0):
    """같은 대상·스킬·서버 tick의 통지를 합산. 화면 div 수로 총피해를 다시 곱하지 않는다."""
    alive, groups, targets, serial = {}, {}, {}, 0
    for event in sorted(parsed.events, key=lambda e: e.timestamp_ms):
        kind = event.event_type
        if kind == E.MAP_CHANGE:
            alive.clear()
        elif kind == E.MONSTER_SPAWN:
            serial += 1
            data = event.data
            cid = data.get('class_id')
            if cid is None:
                continue
            hp = data.get('max_hp')
            hp = hp if type(hp) in (int, float) and math.isfinite(hp) and hp > 0 and event.confidence == MEASURED else None
            alive[event.actor_id] = (cid, serial)
            target = targets.setdefault(cid, dict(class_id=cid, name=data.get('name') or '몬스터 #%s' % cid, hp_values=set()))
            if hp:
                target['hp_values'].add(hp)
        elif kind in (E.MONSTER_DEATH, E.UNIT_VANISH):
            alive.pop(event.actor_id, None)
        elif (kind == E.DAMAGE and event.actor_id == parsed.player_aid and event.timestamp_ms >= from_ms
              and event.data.get('skill') is not None and event.target_id in alive):
            cid, spawn = alive[event.target_id]
            skill = event.data['skill']
            tick = event.data.get('tick', event.timestamp_ms)
            key = (cid, spawn, event.target_id, skill, tick)
            amount = event.data.get('amount')
            if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
                continue
            group = groups.setdefault(key, dict(class_id=cid, skill_id=skill, amount=0, verified=True))
            group['amount'] += amount
            group['verified'] &= event.confidence == MEASURED
    for target in targets.values():
        values = target.pop('hp_values')
        target['hp'] = next(iter(values)) if len(values) == 1 else None
        target['hp_source'] = '리플레이 등장 패킷' if len(values) == 1 else 'HP 미확인 또는 서로 다른 값'
    return dict(targets=list(targets.values()), samples=list(groups.values()))


def observed_range(observations, class_id, skill_id, include_unverified=False):
    samples = [r for r in observations['samples'] if r['class_id'] == class_id and r['skill_id'] == skill_id
               and (r['verified'] or include_unverified)]
    positive = [r['amount'] for r in samples if r['amount'] > 0]
    return dict(min=min(positive) if positive else None, max=max(positive) if positive else None,
                count=len(positive), misses=sum(r['amount'] == 0 for r in samples),
                unverified=sum(not r['verified'] for r in samples))


def hits_to_kill(hp, minimum, maximum):
    values = (hp, minimum, maximum)
    if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in values):
        return None
    if minimum > maximum:
        return None
    fast, safe = math.ceil(hp / maximum), math.ceil(hp / minimum)
    return dict(fast=fast, safe=safe, guaranteed_one=safe == 1, possible_one=fast == 1)


def description_difference(current, candidate):
    """원문 줄 단위 추가·제거. 숫자를 합산한 적용 효과로 취급하지 않는다."""
    old = [x.strip() for x in clean_description(current).splitlines() if x.strip() and x.strip() != '_']
    new = [x.strip() for x in clean_description(candidate).splitlines() if x.strip() and x.strip() != '_']
    return dict(added=[x for x in new if x not in old], removed=[x for x in old if x not in new])


def checked_refinement_summary(item_id, refine):
    """560060의 공식 설명(2026-10-03 확인)만 계산. 전체 피해/제련 기본 ATK/세트 효과와 구분한다.

    https://ro.gnjoy.com/guide/runemidgarts/itemview.asp?ID=560060&itemSeq=1
    """
    if item_id != 560060 or type(refine) is not int or not 0 <= refine <= 20:
        return ''
    pairs, triples = refine // 2, refine // 3
    skill = 15 + triples * 5 + (15 if refine >= 11 else 0)
    melee = 25 if refine >= 7 else 0
    patk = 7 if refine >= 9 else 0
    return ('[확인된 설명의 제련 보너스 합계 · +%d]\n'
            'ATK 추가 +%d, ATK +%d%% (제련 자체의 기본 공격력 보정은 제외).\n'
            '멸화격 피해: 등급 없음/D %d%%, C/B/A %d%%.\n'
            '근접 물리 피해: 등급 없음/D/C %d%%, B/A %d%%.\n'
            'P.ATK: 등급 없음 %d, D/C/B %d, A %d.\n'
            'MHP +5%%. SP 소모 감소: %d%%.\n'
            '이 무기의 원문 효과만 합산한 참고표입니다. 세트·다른 장비·카드·인챈트는 제외하며, 전체 피해 증가율이 아닙니다.') % (
                refine, pairs * 25, pairs, skill, skill + 15, melee, melee + 10,
                patk, patk + 5, patk + 5 + pairs * 3, 10 if refine >= 9 else 0)
