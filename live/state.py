"""Replay-clock reducer. No wall clock, UI, database or game process access."""
from collections import Counter, OrderedDict, deque
from dataclasses import asdict, dataclass

from analyzer.analyzers import tracking
from analyzer.events import EARNED, EventType as E, MEASURED

NO_TIMER_MS = 9999       # 서버가 '끝나는 시각 없음'으로 보내는 값(실측: 무게 70%·탑승·카트·노점 도우미가 모두 정확히 9999)


@dataclass
class Resource:
    value: int | None = None
    max_value: int | None = None
    updated_at: int | None = None
    source_event: str | None = None
    confidence: str = 'UNKNOWN'
    predicted: bool = False


class EntityRegistry:
    def __init__(self, limit=8192):
        self.entities = OrderedDict()
        self.generation = 0
        self.limit = limit
        self.evictions = 0

    def spawn(self, e):
        old = self.entities.get(e.actor_id)
        if old and old['alive'] and e.event_type == E.MONSTER_MOVE:
            old.update(position=(e.x, e.y))
            return old
        if old and old['alive'] and old['map'] == e.map_name and old['class_id'] == e.data.get('class_id'):
            old.update(position=(e.x, e.y), name=e.data.get('name') or old['name'])
            return old
        self.generation += 1
        ent = dict(runtime_id=e.actor_id, generation=self.generation,
                   type={E.PLAYER_SPAWN: 'PLAYER', E.MONSTER_SPAWN: 'MONSTER', E.MONSTER_MOVE: 'MONSTER',
                         E.NPC_SPAWN: 'NPC'}.get(e.event_type, 'UNKNOWN'),
                   class_id=e.data.get('class_id'), name=e.data.get('name'), map=e.map_name,
                   position=(e.x, e.y), spawn=e.timestamp_ms, death=None, despawn=None,
                   alive=True, damaged=False)
        self.entities[e.actor_id] = ent
        self.entities.move_to_end(e.actor_id)
        if len(self.entities) > self.limit:
            self.entities.popitem(last=False)
            self.evictions += 1
        return ent

    def clear_map(self):
        self.entities.clear()


