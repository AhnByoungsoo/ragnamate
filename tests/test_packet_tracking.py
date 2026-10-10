"""해외 공개 정의로 배운 패킷들: 받은 피해, 스킬 재사용 대기·실패, 바닥 드랍, 퀘스트 사냥 수, 노점 검색, 스킬 목록.

합성 패킷으로 배치를 확인하고, 로컬 리플레이가 있으면 2026-10-10 에 잰 값과 맞는지 본다.
"""
import collections
import datetime
import os
import struct
import tempfile
import unittest

from helpers import PLAYER, FakeGameData, ev, parsed, sample

from analyzer import packets as P
from analyzer.analyzers import tracking
from analyzer.analyzers.context import Context
from analyzer.events import EventType as E
from analyzer.normalizer import OBJ_MOB, PacketNormalizer
from core import loot_rows as LR
from live.state import GameStateReducer
from services import gnjoy_market as G
from services import ingame_market

START_ZENY, MOB, MOB_CLASS, OTHER_CLASS = 1_000_000, 5001, 1002, 1113
CARD, JUNK = 4001, 7219


class FakeReplay:
    def meta_chunks(self, kind):
        return {1010: struct.pack('<i', PLAYER), 1051: struct.pack('<i', START_ZENY)} if kind == 3 else {}


def pk(header, fmt, *values, size=None):
    b = struct.pack('<H' + fmt, header, *values)
    return b + bytes((size or len(b)) - len(b))


def pickup(item_id, count=1, refine=0, cards=(), grade=0):
    b = bytearray(70)
    struct.pack_into('<HHHI', b, 0, 0x0b41, 10, count, item_id)
    struct.pack_into('<4I', b, 12, *(list(cards) + [0, 0, 0, 0])[:4])
    b[68], b[69] = refine, grade
    return bytes(b)


def drop(gid, item_id, amount=1, x=100, y=120):
    return pk(0x0add, 'IIHBHHBBH', gid, item_id, 0, 1, x, y, 0, 0, amount, size=24)


def vanish(gid):
    return pk(0x00a1, 'I', gid)


def mob_dies(aid, class_id=MOB_CLASS):
    """몬스터가 보였다가(0x09ff) 죽는다(0x0080 type 1)."""
    body = bytearray(84 + 8)
    body[4] = OBJ_MOB
    struct.pack_into('<I', body, 5, aid)
    struct.pack_into('<H', body, 23, class_id)
    struct.pack_into('<H', body, 2, len(body))
    struct.pack_into('<H', body, 0, 0x09ff)
    return bytes(body), pk(0x0080, 'IB', aid, 1)


TICK = pk(0x7777, 'H', 0)


def run(*timeline, experimental=False):
    n = PacketNormalizer(FakeReplay(), experimental)
    out = []
    for i, (tm, packet) in enumerate(timeline):
        out += n.feed(tm, packet, i)
    return out + n.flush()


def of(events, kind):
    return [e for e in events if e.event_type == kind]


class DefinitionTest(unittest.TestCase):
    def test_every_new_packet_is_registered_with_its_measured_length(self):
        defs = P.registry()
        want = {0x02e1: 33, 0x013e: 24, 0x043d: 8, 0x0110: 14, 0x0a36: 7, 0x0add: 24, 0x009d: 19, 0x00a1: 6,
                0x01c8: 15, 0x0b0c: 155, 0x02b4: 6, 0x0131: 86, 0x0814: 86, 0x0b41: 70}
        for header, size in want.items():
            self.assertEqual(defs[header].length, size, hex(header))
            self.assertTrue(defs[header].verified, hex(header))
        for header in (0x0985, 0x010f, 0x0b32, 0x0afe, 0x08fe, 0x0b64, 0x0b3d):       # 길이 칸이 있는 가변 패킷
            self.assertIsNone(defs[header].length, hex(header))
        self.assertEqual(P.PACKET_SETS[P.DEFAULT_PACKET_VER][0x07fa].length, 8)       # 예전 정의 10은 틀렸다

    def test_pickup_reads_refine_grade_and_cards_from_the_tail(self):
        d = P.d_pickup(pickup(CARD, 2, refine=12, cards=(4003, 4004), grade=3))
        self.assertEqual((d['item_id'], d['count'], d['refine'], d['grade'], d['cards'], d['result']),
                         (CARD, 2, 12, 3, [4003, 4004], 0))


