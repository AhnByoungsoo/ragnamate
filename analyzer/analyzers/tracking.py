"""패킷으로 따라가는 것들: 스킬 재사용 대기·실패, 바닥 드랍(놓친 것·몬스터별), 퀘스트 사냥 수, 노점 검색 기록, 아이템 사용.

칸 배치는 해외 공개 자료(rAthena·Hercules·OpenKore)에서 배웠고 값은 내 리플레이로 확인했다(analyzer/packets.py).
모르는 것은 None 으로 둔다 — 드랍 주인을 모르면 몬스터별 집계에 넣지 않는다.
"""
import collections

from analyzer.events import EventType as E

# 스킬 실패 사유(서버 구현체 rAthena 의 useskill_fail_cause). 게임 안 문구와 대조한 것은 아니다.
FAIL_CAUSE = {0: '스킬 레벨 부족', 1: 'SP 부족', 2: 'HP 부족', 3: '재료 부족', 4: '스킬 딜레이 중', 5: '제니 부족',
              6: '무기가 맞지 않음', 7: '레드 젬스톤 없음', 8: '블루 젬스톤 없음', 9: '무게 초과', 10: '사용 실패',
              11: '대상이 맞지 않음', 23: '조건이 맞지 않음', 26: '쓸 수 없는 위치', 73: '콤보 조건이 맞지 않음',
              74: '기구 부족', 75: '폭기 상태가 아님'}
DELAY_BLOCKED = 4            # '스킬 딜레이 중' — 너무 빨리 눌러 씹힌 입력


def cause_text(cause):
    return FAIL_CAUSE.get(cause, '사유 %d' % cause)


def skills(ctx):
    """{스킬 번호: dict(cooldown_ms, failures, blocked, causes)} — 서버가 알려 준 재사용 대기와 실패 횟수."""
    delays, fails = collections.defaultdict(list), collections.defaultdict(collections.Counter)
    for e in ctx.events:
        if e.event_type == E.SKILL_DELAY and not e.data.get('carried'):
            delays[e.data['skill']].append(e.data['delay_ms'])
        elif e.event_type == E.SKILL_FAIL:
            fails[e.data['skill']][e.data['cause']] += 1
    out = {}
    for sid in set(delays) | set(fails):
        values = collections.Counter(delays.get(sid) or [])
        causes = fails.get(sid) or {}
        out[sid] = dict(cooldown_ms=values.most_common(1)[0][0] if values else None,
                        failures=sum(causes.values()) or None, blocked=causes.get(DELAY_BLOCKED, 0),
                        causes=[(cause_text(c), n) for c, n in sorted(causes.items(), key=lambda kv: -kv[1])])
    return out


def drops(ctx):
    """바닥 드랍: 떨어진 것, 내가 주운 것, 놓친 것(남이 줍거나 사라짐), 몬스터별 실측 드랍.

    by_mob 의 rate 는 '그 종류 몬스터 처치 수 대비 그 아이템이 떨어진 횟수'다. 주인을 한 종류로 좁히지 못한 드랍
    (0.6초 안에 여러 종류가 죽음, 또는 화면에 들어왔을 때 이미 떨어져 있던 것)은 넣지 않고 unknown_owner 로 센다.
    """
    items, monsters = ctx.gamedata.items, ctx.gamedata.monsters
    dropped = picked = unknown = 0
    missed = collections.defaultdict(lambda: dict(count=0, amount=0))
    by_mob = collections.defaultdict(lambda: collections.defaultdict(lambda: dict(drops=0, amount=0)))
    kills = collections.Counter()
    for e in ctx.events:
        if e.event_type == E.MONSTER_DEATH:
            kills[e.data.get('class_id')] += 1
        elif e.event_type == E.ITEM_DROP:
            dropped += 1
            if e.data['mob'] is None:
                unknown += 1
            else:
                cell = by_mob[e.data['mob']][e.data['item_id']]
                cell['drops'] += 1
                cell['amount'] += e.data['amount'] or 1
        elif e.event_type == E.ITEM_DROP_END:
            if e.data['picked']:
                picked += 1
            else:
                cell = missed[e.data['item_id']]
                cell['count'] += 1
                cell['amount'] += e.data['amount'] or 1
    mob_rows = []
    for cls, cells in by_mob.items():
        n = kills.get(cls, 0)
        mob_rows.append(dict(
            class_id=cls, name=monsters.name(cls), kills=n,
            items=sorted((dict(item_id=i, name=items.name(i), drops=c['drops'], amount=c['amount'],
                               rate=c['drops'] * 100 / n if n else None) for i, c in cells.items()),
                         key=lambda r: -r['drops'])))
    mob_rows.sort(key=lambda r: -r['kills'])
    missed_rows = sorted((dict(item_id=i, name=items.name(i), quantity=c['amount'], count=c['count'])
                          for i, c in missed.items()), key=lambda r: -r['quantity'])
    return dict(available=bool(dropped or missed_rows), dropped=dropped, picked=picked,
                missed_count=sum(r['count'] for r in missed_rows), missed=missed_rows, by_mob=mob_rows,
                unknown_owner=unknown,
                note='' if dropped else '이 구간에는 바닥 드랍 기록이 없습니다')


