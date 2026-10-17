"""Packet → ReplayEvent 표준화. 패킷은 한 번만 순회한다.

해석하지 못한 패킷은 버리지 않고 unknown 집계 + unknown_packets.log에 남긴다.
"""
import collections
import datetime
import logging
import struct
from dataclasses import dataclass, field

from analyzer.events import (ESTIMATE, FROM_CART, FROM_STORAGE, LOOT, MEASURED, NPC_SALE, OTHER_GAIN, OTHER_INCOME,
                             PURCHASE, SPEND, VEND_SALE, EventType as E, ReplayEvent)
from analyzer.packets import DEFAULT_PACKET_VER, registry

log = logging.getLogger('parser')
unknown_log = logging.getLogger('unknown_packets')

OBJ_PC, OBJ_NPC, OBJ_MOB, OBJ_EVT_NPC = 0, 1, 5, 6
VANISH_DEAD = 1
# 0x00b0 varID (샘플 검증: 5=현재HP(6초 주기 회복), 6=최대HP(맵 로딩 시 장비 반영으로 단계 상승),
# 7=현재SP(스킬 사용 시 감소), 8=최대SP, 24/25=무게, 0x00b1 20=Zeny(메타데이터 Zeny와 일치))
VAR_HP, VAR_MAXHP, VAR_SP, VAR_MAXSP, VAR_WEIGHT, VAR_ZENY = 5, 6, 7, 8, 24, 20
# 0x00b0 11=Base 레벨, 55=Job 레벨 (샘플 검증: 6개 파일의 Base 레벨업 8번이 모두 '녹화 시작 레벨 + 1'씩 이어짐 —
# 154→155, 190→191, 207→208, 208→209, 223→224→225→226. Job 레벨도 1씩 오름)
VAR_BASE_LEVEL, VAR_JOB_LEVEL = 11, 55
# ZC_NOTIFY_ACT3 action (샘플 관측: 2=앉기). 피해 관련 값은 rAthena 문서 기준
ACT_DAMAGE = {0: 'normal', 4: 'endure', 8: 'multi', 9: 'multi_endure', 10: 'critical', 11: 'lucky_dodge',
              13: 'multi_critical'}
ACT_OTHER = {1: 'pickup', 2: 'sit', 3: 'stand'}
INV_EVENT = {0: E.INVENTORY_UPDATE, 1: E.CART_UPDATE, 2: E.STORAGE_UPDATE}


@dataclass
class ParsedReplay:
    path: str
    packet_ver: str
    recorded_at: datetime.datetime
    duration_ms: int
    player_aid: int
    character: str
    job_id: int
    base_level: int
    job_level: int
    start_zeny: int
    last_map: str
    server: str
    events: list
    packets: list                   # [(time_ms, packet_bytes)] (개발자 도구용)
    actors: dict                    # aid → {obj_type, class_id, name, level}
    inventory_lists: list           # [(type, items, time_ms)] 기존 파서 결과
    unknown_counts: collections.Counter
    unsupported_counts: collections.Counter   # 정의는 있으나 길이/값이 맞지 않음
    decoded_counts: collections.Counter
    unmapped_vars: collections.Counter          # (packet, varID) 의미 미검증 스탯
    warnings: list = field(default_factory=list)


def _i32(b):
    return struct.unpack_from('<i', b)[0] if b and len(b) >= 4 else None