class SpawnNameTest(unittest.TestCase):
    def test_new_entry_name_is_not_cut(self):
        name = '블루 문 로리루리'.encode('cp949')
        b = bytearray(83 + len(name) + 1)
        struct.pack_into('<HH', b, 0, 0x09fe, len(b))
        b[4] = OBJ_MOB
        struct.pack_into('<I', b, 5, MOB)
        struct.pack_into('<H', b, 23, 20940)
        struct.pack_into('<H', b, 68, 255)                                   # 레벨
        b[83:83 + len(name)] = name
        d = P.d_newentry(bytes(b))
        self.assertEqual((d['name'], d['class_id'], d['level']), ('블루 문 로리루리', 20940, 255))

    def test_real_replay_names_agree_between_packet_kinds(self):
        path = sample('3qww.rrf')
        if not path:
            self.skipTest('local replay unavailable')
        from rrf import Replay
        seen = collections.defaultdict(set)
        for _t, b in Replay(path).packets():
            h = struct.unpack_from('<H', b)[0]
            if h in (0x09ff, 0x09fd, 0x09fe) and b[4] == OBJ_MOB:
                d = P.registry()[h].decode(b)
                seen[d['class_id']].add(d['name'])
        self.assertEqual(seen[20940], {'블루 문 로리루리'})                     # 어느 패킷으로 봐도 같은 이름
        self.assertTrue(all(len(v) == 1 for v in seen.values()))


class DamageTakenTest(unittest.TestCase):
    def hit(self, damage, action=0, src=MOB, tgt=PLAYER):
        return pk(0x02e1, 'IIIiiiHBi', src, tgt, 1, 100, 100, damage, 1, action, 0)

    def test_monster_hits_become_damage_events(self):
        events = of(run((0, self.hit(5000)), (10, self.hit(0, action=11)), (20, self.hit(7000, action=10))), E.DAMAGE)
        self.assertEqual([(e.actor_id, e.target_id, e.data['amount'], e.data['hit'], e.data['miss']) for e in events],
                         [(MOB, PLAYER, 5000, 'normal', False), (MOB, PLAYER, 0, 'lucky_dodge', True),
                          (MOB, PLAYER, 7000, 'critical', False)])

    def test_non_damage_actions_are_ignored(self):
        self.assertEqual(of(run((0, self.hit(0, action=2))), E.DAMAGE), [])        # 앉기 같은 동작

    def test_monster_cast_is_a_cast_start(self):
        cast = pk(0x013e, 'IIHHHII', MOB, PLAYER, 0, 0, 86, 0, 1000)
        e = of(run((0, cast)), E.SKILL_CAST_START)[0]
        self.assertEqual((e.actor_id, e.target_id, e.data['skill'], e.data['cast_ms']), (MOB, PLAYER, 86, 1000))


class SkillTest(unittest.TestCase):
    def test_cooldown_and_failures_reach_the_skill_table(self):
        delay = pk(0x043d, 'HI', 5001, 700)
        fail = lambda cause: pk(0x0110, 'HiIBB', 5001, 0, 0, 0, cause)              # noqa: E731
        events = run((0, delay), (700, delay), (900, fail(4)), (950, fail(4)), (1000, fail(11)))
        events.append(ev(100, E.SKILL_USE, PLAYER, MOB, skill=5001))
        ctx = Context.build(parsed(events, 2000), FakeGameData(), {})
        got = tracking.skills(ctx)[5001]
        self.assertEqual((got['cooldown_ms'], got['failures'], got['blocked']), (700, 3, 2))
        self.assertEqual(got['causes'], [('스킬 딜레이 중', 2), ('대상이 맞지 않음', 1)])
        from analyzer.analyzers import skill
        row = skill.analyze(ctx)['skills'][0]
        self.assertEqual((row['cooldown'], row['failures'], row['blocked']), (700, 3, 2))

    def test_carried_cooldown_list_is_not_the_skill_setting(self):
        listed = pk(0x0985, 'HHII', 14, 5246, 180000, 110393)                   # 접속했을 때 남아 있던 쿨타임
        events = run((0, listed))
        e = of(events, E.SKILL_DELAY)[0]
        self.assertEqual((e.data['skill'], e.data['delay_ms'], e.data['total_ms'], e.data['carried']),
                         (5246, 110393, 180000, True))
        self.assertEqual(tracking.skills(Context.build(parsed(events, 1000), FakeGameData(), {})).get(5246, {})
                         .get('cooldown_ms'), None)                             # 남은 시간을 재사용 대기로 착각하지 않는다

    def test_unknown_cause_is_shown_as_a_number_not_invented(self):
        self.assertEqual(tracking.cause_text(116), '사유 116')

    def test_skill_list_gives_overcharge_level(self):
        body = b''.join(struct.pack('<HiHHHBH', sid, 0, lv, 0, 1, 0, lv) for sid, lv in ((1, 9), (LR.OVERCHARGE_SKILL, 10)))
        events = run((0, struct.pack('<HH', 0x0b32, 4 + len(body)) + body))
        self.assertEqual(of(events, E.SKILL_LIST)[0].data['skills'][LR.OVERCHARGE_SKILL]['level'], 10)
        self.assertEqual(LR.overcharge_level(events), 10)
        self.assertEqual(LR.overcharge_level([]), None)                         # 스킬 목록이 없으면 모른다
        self.assertEqual([LR.overcharge_rate(lv) for lv in (0, 1, 9, 10)], [0, 7, 23, 24])