class GameStateReducer:
    def __init__(self, player, start_zeny=None, initial_map=None, combat_gap_ms=3000):
        self.player = player
        self.map = initial_map
        self.position = None
        self.entities = EntityRegistry()
        self.resources = {k: Resource() for k in ('HP', 'SP', 'AP')}
        self.buffs = {}
        self.skills = {}
        self.inventory = {}
        self.inventory_first = self.inventory_last = None
        self.inventory_snapshots = 0
        self.item_gains = Counter()
        self.gains = deque(maxlen=50)        # 최근 획득 [dict(seq, at, item_id, amount, source)] — 득템 알림용
        self.gain_seq = 0
        self.gain_packets = False            # 획득 패킷을 한 번이라도 봤는가(봤으면 인벤토리 비교를 쓰지 않는다)
        self.item_last = {}                  # 아이템 번호 → 마지막으로 얻은 시각(리플레이 시계 ms)
        self.flows = deque(maxlen=50)        # 최근 제니 증감 [dict(seq, at, amount, kind)]
        self.flow_seq = 0
        self.income = Counter()              # kind → 제니 수입 합계
        self.spent = 0
        self.drops = deque(maxlen=50)        # 방금 바닥에 떨어진 것 [dict(seq, at, item_id, amount)] — 고가 드랍 알림용
        self.drop_seq = 0
        self.dropped = self.drop_picked = self.drop_missed = 0
        self.cooldowns = {}                  # 스킬 → dict(ready_at, total_ms) 서버가 알려 준 재사용 대기
        self.monster_hp = {}                 # 몬스터 → HP %(5% 단위)
        self.quest_events = []               # 퀘스트 받음/진행/끝 이벤트(진행 상황은 view 에서 계산)
        self.queue = None                    # 인던 대기 중이면 dict(name, position, at)
        self.skill_fails = Counter()         # 스킬 → 실패 횟수
        self.zeny_start = self.zeny = start_zeny
        self.base_exp = self.job_exp = 0
        self.exp_available = False
        self.damage = self.hits = self.kills = self.received = 0
        self.clock = 0
        self.combat_gap_ms = combat_gap_ms
        self.combat_ms = 0
        self.last_damage = None
        self.last_combat = None
        self.combat_state = 'OUT_OF_COMBAT'
        self.target = None
        self.rolling = deque(maxlen=10001)  # at most one bucket per replay millisecond
        self.recent = deque(maxlen=500)
        self.resource_history = deque(maxlen=500)
        self.corrections = 0
        self.unverified_events = 0
        self.extensions = deque(maxlen=200)  # future build/planner consumers; no automatic actions

    def advance(self, tm):
        self.clock = max(self.clock, tm)
        while self.rolling and self.rolling[0][0] <= self.clock - 10000:
            self.rolling.popleft()
        if self.last_combat is not None and self.clock - self.last_combat > self.combat_gap_ms:
            self.combat_state = 'OUT_OF_COMBAT'
        for b in self.buffs.values():
            if b['active'] and b['expected_end'] is not None and self.clock >= b['expected_end']:
                b.update(active=False, ending='ESTIMATED', confidence='MEDIUM')

    def apply(self, e, offset=None, packet_id=None):
        self.advance(e.timestamp_ms)
        t, d, typ = e.timestamp_ms, e.data, e.event_type
        self.recent.append(dict(timestamp=t, offset=offset, packet_id=packet_id, event_type=typ,
                                actor=e.actor_id, target=e.target_id, confidence=e.confidence))
        if e.confidence != MEASURED:
            self.unverified_events += 1
        if typ == E.MAP_CHANGE:
            self.monster_hp.clear()
            self.map, self.position = e.map_name, (e.x, e.y)
            self.entities.clear_map()
            self.target = None
            self.last_combat = None
            self.combat_state = 'OUT_OF_COMBAT'
            for row in self.skills.values():
                row['pending_cast'] = None
            return
        if typ in (E.MONSTER_SPAWN, E.NPC_SPAWN, E.PLAYER_SPAWN, E.UNIT_SPAWN, E.MONSTER_MOVE):
            self.entities.spawn(e)
        elif typ in (E.MONSTER_DEATH, E.UNIT_VANISH):
            ent = self.entities.entities.get(e.actor_id)
            if ent and ent['alive']:
                if typ == E.MONSTER_DEATH:
                    if ent['type'] == 'MONSTER' and ent['damaged']:
                        self.kills += 1
                    ent['death'] = t
                ent.update(alive=False, despawn=t)
        elif typ == E.PLAYER_MOVE and e.actor_id == self.player:
            self.position = (e.x, e.y)
        elif typ in (E.HP_CHANGE, E.SP_CHANGE, 'AP_CHANGE') and e.actor_id == self.player:
            key = typ.split('_')[0]
            r = self.resources[key]
            if 'max' in d:
                r.max_value = d['max']
            if 'value' in d:
                if r.predicted and r.value is not None and r.value != d['value']:
                    self.corrections += 1
                    self.resource_history.append(dict(at=t, resource=key, before=r.value,
                                                      after=d['value'], reason='absolute reconciliation'))
                r.value, r.predicted = d['value'], False
            elif 'delta' in d and r.value is not None:
                r.value += d['delta']
                r.predicted = True
            r.updated_at, r.source_event = t, '%s:%s' % (offset, typ)
            r.confidence = 'CONFIRMED' if e.confidence == MEASURED and not r.predicted else 'MEDIUM'
            self.resource_history.append(dict(at=t, resource=key, value=r.value, max=r.max_value))
        elif typ in (E.BUFF_START, E.BUFF_END) and e.actor_id == self.player:
            bid = d['efst']
            b = self.buffs.setdefault(bid, dict(buff_id=bid, started_at=None, refreshed_at=None,
                expected_end=None, stacks=None, active=False, confidence='UNKNOWN', ending=None, timed=True,
                measured_start=None, measured_ms=0))
            if typ == E.BUFF_START:
                if b['measured_start'] is None:
                    b['measured_start'] = t
                    b['started_at'] = t
                b['refreshed_at'] = t
                remaining = d.get('remain_ms')
                # 서버는 끝나는 시각이 없는 상태(무게 70%·탑승·카트 등)를 9999ms로 보낸다 — 10초 타이머가 아니다
                b['timed'] = NO_TIMER_MS not in (remaining, d.get('max_ms'))
                if not b['timed']:
                    remaining = None
                b.update(active=True, expected_end=t + remaining if remaining is not None and remaining > 0 else None,
                         confidence='CONFIRMED' if e.confidence == MEASURED else 'MEDIUM', ending=None)
                # vals are not assumed to be stacks: semantics are buff-specific.
                if 'stacks' in d:
                    b['stacks'] = d['stacks']
            else:
                if b['measured_start'] is not None:
                    b['measured_ms'] += max(0, t - b['measured_start'])
                b.update(active=False, measured_start=None, ending='CONFIRMED')
        elif typ == E.ZENY_CHANGE and e.actor_id == self.player:
            self.zeny = d['value']
        elif typ in (E.BASE_EXP_GAIN, E.JOB_EXP_GAIN) and e.actor_id == self.player:
            self.exp_available = True
            if typ == E.BASE_EXP_GAIN:
                self.base_exp += d['amount']
            else:
                self.job_exp += d['amount']
        elif typ == E.INVENTORY_UPDATE:
            self.inventory = {i['index']: dict(i) for i in d['items']}
            counts = Counter()
            for item in d['items']:
                counts[item['id']] += item['amount']
            if self.inventory_first is None:
                self.inventory_first = counts
            self.inventory_last = counts
            self.inventory_snapshots += 1
        elif typ == E.ITEM_GAIN:
            self.gain_packets = True
            if d.get('source', EARNED[0]) in EARNED:         # 창고·카트에서 꺼낸 것, 산 것은 세지 않는다
                self.item_gains[d['item_id']] += d.get('amount', 1)
                self.item_last[d['item_id']] = t
                self.gain_seq += 1
                self.gains.append(dict(seq=self.gain_seq, at=t, item_id=d['item_id'], amount=d.get('amount', 1),
                                       source=d.get('source')))
        elif typ == E.ITEM_DROP:
            self.dropped += 1
            self.drop_seq += 1
            self.drops.append(dict(seq=self.drop_seq, at=t, item_id=d['item_id'], amount=d['amount'] or 1))
        elif typ == E.ITEM_DROP_END:
            if d['picked']:
                self.drop_picked += 1
            else:
                self.drop_missed += 1
        elif typ == E.SKILL_DELAY:
            self.cooldowns[d['skill']] = dict(ready_at=t + d['delay_ms'], total_ms=d.get('total_ms') or d['delay_ms'])
        elif typ == E.SKILL_FAIL:
            self.skill_fails[d['skill']] += 1
        elif typ == E.MONSTER_HP:
            self.monster_hp[e.actor_id] = d['hp_pct']
        elif typ in (E.QUEST_ADD, E.QUEST_PROGRESS, E.QUEST_REMOVE):
            self.quest_events.append(e)
        elif typ == E.INSTANCE_QUEUE:                # 순번만 온 갱신은 이름을 이어 쓴다
            self.queue = dict(name=d['name'] or (self.queue or {}).get('name', ''), position=d['position'], at=t)
        elif typ == E.INSTANCE_READY:
            self.queue = None
        elif typ == E.ZENY_FLOW and e.actor_id == self.player:
            self.flow_seq += 1
            self.flows.append(dict(seq=self.flow_seq, at=t, amount=d['amount'], kind=d['kind']))
            if d['amount'] > 0:
                self.income[d['kind']] += d['amount']
            else:
                self.spent -= d['amount']
        if typ == E.DAMAGE:
            if e.actor_id == self.player or e.target_id == self.player:
                self.last_combat = t
                self.combat_state = 'IN_COMBAT'
            if e.target_id == self.player:
                self.received += d.get('amount', 0)
            if e.actor_id == self.player:
                if self.last_damage is not None:
                    self.combat_ms += min(max(0, t - self.last_damage), self.combat_gap_ms)
                self.last_damage = t
                self.target = e.target_id
                if not d.get('miss'):
                    amount = d['amount']
                    self.damage += amount
                    self.hits += 1
                    if self.rolling and self.rolling[-1][0] == t:
                        self.rolling[-1] = (t, self.rolling[-1][1] + amount)
                    else:
                        self.rolling.append((t, amount))
                    ent = self.entities.entities.get(e.target_id)
                    if ent:
                        ent['damaged'] = True
        sid = d.get('skill')
        if sid is not None and e.actor_id == self.player:
            r = self.skills.setdefault(sid, dict(skill_id=sid, casts=0, hits=0, damage=0, uses_explicit=0,
                groups=0, group_start=None, pending_cast=None, phase='IDLE', connected_hits=0))
            if typ == E.SKILL_CAST_START:
                r['casts'] += 1
                r.update(pending_cast=t, phase='CAST_START', connected_hits=0)
            elif typ == E.SKILL_USE:
                r['uses_explicit'] += 1
                r['phase'] = 'SKILL_USE' if d.get('result', 1) else 'FAIL'
            elif typ == E.DAMAGE:
                r['hits'] += 1
                r['damage'] += 0 if d.get('miss') else d['amount']
                if r['group_start'] is None or t - r['group_start'] > 50:
                    r['groups'] += 1
                    r['group_start'] = t
                r['connected_hits'] += max(1, d.get('div', 1))
                r['phase'] = 'DAMAGE'

    def metrics(self):
        diff = Counter(self.inventory_last or {})
        diff.subtract(self.inventory_first or {})
        loot = dict(self.item_gains) if self.gain_packets else {k: v for k, v in diff.items() if v > 0}
        return dict(damage=self.damage, kills=self.kills, base_exp=self.base_exp, job_exp=self.job_exp,
            zeny=self.zeny - self.zeny_start if self.zeny is not None and self.zeny_start is not None else None,
            loot=loot, skills={k: dict(casts=r['casts'], hits=r['hits'],
                uses=max(r['groups'], r['uses_explicit']) or r['casts']) for k, r in self.skills.items()},
            buff_ms={k: b['measured_ms'] + (self.clock - b['measured_start'] if b['measured_start'] is not None else 0)
                     for k, b in self.buffs.items()})

    def view(self):
        seconds = self.clock / 1000
        combat_ms = max(1000, self.combat_ms) if self.last_damage is not None else 0
        metrics = self.metrics()
        return dict(map=self.map, position=self.position, duration_ms=self.clock,
            combat_available=self.last_damage is not None,
            combat_ms=combat_ms, combat_state=self.combat_state,
            resources={k: dict(asdict(r), stale=r.updated_at is None or self.clock - r.updated_at > 15000)
                       for k, r in self.resources.items()},
            buffs=[dict(b) for b in self.buffs.values() if b['active'] or b['ending'] == 'ESTIMATED'],
            skills=sorted([dict(r) for r in self.skills.values()], key=lambda r: -r['damage'])[:20],
            rolling_dps={str(w): (sum(a for t, a in self.rolling if t > self.clock - w * 1000) / w
                                 if self.last_damage is not None else None)
                         for w in (1, 5, 10)},
            session_dps=self.damage / seconds if seconds and self.last_damage is not None else None,
            combat_dps=self.damage * 1000 / combat_ms if combat_ms else None,
            exp_per_hour=self.base_exp * 3600 / seconds if seconds and self.exp_available else None,
            zeny_per_hour=metrics['zeny'] * 3600 / seconds if seconds and metrics['zeny'] is not None else None,
            profit_per_hour=None, loot_available=self.inventory_snapshots >= 2 or self.gain_packets,
            loot_confidence='INVENTORY_NET_CHANGE' if not self.gain_packets else 'CONFIRMED',
            drop_attribution='UNKNOWN', metrics=metrics, monsters=self.map_monsters(),
            gains=list(self.gains), flows=list(self.flows), drops=list(self.drops),
            drop_counts=dict(dropped=self.dropped, picked=self.drop_picked, missed=self.drop_missed),
            # 1초 넘게 남은 재사용 대기만(짧은 후딜은 화면에 띄울 틈이 없다)
            cooldowns=sorted((dict(skill=k, remain_ms=c['ready_at'] - self.clock, total_ms=c['total_ms'])
                              for k, c in self.cooldowns.items() if c['ready_at'] - self.clock > 1000),
                             key=lambda c: c['remain_ms']),
            target_hp=self.monster_hp.get(self.target),
            queue=dict(self.queue) if self.queue else None,
            quests=[dict(quest=q['quest'], removed=q['removed'], updates=q['updates'], at_ms=q['at_ms'], goals=q['goals'])
                    for q in tracking.quest_state(self.quest_events).values()],
            ledger=dict(items=dict(self.item_gains), income=dict(self.income), spent=self.spent,
                        last=dict(self.item_last)))

    def map_monsters(self):
        """현재 맵에 들어온 뒤 본 몬스터 종류별 마리 수(맵 이동 시 초기화)."""
        seen = {}
        for ent in self.entities.entities.values():
            if ent['type'] == 'MONSTER' and ent['class_id'] is not None:
                row = seen.setdefault(ent['class_id'], dict(class_id=ent['class_id'], name=ent['name'], weight=0))
                row['weight'] += 1
                row['name'] = row['name'] or ent['name']
        return sorted(seen.values(), key=lambda r: -r['weight'])