def _goal(goals, index=None, mob_id=None):
    """퀘스트의 목표 한 칸. 진행 패킷은 '몇 번째 목표'(1부터)로, 목록 패킷은 몬스터 번호로 가리킨다."""
    if index is None:
        for g in goals:
            if g['mob_id'] == mob_id:
                return g
        index = len(goals) + 1
    while len(goals) < index:
        goals.append(dict(name='', mob_id=None, need=None, count=0))
    if mob_id is not None:
        goals[index - 1]['mob_id'] = mob_id
    return goals[index - 1]


def quest_state(events, title_of=None):
    """이벤트 → {퀘스트 번호: dict(quest, title, goals[{name, mob_id, need, count}], removed, updates, at_ms)}."""
    state = collections.OrderedDict()

    def quest(quest_id):
        return state.setdefault(quest_id, dict(quest=quest_id, title=title_of(quest_id) if title_of else None,
                                               goals=[], removed=False, updates=0, at_ms=None))
    for e in events:
        d = e.data
        if e.event_type == E.QUEST_ADD:
            q = quest(d['quest'])
            q['removed'] = False
            for i, g in enumerate(d['goals'], 1):
                _goal(q['goals'], i, g['mob_id']).update(name=g['name'], count=g['count'])
        elif e.event_type == E.QUEST_PROGRESS:
            q = quest(d['quest'])
            _goal(q['goals'], d.get('ident2'), d.get('mob_id')).update(need=d['need'], count=d['count'])
            if d.get('ident2') is not None:              # 목록(받을 때 한 번)이 아니라 실제로 한 마리 잡은 것
                q['updates'] += 1
                q['at_ms'] = e.timestamp_ms
        elif e.event_type == E.QUEST_REMOVE and d['quest'] in state:
            state[d['quest']]['removed'] = True
    return state


def quests(ctx, title_of=None):
    """퀘스트별 마지막 사냥 수. title_of(quest_id) → 제목(게임 클라이언트의 퀘스트 목록) 또는 None.

    need 는 목표들의 '채울 수' 합, count 는 '잡은 수' 합. 채울 수를 모르는 목표가 있으면 need 는 None."""
    rows = []
    for q in quest_state(ctx.events, title_of).values():
        goals = q['goals']
        known = bool(goals) and all(g['need'] is not None for g in goals)
        need = sum(g['need'] for g in goals) if known else None
        count = sum(g['count'] or 0 for g in goals)
        rows.append(dict(quest=q['quest'], title=q['title'], removed=q['removed'], updates=q['updates'],
                         at_ms=q['at_ms'], goals=goals, need=need, count=count,
                         complete=bool(need) and count >= need))
    return dict(available=bool(rows), quests=rows)


def stores(ctx):
    """노점: 게임 안 검색 결과, 열어 본 노점의 물건, 지나가며 본 간판 수."""
    items = ctx.gamedata.items
    search, opened, signs = [], [], collections.Counter()
    buying = []
    for e in ctx.events:
        if e.event_type == E.STORE_SEARCH:
            search += [dict(r, name=items.name(r['item_id']), at_ms=e.timestamp_ms) for r in e.data['rows']]
        elif e.event_type == E.STORE_ITEMS:
            opened += [dict(r, name=items.name(r['item_id']), at_ms=e.timestamp_ms) for r in e.data['rows']]
        elif e.event_type == E.STORE_SIGN:
            signs[e.data['kind']] += 1
            if e.data['kind'] == 'buy' and e.data['title'] not in buying:
                buying.append(e.data['title'])
    return dict(available=bool(search or opened or signs), search=search, opened=opened,
                sale_signs=signs['sale'], buy_signs=signs['buy'], buying=buying)


def item_use(ctx):
    """내가 쓴 아이템: 횟수(●)와 마지막에 남은 수."""
    items = ctx.gamedata.items
    used, last = collections.Counter(), {}
    for e in ctx.of(E.ITEM_USE):
        used[e.data['item_id']] += 1
        last[e.data['item_id']] = e.data['remaining']
    return [dict(item_id=i, name=items.name(i), uses=n, remaining=last[i]) for i, n in used.most_common()]