class DropTest(unittest.TestCase):
    def setUp(self):
        self.spawn, self.death = mob_dies(MOB)

    def ends(self, *timeline):
        return [(e.data['item_id'], e.data['picked'], e.data['mob']) for e in of(run(*timeline), E.ITEM_DROP_END)]

    def test_drop_right_after_a_kill_belongs_to_that_monster(self):
        events = run((0, self.spawn), (1000, self.death), (1100, drop(1, JUNK, 2)))
        e = of(events, E.ITEM_DROP)[0]
        self.assertEqual((e.data['item_id'], e.data['amount'], e.data['mob'], e.x, e.y), (JUNK, 2, MOB_CLASS, 100, 120))

    def test_owner_is_unknown_when_two_kinds_died_or_nobody_did(self):
        spawn2, death2 = mob_dies(MOB + 1, OTHER_CLASS)
        events = run((0, self.spawn), (1, spawn2), (1000, self.death), (1050, death2), (1100, drop(1, JUNK)))
        self.assertIsNone(of(events, E.ITEM_DROP)[0].data['mob'])                # 누구 것인지 단정하지 않는다
        self.assertIsNone(of(run((5000, drop(2, JUNK))), E.ITEM_DROP)[0].data['mob'])

    def test_picked_when_i_gain_the_same_item_around_the_vanish(self):
        self.assertEqual(self.ends((0, drop(1, JUNK)), (500, pickup(JUNK)), (520, vanish(1)), (2000, TICK)),
                         [(JUNK, True, None)])                                   # 획득이 먼저
        self.assertEqual(self.ends((0, drop(1, JUNK)), (500, vanish(1)), (540, pickup(JUNK)), (2000, TICK)),
                         [(JUNK, True, None)])                                   # 사라짐이 먼저

    def test_missed_when_it_vanishes_without_my_pickup(self):
        self.assertEqual(self.ends((0, drop(1, CARD)), (60_000, vanish(1)), (61_000, TICK)), [(CARD, False, None)])
        self.assertEqual(self.ends((0, drop(1, CARD)), (500, pickup(JUNK)), (520, vanish(1)), (2000, TICK)),
                         [(CARD, False, None)])                                  # 다른 아이템을 주운 것

    def test_one_pickup_does_not_claim_two_drops(self):
        got = self.ends((0, drop(1, JUNK)), (0, drop(2, JUNK)), (500, pickup(JUNK)), (510, vanish(1)), (515, vanish(2)),
                        (2000, TICK))
        self.assertEqual(sorted(p for _, p, _ in got), [False, True])

    def test_report_counts_missed_and_per_monster_rates(self):
        spawn2, death2 = mob_dies(MOB + 1)
        events = run((0, self.spawn), (1, spawn2), (1000, self.death), (1100, drop(1, JUNK)), (1200, pickup(JUNK)),
                     (1210, vanish(1)), (5000, death2), (5100, drop(2, CARD)), (65_000, vanish(2)), (66_000, TICK))
        d = tracking.drops(Context.build(parsed(events, 70_000), FakeGameData(), {}))
        self.assertEqual((d['dropped'], d['picked'], d['missed_count'], d['unknown_owner']), (2, 1, 1, 0))
        self.assertEqual([(r['item_id'], r['quantity']) for r in d['missed']], [(CARD, 1)])
        mob = d['by_mob'][0]
        self.assertEqual((mob['class_id'], mob['kills']), (MOB_CLASS, 2))
        self.assertEqual({i['item_id']: i['rate'] for i in mob['items']}, {JUNK: 50.0, CARD: 50.0})   # 2마리 중 1번씩

    def test_item_use_is_counted_for_me_only(self):
        used = lambda aid, remaining: pk(0x01c8, 'HIIHB', 3, 501, aid, remaining, 1)          # noqa: E731
        events = run((0, used(PLAYER, 9)), (10, used(PLAYER, 8)), (20, used(MOB, 1)))
        rows = tracking.item_use(Context.build(parsed(events, 100), FakeGameData(), {}))
        self.assertEqual([(r['item_id'], r['uses'], r['remaining']) for r in rows], [(501, 2, 8)])