def normalize(rp, path, experimental=False, packet_ver=DEFAULT_PACKET_VER):
    """rp: rrf.Replay"""
    defs = registry(packet_ver, experimental)
    meta, sess = rp.meta_chunks(2), rp.meta_chunks(3)
    player = _i32(sess.get(1010)) or 0
    raw = list(rp.packets())
    t0 = raw[0][0] if raw else 0
    events, actors = [], {}
    unknown, unsupported, decoded = collections.Counter(), collections.Counter(), collections.Counter()
    unmapped_vars = collections.Counter()
    cur_map = None
    add = events.append

    decoder = PacketNormalizer(rp, experimental, packet_ver)
    inv = []
    decoder.t0 = t0
    for idx, (tm, p) in sorted(enumerate(raw), key=lambda row: (row[1][0], row[0])):
        events.extend(decoder.feed(tm, p, idx))
        inv.extend(decoder.inventory_lists)
    events.extend(decoder.flush())
    actors = decoder.observed_actors
    unknown, unsupported, decoded = decoder.unknown, decoder.unsupported, decoder.decoded
    unmapped_vars = decoder.unmapped_vars

    events.sort(key=lambda e: e.timestamp_ms)

    # 맵 이동이 없으면 전체가 ReplayData의 맵에서 진행된 것
    last_map = (meta.get(965) or b'').split(b'\0')[0].decode('cp949', 'replace')
    map_changes = [e for e in events if e.event_type == E.MAP_CHANGE]
    first_map = last_map if not map_changes else None
    cur = first_map
    for e in events:
        if e.event_type == E.MAP_CHANGE:
            cur = e.map_name
        elif e.map_name is None:
            e.map_name = cur

    duration = max((tm - t0 for tm, _ in raw), default=0)
    y, mo, dd, hh, mi, ss = rp.date
    try:
        server = rp.server()
    except Exception:
        server = None
    pr = ParsedReplay(
        path=path, packet_ver=packet_ver, recorded_at=datetime.datetime(y, mo, dd, hh, mi, ss),
        duration_ms=duration, player_aid=player,
        character=(meta.get(964) or b'').split(b'\0')[0].decode('cp949', 'replace'),
        job_id=_i32(sess.get(1014)), base_level=_i32(sess.get(1016)), job_level=_i32(sess.get(1017)),
        start_zeny=_i32(sess.get(1051)), last_map=last_map, server=server,
        events=events, packets=raw, actors=actors, inventory_lists=inv,
        unknown_counts=unknown, unsupported_counts=unsupported, decoded_counts=decoded, unmapped_vars=unmapped_vars)
    events.insert(0, ReplayEvent(0, E.SESSION_START, player, None, first_map))
    events.append(ReplayEvent(duration, E.SESSION_END, player, None, cur))
    if unknown:
        unknown_log.info('%s: 미해석 패킷 %s', path,
                         ', '.join('0x%04x×%d' % (k, v) for k, v in unknown.most_common()))
    return pr

# 획득·제니 수입의 출처는 앞뒤 패킷으로 가른다. 해석하지 않는 패킷도 '언제 왔는지'만 본다.
#   바닥 아이템 사라짐(0x00a1): 획득 300ms 전 ~ 150ms 뒤 (실측: 주운 것 다수가 획득 0~80ms 뒤에 옴)
#   창고에서 삭제(0x00f6): 획득 바로 앞 (실측 23/23) · 카트에서 삭제(0x0125): 같은 규칙으로 가정(표본 없음)
#   구입: 제니 감소가 획득 앞 300ms ~ 뒤 150ms (실측: 감소가 43~49ms 뒤에 온 경우 있음)
MARK_FLOOR, MARK_STORAGE, MARK_CART = 0x00a1, 0x00f6, 0x0125
GAIN_BEFORE_MS, GAIN_AFTER_MS = 300, 150
# 판매 표시(표본 없음 → experimental 일 때만 보고, 분류는 △): 상점 판매 결과 0x00cb(3), 노점 판매 0x09e5(18)/0x0137(6)
MARK_NPC_SELL, MARK_VEND = {0x00cb: 3}, {0x09e5: 18, 0x0137: 6}
DELETE_SOLD = 6                      # ZC_DELETE_ITEM_FROM_BODY reason 6 = 판매 (4 = 창고로 옮김은 실측 51/51 일치)
FLOW_WINDOW_MS = 500
DROP_OWNER_MS = 600                  # 몬스터 사망 뒤 이 시간 안에 떨어진 것은 그 몬스터의 드랍(실측 2,096/2,101)