class QuestTest(unittest.TestCase):
    def add(self, quest, *mobs):
        b = bytearray(155)
        struct.pack_into('<HIBiih', b, 0, 0x0b0c, quest, 1, 0, 0, len(mobs))
        for k, (mob_id, name) in enumerate(mobs):
            o = 17 + k * 46
            struct.pack_into('<IIIIhhh', b, o, quest, 0, 0, mob_id, 0, 0, 0)
            b[o + 22:o + 22 + len(name.encode('cp949'))] = name.encode('cp949')
        return bytes(b)

    def listed(self, quest, mob_id, need):
        return struct.pack('<HHIIhh', 0x08fe, 16, quest, mob_id, need, 0)

    def hunted(self, quest, index, need, count):
        return struct.pack('<HHHIIIhh', 0x0afe, 22, 1, quest, quest, index, need, count)

    def test_progress_follows_goal_position(self):
        events = run((0, self.add(7897, (20614, '블랙 어시더스'), (20615, '실버 어시더스'))),
                     (1, self.listed(7897, 20614, 20)), (2, self.listed(7897, 20615, 20)),
                     (1000, self.hunted(7897, 2, 20, 1)), (2000, self.hunted(7897, 1, 20, 1)),
                     (3000, self.hunted(7897, 2, 20, 2)))
        q = tracking.quests(Context.build(parsed(events, 5000), FakeGameData(), {}), {7897: '어시더스 속성연구'}.get)['quests'][0]
        self.assertEqual((q['title'], q['count'], q['need'], q['complete'], q['updates']), ('어시더스 속성연구', 3, 40, False, 3))
        self.assertEqual([(g['name'], g['count'], g['need']) for g in q['goals']],
                         [('블랙 어시더스', 1, 20), ('실버 어시더스', 2, 20)])

    def test_complete_and_removed(self):
        events = run((0, self.hunted(11949, 1, 1, 1)), (10, struct.pack('<HI', 0x02b4, 11949)))
        q = tracking.quests(Context.build(parsed(events, 100), FakeGameData(), {}))['quests'][0]
        self.assertEqual((q['complete'], q['removed'], q['title']), (True, True, None))     # 제목을 모르면 None

    def test_need_is_unknown_until_the_game_says_it(self):
        events = run((0, self.add(7893, (20612, '퍼플 페러스'))))
        q = tracking.quests(Context.build(parsed(events, 100), FakeGameData(), {}))['quests'][0]
        self.assertEqual((q['need'], q['complete']), (None, False))


class StoreTest(unittest.TestCase):
    def search(self, first, *rows):
        body = b''
        for shop, item_id, price, amount, refine in rows:
            b = bytearray(142)
            struct.pack_into('<II', b, 0, 1, 2)
            b[8:8 + len(shop.encode('cp949'))] = shop.encode('cp949')
            struct.pack_into('<IBIH', b, 88, item_id, 0, price, amount)
            b[140] = refine
            body += bytes(b)
        return struct.pack('<HHBBB', 0x0b64, 7 + len(body), first, 1, 2) + body

    def test_search_rows_are_decoded(self):
        e = of(run((0, self.search(1, ('와기상단', 12114, 7500, 1470, 0), ('불물풍', 12114, 8250, 542, 0)))), E.STORE_SEARCH)[0]
        self.assertEqual([(r['shop'], r['item_id'], r['price'], r['amount']) for r in e.data['rows']],
                         [('와기상단', 12114, 7500, 1470), ('불물풍', 12114, 8250, 542)])
        self.assertTrue(e.data['first'])

    def test_only_searches_that_start_from_the_first_page_become_prices(self):
        first = (0, self.search(1, ('가', CARD, 100, 1, 0), ('나', CARD, 120, 1, 0)))
        more = (5000, self.search(0, ('다', CARD, 150, 1, 0), ('라', JUNK, 9, 1, 0)))
        got = ingame_market.searches(run(first, more))
        self.assertEqual([(s['item_id'], [r['price'] for r in s['rows']]) for s in got], [(CARD, [100, 120, 150])])
        self.assertEqual(ingame_market.searches(run(more)), [])                 # 뒤쪽만 있으면 최저가를 모른다

    def test_unsorted_results_are_not_trusted(self):
        self.assertEqual(ingame_market.searches(run((0, self.search(1, ('가', CARD, 500, 1, 0), ('나', CARD, 100, 1, 0))))), [])

    def test_ingest_saves_once_and_never_goes_back_in_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = G.VendingStore(os.path.join(tmp, 'v.db'))
            try:
                events = run((0, self.search(1, ('가', CARD, 12_000_000, 1, 7))))
                when = datetime.datetime(2026, 10, 10, 9, 0)
                self.assertEqual(ingame_market.ingest(store, '129', events, when, {CARD: '포링 카드'}.get), [CARD])
                at, rows = store.latest_listings('129', CARD)
                self.assertEqual((at, rows[0]['price'], rows[0]['listing_name']), (when.timestamp(), 12_000_000, '+7포링 카드'))
                self.assertEqual(ingame_market.ingest(store, '129', events, when, str), [])          # 같은 것 다시
                older = datetime.datetime(2026, 10, 1, 9, 0)
                self.assertEqual(ingame_market.ingest(store, '129', events, older, str), [])         # 더 옛날 리플레이
            finally:
                store.close()

    def test_signs_and_opened_shop(self):
        sign = lambda header, title: pk(header, 'I', 77) + title.encode('cp949') + bytes(80 - len(title.encode('cp949')))   # noqa: E731
        item = bytearray(64)
        struct.pack_into('<IHhBI', item, 0, 8500, 1830, 2, 0, 12114)
        opened = struct.pack('<HHII', 0x0b3d, 12 + 64, 77, 1) + bytes(item)
        events = run((0, sign(0x0131, '팝니다')), (1, sign(0x0814, '러프윈드 사요')), (2, opened))
        st = tracking.stores(Context.build(parsed(events, 100), FakeGameData(), {}))
        self.assertEqual((st['sale_signs'], st['buy_signs'], st['buying']), (1, 1, ['러프윈드 사요']))
        self.assertEqual([(r['item_id'], r['price'], r['amount']) for r in st['opened']], [(12114, 8500, 1830)])


class ReducerTest(unittest.TestCase):
    def test_live_snapshot_carries_cooldowns_quests_drops_and_target_hp(self):
        s = GameStateReducer(PLAYER, START_ZENY)
        for e in (ev(0, E.SKILL_DELAY, PLAYER, skill=5001, delay_ms=30_000),
                  ev(0, E.SKILL_DELAY, PLAYER, skill=5002, delay_ms=700),
                  ev(100, E.DAMAGE, PLAYER, MOB, amount=10, skill=None, miss=False),
                  ev(150, E.MONSTER_HP, MOB, hp_pct=35),
                  ev(200, E.ITEM_DROP, PLAYER, gid=1, item_id=CARD, amount=1, mob=MOB_CLASS),
                  ev(300, E.ITEM_DROP_END, PLAYER, gid=1, item_id=CARD, amount=1, picked=False, mob=MOB_CLASS),
                  ev(400, E.QUEST_PROGRESS, PLAYER, quest=11951, ident=11951, ident2=1, need=10, count=4)):
            s.apply(e)
        v = s.view()
        self.assertEqual([(c['skill'], c['remain_ms']) for c in v['cooldowns']], [(5001, 29_600)])   # 0.7초짜리는 뺀다
        self.assertEqual(v['target_hp'], 35)
        self.assertEqual([(d['seq'], d['item_id']) for d in v['drops']], [(1, CARD)])
        self.assertEqual(v['drop_counts'], dict(dropped=1, picked=0, missed=1))
        self.assertEqual([(q['quest'], q['goals'][0]['count'], q['goals'][0]['need']) for q in v['quests']], [(11951, 4, 10)])
        s.advance(31_000)
        self.assertEqual(s.view()['cooldowns'], [])