class PacketNormalizer:
    """Stateful common packet decoder for offline and incremental input.

    Actor state is current-map scoped; aggregate diagnostics contain no raw history.
    Inventory fragments are retained only until the end marker (bounded at 4 MiB).
    """
    def __init__(self, rp, experimental=False, packet_ver=DEFAULT_PACKET_VER, retain_actors=True):
        self.rp = rp
        self.defs = registry(packet_ver, experimental)
        self.player = _i32(rp.meta_chunks(3).get(1010)) or 0
        self.actors = {}
        self.actor_evictions = 0
        self.observed_actors = {}
        self.retain_actors = retain_actors
        self.cur_map = None
        self.t0 = None
        self.unknown = collections.Counter()
        self.unsupported = collections.Counter()
        self.decoded = collections.Counter()
        self.unmapped_vars = collections.Counter()
        self.inventory_pending = []
        self.inventory_bytes = 0
        self.inventory_lists = []
        self.experimental = experimental
        self.zeny = _i32(rp.meta_chunks(3).get(1051))      # 녹화 시작 때의 제니 → 첫 변화부터 증감을 안다
        self.marks = {}                  # 표시 → 마지막으로 본 시각
        self.pending_gains = []          # [(시각, ITEM_GAIN)] 출처 판정을 기다리는 획득
        self.pending_flows = []          # [(시각, ZENY_FLOW)] 상점/노점 판정을 기다리는 제니 수입
        self.floor = {}                  # 바닥번호 → dict(item_id, amount, at, mob) 지금 바닥에 보이는 물건
        self.deaths = collections.deque(maxlen=32)       # (시각, 몬스터 종류) 방금 죽은 몬스터
        self.pending_ends = []           # [(시각, ITEM_DROP_END)] 내가 주웠는지 판정을 기다리는 '사라짐'
        self.loose_gains = collections.deque(maxlen=64)  # [시각, 아이템] 아직 어느 바닥 물건인지 못 맞춘 획득

    def _recent(self, key, tm, window):
        at = self.marks.get(key)
        return at is not None and 0 <= tm - at <= window

    def _mark(self, h, size, tm, out):
        """이 패킷이 대기 중인 획득/제니 수입의 출처를 정하는지 보고, 대기 시간이 지난 것은 내보낸다."""
        key = {MARK_FLOOR: 'floor', MARK_STORAGE: 'storage', MARK_CART: 'cart'}.get(h)
        if key is None and self.experimental:
            key = 'npc' if MARK_NPC_SELL.get(h) == size else 'vend' if MARK_VEND.get(h) == size else None
        while self.pending_gains and tm - self.pending_gains[0][0] > GAIN_AFTER_MS:     # 기다릴 만큼 기다린 것부터
            out.append(self.pending_gains.pop(0)[1])
        while self.pending_flows and tm - self.pending_flows[0][0] > FLOW_WINDOW_MS:
            out.append(self.pending_flows.pop(0)[1])
        while self.pending_ends and tm - self.pending_ends[0][0] > GAIN_AFTER_MS:       # 끝내 줍지 않았다
            out.append(self.pending_ends.pop(0)[1])
        if key:
            self._note(key, tm)

    def _note(self, key, tm):
        self.marks[key] = tm
        if key in ('floor', 'spend'):                # 획득 바로 뒤에 온 표시 → 출처를 고친다
            source = LOOT if key == 'floor' else PURCHASE
            for _, ev in self.pending_gains:
                if ev.data['source'] == OTHER_GAIN:
                    ev.data['source'] = source
        elif key in ('npc', 'vend'):
            for _, ev in self.pending_flows:
                if ev.data['kind'] == OTHER_INCOME:
                    ev.data['kind'] = NPC_SALE if key == 'npc' else VEND_SALE

    def _gain_source(self, tm):
        for key, source in (('storage', FROM_STORAGE), ('cart', FROM_CART), ('spend', PURCHASE), ('floor', LOOT)):
            if self._recent(key, tm, GAIN_BEFORE_MS):
                return source
        return OTHER_GAIN

    def _flow_kind(self, amount, tm):
        if amount < 0:
            return SPEND
        if self._recent('npc', tm, FLOW_WINDOW_MS):
            return NPC_SALE
        return VEND_SALE if self._recent('vend', tm, FLOW_WINDOW_MS) else OTHER_INCOME

    def flush(self):
        """입력이 끝났을 때 판정을 기다리던 이벤트를 그대로 내보낸다."""
        out = [ev for _, ev in self.pending_gains + self.pending_flows + self.pending_ends]
        self.pending_gains, self.pending_flows, self.pending_ends = [], [], []
        return out

    def _drop_owner(self, tm):
        """방금(0.6초 안) 죽은 몬스터가 한 종류면 그 종류, 없거나 여러 종류면 None(누구 것인지 단정하지 않는다)."""
        kinds = {cls for at, cls in self.deaths if 0 <= tm - at <= DROP_OWNER_MS}
        return kinds.pop() if len(kinds) == 1 else None

    def _gained(self, tm, item_id):
        """획득 한 건: 방금 사라진 같은 아이템이 있으면 '내가 주운 것'으로 맞춘다. 없으면 남겨 둔다."""
        for at, ev in self.pending_ends:
            if not ev.data['picked'] and ev.data['item_id'] == item_id and tm - at <= GAIN_AFTER_MS:
                ev.data['picked'] = True
                return
        self.loose_gains.append([tm, item_id])

    def _vanished(self, tm, t, gid, cur_map, idx, conf):
        """바닥 물건이 사라짐 → ITEM_DROP_END. 방금 같은 아이템을 얻었으면 내가 주운 것, 아니면 잠깐 기다려 본다."""
        rec = self.floor.pop(gid, None)
        if rec is None:
            return
        ev = ReplayEvent(t, E.ITEM_DROP_END, self.player, None, cur_map, data=dict(
            gid=gid, item_id=rec['item_id'], amount=rec['amount'], mob=rec['mob'], picked=False,
            age_ms=tm - rec['at']), raw_packet=idx, confidence=conf)
        for gain in self.loose_gains:
            if gain[1] == rec['item_id'] and 0 <= tm - gain[0] <= GAIN_BEFORE_MS:
                self.loose_gains.remove(gain)
                ev.data['picked'] = True
                break
        self.pending_ends.append((tm, ev))

    def feed(self, timestamp, packet, index):
        if self.t0 is None:
            self.t0 = timestamp
        defs, player, actors = self.defs, self.player, self.actors
        t0, cur_map = self.t0, self.cur_map
        unknown, unsupported, decoded = self.unknown, self.unsupported, self.decoded
        unmapped_vars = self.unmapped_vars
        events = []
        add = events.append
        for idx, (tm, p) in [(index, (timestamp, packet))]:
            if len(p) < 2:
                continue
            h = struct.unpack_from('<H', p)[0]
            t = tm - t0
            self._mark(h, len(p), tm, events)
            d = defs.get(h)
            if d is None:
                unknown[h] += 1
                continue
            if d.decode is None:                     # 인벤토리 목록: 아래에서 기존 파서로 처리
                decoded[h] += 1
                continue
            if (d.length is not None and len(p) != d.length) or \
                    (d.length is None and (len(p) < 4 or struct.unpack_from('<H', p, 2)[0] != len(p))):
                unsupported[h] += 1
                log.debug('길이 불일치 0x%04x len=%d', h, len(p))
                continue
            try:
                f = d.decode(p)
            except Exception:
                unsupported[h] += 1
                log.debug('디코드 실패 0x%04x', h, exc_info=True)
                continue
            decoded[h] += 1
            conf = MEASURED if d.verified else ESTIMATE

            if h in (0x0091, 0x0ac7):
                cur_map = f['map']
                actors.clear()
                self.floor.clear()                   # 맵을 떠나면 바닥 물건의 끝은 알 수 없다
                add(ReplayEvent(t, E.MAP_CHANGE, player, None, cur_map, f['x'], f['y'], {}, idx, conf))
            elif h == 0x0087:
                add(ReplayEvent(t, E.PLAYER_MOVE, player, None, cur_map, f['dx'], f['dy'],
                                dict(sx=f['sx'], sy=f['sy']), idx, conf))
            elif h in (0x09ff, 0x09fd, 0x09fe):
                aid, ot = f['aid'], f['obj_type']
                known = actors.get(aid)
                actors[aid] = dict(obj_type=ot, class_id=f['class_id'], name=f['name'] or (known or {}).get('name', ''),
                                   level=f.get('level'))
                if self.retain_actors:
                    self.observed_actors[aid] = actors[aid]
                if aid == player:
                    continue
                if h == 0x09fd and ot == OBJ_MOB and known:
                    et = E.MONSTER_MOVE
                else:
                    et = {OBJ_PC: E.PLAYER_SPAWN, OBJ_MOB: E.MONSTER_SPAWN, OBJ_NPC: E.NPC_SPAWN,
                          OBJ_EVT_NPC: E.NPC_SPAWN}.get(ot, E.UNIT_SPAWN)
                add(ReplayEvent(t, et, aid, None, cur_map, f.get('x'), f.get('y'),
                                dict(obj_type=ot, class_id=f['class_id'], name=f['name'], max_hp=f.get('max_hp'),
                                     hp=f.get('hp')), idx, conf))
            elif h == 0x0080:
                a = actors.get(f['aid'], {})
                if f['vanish_type'] == VANISH_DEAD and a.get('obj_type') == OBJ_MOB:
                    self.deaths.append((tm, a.get('class_id')))
                    add(ReplayEvent(t, E.MONSTER_DEATH, f['aid'], None, cur_map, data=dict(class_id=a.get('class_id')),
                                    raw_packet=idx, confidence=conf))
                else:
                    add(ReplayEvent(t, E.UNIT_VANISH, f['aid'], None, cur_map,
                                    data=dict(vanish_type=f['vanish_type'], obj_type=a.get('obj_type')),
                                    raw_packet=idx, confidence=conf))
                actors.pop(f['aid'], None)
            elif h == 0x0088:
                if f['aid'] == player:
                    add(ReplayEvent(t, E.PLAYER_MOVE, player, None, cur_map, f['x'], f['y'], dict(stop=True), idx, conf))
            elif h == 0x08c8:
                if f['action'] in ACT_DAMAGE:
                    dmg = max(f['damage'], 0) + max(f['left_damage'], 0)
                    add(ReplayEvent(t, E.DAMAGE, f['src'], f['tgt'], cur_map,
                                    data=dict(amount=dmg, skill=None, hit=ACT_DAMAGE[f['action']], div=f['div'],
                                              miss=(dmg == 0), tick=f['tick']), raw_packet=idx, confidence=conf))
                else:
                    add(ReplayEvent(t, E.ACTION, f['src'], f['tgt'], cur_map,
                                    data=dict(action=ACT_OTHER.get(f['action'], f['action'])), raw_packet=idx,
                                    confidence=conf))
            elif h == 0x02e1:                        # 몬스터가 때린 평타(내 평타는 0x08c8)
                if f['action'] in ACT_DAMAGE:
                    dmg = max(f['damage'], 0) + max(f['left_damage'], 0)
                    add(ReplayEvent(t, E.DAMAGE, f['src'], f['tgt'], cur_map,
                                    data=dict(amount=dmg, skill=None, hit=ACT_DAMAGE[f['action']], div=f['div'],
                                              miss=(dmg == 0), tick=f['tick']), raw_packet=idx, confidence=conf))
            elif h == 0x013e:                        # 몬스터의 시전 시작
                add(ReplayEvent(t, E.SKILL_CAST_START, f['src'], f['tgt'] or None, cur_map, f['x'] or None, f['y'] or None,
                                dict(skill=f['skill'], cast_ms=f['cast_ms']), idx, conf))
            elif h == 0x043d:
                add(ReplayEvent(t, E.SKILL_DELAY, player, None, cur_map,
                                data=dict(skill=f['skill'], delay_ms=f['delay_ms']), raw_packet=idx, confidence=conf))
            elif h == 0x0985:                        # 접속·맵 이동 때 남아 있던 쿨타임
                for s in f['skills']:
                    add(ReplayEvent(t, E.SKILL_DELAY, player, None, cur_map,
                                    data=dict(skill=s['skill'], delay_ms=s['remain_ms'], total_ms=s['total_ms'],
                                              carried=True), raw_packet=idx, confidence=conf))
            elif h == 0x0110:
                add(ReplayEvent(t, E.SKILL_FAIL, player, None, cur_map,
                                data=dict(skill=f['skill'], cause=f['cause'], item_id=f['item_id'] or None),
                                raw_packet=idx, confidence=conf))
            elif h == 0x0a36:
                add(ReplayEvent(t, E.MONSTER_HP, f['aid'], None, cur_map, data=dict(hp_pct=min(100, f['hp'] * 5)),
                                raw_packet=idx, confidence=conf))
            elif h == 0x0add:
                mob = self._drop_owner(tm)
                self.floor[f['gid']] = dict(item_id=f['item_id'], amount=f['amount'], at=tm, mob=mob)
                add(ReplayEvent(t, E.ITEM_DROP, player, None, cur_map, f['x'], f['y'],
                                dict(gid=f['gid'], item_id=f['item_id'], amount=f['amount'], mob=mob), idx, conf))
            elif h == 0x009d:                        # 이미 떨어져 있던 물건: 누가 떨어뜨렸는지 모른다
                self.floor.setdefault(f['gid'], dict(item_id=f['item_id'], amount=f['amount'], at=tm, mob=None))
            elif h == 0x00a1:
                self._vanished(tm, t, f['gid'], cur_map, idx, conf)
            elif h == 0x01c8:
                if f['aid'] == player:
                    add(ReplayEvent(t, E.ITEM_USE, player, None, cur_map,
                                    data=dict(item_id=f['item_id'], remaining=f['remaining'], ok=f['ok']),
                                    raw_packet=idx, confidence=conf))
            elif h in (0x010f, 0x0b32):
                add(ReplayEvent(t, E.SKILL_LIST, player, None, cur_map, data=dict(skills=f['skills']), raw_packet=idx,
                                confidence=conf))
            elif h == 0x0b0c:
                add(ReplayEvent(t, E.QUEST_ADD, player, None, cur_map,
                                data=dict(quest=f['quest'], active=f['active'], end=f['end'], goals=f['goals']),
                                raw_packet=idx, confidence=conf))
            elif h in (0x0afe, 0x08fe):
                for g in f['goals']:
                    add(ReplayEvent(t, E.QUEST_PROGRESS, player, None, cur_map, data=dict(g), raw_packet=idx,
                                    confidence=conf))
            elif h == 0x02b4:
                add(ReplayEvent(t, E.QUEST_REMOVE, player, None, cur_map, data=dict(quest=f['quest']), raw_packet=idx,
                                confidence=conf))
            elif h in (0x02cb, 0x02cc, 0x02cd):
                add(ReplayEvent(t, E.INSTANCE_READY if h == 0x02cd else E.INSTANCE_QUEUE, player, None, cur_map,
                                data=dict(f), raw_packet=idx, confidence=conf))
            elif h in (0x0131, 0x0814):
                add(ReplayEvent(t, E.STORE_SIGN, f['aid'], None, cur_map,
                                data=dict(title=f['title'], kind='sale' if h == 0x0131 else 'buy'), raw_packet=idx,
                                confidence=conf))
            elif h == 0x0b64:
                add(ReplayEvent(t, E.STORE_SEARCH, player, None, cur_map,
                                data=dict(rows=f['rows'], first=bool(f['first']), more=bool(f['more'])),
                                raw_packet=idx, confidence=conf))
            elif h == 0x0b3d:
                add(ReplayEvent(t, E.STORE_ITEMS, f['aid'], None, cur_map, data=dict(rows=f['rows']), raw_packet=idx,
                                confidence=conf))
            elif h == 0x01de:
                add(ReplayEvent(t, E.DAMAGE, f['src'], f['tgt'], cur_map,
                                data=dict(amount=max(f['damage'], 0), skill=f['skill'], hit='skill', div=f['div'],
                                          miss=f['damage'] <= 0, tick=f['tick']), raw_packet=idx, confidence=conf))
            elif h == 0x07fb:
                add(ReplayEvent(t, E.SKILL_CAST_START, f['src'], f['tgt'] or None, cur_map, f['x'] or None, f['y'] or None,
                                dict(skill=f['skill'], cast_ms=f['cast_ms']), idx, conf))
            elif h == 0x09cb:
                add(ReplayEvent(t, E.SKILL_USE, f['src'], f['tgt'], cur_map,
                                data=dict(skill=f['skill'], level=f['level'], result=f['result']), raw_packet=idx,
                                confidence=conf))
            elif h in (0x0983, 0x043f, 0x0196, 0x0984):
                et = E.BUFF_START if f['state'] else E.BUFF_END
                add(ReplayEvent(t, et, f['aid'], None, cur_map,
                                data=dict(efst=f['efst'], max_ms=f['max_ms'], remain_ms=f['remain_ms'], vals=f['vals'],
                                          in_view=(h == 0x0984)), raw_packet=idx, confidence=conf))
            elif h in (0x00b0, 0x00b1):
                var, val = f['var'], f['value']
                et = {(0x00b0, VAR_HP): E.HP_CHANGE, (0x00b0, VAR_MAXHP): E.HP_CHANGE,
                      (0x00b0, VAR_SP): E.SP_CHANGE, (0x00b0, VAR_MAXSP): E.SP_CHANGE,
                      (0x00b0, VAR_WEIGHT): E.WEIGHT_CHANGE, (0x00b1, VAR_ZENY): E.ZENY_CHANGE}.get((h, var))
                if (h, var) in ((0x00b0, VAR_BASE_LEVEL), (0x00b0, VAR_JOB_LEVEL)):
                    add(ReplayEvent(t, E.LEVEL_CHANGE, player, None, cur_map,
                                    data=dict(kind='base' if var == VAR_BASE_LEVEL else 'job', level=val),
                                    raw_packet=idx, confidence=conf))
                elif et:
                    key = 'max' if var in (VAR_MAXHP, VAR_MAXSP) else 'value'
                    add(ReplayEvent(t, et, player, None, cur_map, data={key: val, 'var': var}, raw_packet=idx,
                                    confidence=conf))
                else:
                    unmapped_vars[(h, var)] += 1   # 의미가 검증되지 않은 varID: 버리지 않고 집계만
                if (h, var) == (0x00b1, VAR_ZENY):
                    if self.zeny is not None and val != self.zeny:
                        amount = val - self.zeny
                        flow = ReplayEvent(t, E.ZENY_FLOW, player, None, cur_map,
                                           data=dict(amount=amount, kind=self._flow_kind(amount, tm)),
                                           raw_packet=idx, confidence=conf)
                        if amount < 0:
                            self._note('spend', tm)
                            add(flow)
                        else:
                            self.pending_flows.append((tm, flow))
                    self.zeny = val
            elif h in (0x0acc, 0x07f6):
                if f['aid'] in (player, 0):
                    et = {1: E.BASE_EXP_GAIN, 2: E.JOB_EXP_GAIN}.get(f['var'])
                    if et:
                        add(ReplayEvent(t, et, player, None, cur_map, data=dict(amount=f['amount']), raw_packet=idx,
                                        confidence=conf))
            elif h == 0x07fa:
                add(ReplayEvent(t, E.ITEM_REMOVE, player, None, cur_map,
                                data=dict(index=f['index'], count=f['count'], reason=f['reason']), raw_packet=idx,
                                confidence=conf))
                if f['reason'] == DELETE_SOLD:
                    self._note('npc', tm)
            elif h == 0x0b41:
                if f['item_id'] and f['count'] and f['result'] == 0:      # 번호 0 = 줍기 실패 알림
                    self._gained(tm, f['item_id'])
                    self.pending_gains.append((tm, ReplayEvent(
                        t, E.ITEM_GAIN, player, None, cur_map,
                        data=dict(item_id=f['item_id'], amount=f['count'], index=f['index'], refine=f['refine'],
                                  grade=f['grade'], cards=f['cards'],
                                  source=self._gain_source(tm)), raw_packet=idx, confidence=conf)))
        if not self.retain_actors and len(actors) > 8192:
            actors.pop(next(iter(actors)))
            self.actor_evictions += 1
        self.cur_map = cur_map
        self.inventory_lists = []
        if len(packet) >= 2:
            pid = struct.unpack_from('<H', packet)[0]
            if pid == 0x0b08:
                self.inventory_pending = []
                self.inventory_bytes = 0
            if pid in (0x0b08, 0x0b09, 0x0b39, 0x0b0b):
                self.inventory_bytes += len(packet)
                if self.inventory_bytes <= 4 * 1024 * 1024:
                    self.inventory_pending.append((timestamp, packet))
                else:
                    self.inventory_pending.clear()
                    self.unsupported[pid] += 1
                if pid == 0x0b0b:
                    self.inventory_lists = self.rp.inventory_lists(packets=self.inventory_pending, with_time=True)
                    for typ, items, tm in self.inventory_lists:
                        events.append(ReplayEvent(tm - t0, INV_EVENT.get(typ, E.UNKNOWN), player,
                            map_name=cur_map, data=dict(inv_type=typ, items=items, count=len(items))))
                    self.inventory_pending.clear()
                    self.inventory_bytes = 0
        return events