class RealReplayTest(unittest.TestCase):
    """2026-10-10 실측값(리플레이가 없으면 건너뜀)."""

    def load(self, name):
        path = sample(name)
        if not path:
            self.skipTest('local replay unavailable')
        from analyzer.normalizer import normalize
        from rrf import Replay
        return normalize(Replay(path), path, experimental=True)

    def test_damage_taken_matches_hp_loss(self):
        pr = self.load('321321.rrf')
        hits = [e for e in pr.events if e.event_type == E.DAMAGE and e.target_id == pr.player_aid and e.data['skill'] is None]
        landed = [e for e in hits if not e.data['miss']]
        # 피해가 들어간 679회의 합 1,302,048 — 같은 리플레이의 HP 감소량 합(1,338,270)의 97%.
        # 나머지 124회는 피해 0(회피 78회 + 0으로 막은 46회)
        self.assertEqual((len(landed), len(hits) - len(landed), sum(e.data['amount'] for e in landed)), (679, 124, 1_302_048))

    def test_cooldowns_are_fixed_per_skill(self):
        pr = self.load('111.rrf')
        seen = collections.defaultdict(set)
        for e in pr.events:
            if e.event_type == E.SKILL_DELAY and not e.data.get('carried'):
                seen[e.data['skill']].add(e.data['delay_ms'])
        self.assertTrue(seen)
        self.assertTrue(all(len(v) == 1 for v in seen.values()))               # 스킬마다 값이 하나

    def test_quests_and_market_search(self):
        pr = self.load('qewq.rrf')
        quests = {q['quest']: q for q in tracking.quests(Context.build(pr, FakeGameData(), {}))['quests']}
        self.assertEqual((quests[7897]['count'], quests[7897]['need']), (14, 40))
        self.assertEqual([(g['name'], g['count']) for g in quests[7897]['goals']], [('블랙 어시더스', 6), ('실버 어시더스', 8)])
        found = ingame_market.searches(pr.events)
        self.assertEqual(min(r['price'] for s in found for r in s['rows']), 7490)
        opened = [r for e in pr.events if e.event_type == E.STORE_ITEMS for r in e.data['rows']]
        spent = [-e.data['amount'] for e in pr.events if e.event_type == E.ZENY_FLOW and e.data['amount'] < 0]
        self.assertIn(8500 * 10, spent)                                         # 연 노점의 값 × 10개 = 실제로 나간 제니
        self.assertIn(8500, [r['price'] for r in opened])

    def test_merchant_overcharge_level(self):
        self.assertEqual(LR.overcharge_level(self.load('1.rrf').events), 10)

    def test_pickup_refine_cards_and_grade_match_the_storage_list(self):
        """창고에서 꺼낸 장비: 획득 패킷의 제련·카드·등급이 창고 목록의 같은 아이템과 같아야 한다."""
        pr = self.load('11.rrf')
        stored = collections.defaultdict(set)
        for typ, items, _tm in pr.inventory_lists:
            if typ == 2:
                for it in items:
                    stored[it['id']].add((it.get('refine'), tuple(c for c in it.get('cards') or [] if c), it.get('grade')))
        checked = nonzero = 0
        for e in pr.events:
            if e.event_type == E.ITEM_GAIN and len(stored.get(e.data['item_id'], ())) == 1:
                got = (e.data['refine'], tuple(e.data['cards']), e.data['grade'])
                self.assertIn(got, stored[e.data['item_id']])
                checked += 1
                nonzero += bool(got[0] or got[1])
        self.assertEqual((checked, nonzero), (20, 18))


if __name__ == '__main__':
    unittest.main()
