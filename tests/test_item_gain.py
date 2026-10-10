"""획득 패킷(0x0b41)과 제니 증감: 주운 것 / 창고에서 꺼낸 것 / 산 것을 앞뒤 패킷으로 가른다."""
import struct
import unittest

from helpers import PLAYER, sample

from analyzer import packets as P
from analyzer.events import (FROM_STORAGE, LOOT, NPC_SALE, OTHER_GAIN, OTHER_INCOME, PURCHASE, SPEND, VEND_SALE,
                             EventType as E)
from analyzer.normalizer import PacketNormalizer
from live.state import GameStateReducer

CARD, JUNK = 4001, 7219
START_ZENY = 1_000_000


def pickup(item_id, count=1, index=10, result=0):
    b = bytearray(70)
    struct.pack_into('<HHHI', b, 0, 0x0b41, index, count, item_id)
    b[33] = result
    return bytes(b)


def zeny(value):
    return struct.pack('<HHi', 0x00b1, 20, value)


def raw(header, size):
    return struct.pack('<H', header) + bytes(size - 2)


FLOOR, STORAGE = raw(0x00a1, 6), raw(0x00f6, 6)
TICK = raw(0x7777, 4)                    # 아무 뜻 없는 패킷(시간만 흐르게 한다)


class FakeReplay:
    def meta_chunks(self, kind):
        return {1010: struct.pack('<i', PLAYER), 1051: struct.pack('<i', START_ZENY)} if kind == 3 else {}


def run(*timeline, experimental=False):
    """[(시각 ms, 패킷)] → (획득 이벤트, 제니 이벤트). 마지막에 판정 대기분을 내보낸다."""
    n = PacketNormalizer(FakeReplay(), experimental)
    out = []
    for i, (tm, packet) in enumerate(timeline):
        out += n.feed(tm, packet, i)
    out += n.flush()
    return ([e for e in out if e.event_type == E.ITEM_GAIN], [e for e in out if e.event_type == E.ZENY_FLOW])


class PickupPacketTest(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(P.d_pickup(pickup(CARD, 3, index=42)),
                         dict(index=42, count=3, item_id=CARD, result=0, cards=[], refine=0, grade=0))
        self.assertTrue(P.registry()[0x0b41].verified)

    def test_failed_pickup_is_not_a_gain(self):
        gains, _ = run((0, pickup(0, 0, result=6)), (10, pickup(CARD, 1, result=1)))
        self.assertEqual(gains, [])


class GainSourceTest(unittest.TestCase):
    def source(self, *timeline):
        gains, _ = run(*timeline)
        return [g.data['source'] for g in gains]

    def test_floor_item_vanishing_before_or_just_after_means_loot(self):
        self.assertEqual(self.source((1000, FLOOR), (1010, pickup(JUNK))), [LOOT])
        self.assertEqual(self.source((1000, pickup(JUNK)), (1050, FLOOR)), [LOOT])     # 실측: 0~80ms 뒤에 오기도 한다

    def test_storage_withdrawal_is_not_loot(self):
        self.assertEqual(self.source((1000, STORAGE), (1005, pickup(CARD))), [FROM_STORAGE])

    def test_purchase_when_zeny_drops_around_it(self):
        self.assertEqual(self.source((1000, zeny(START_ZENY - 40_000)), (1010, pickup(CARD))), [PURCHASE])
        self.assertEqual(self.source((1000, pickup(CARD)), (1045, zeny(START_ZENY - 85_000))), [PURCHASE])

    def test_nothing_around_is_other(self):
        self.assertEqual(self.source((1000, pickup(CARD)), (5000, TICK)), [OTHER_GAIN])    # 상자·퀘스트 보상 등

    def test_late_marker_does_not_rewrite_history(self):
        self.assertEqual(self.source((1000, pickup(CARD)), (1400, FLOOR)), [OTHER_GAIN])   # 150ms 넘으면 남의 일

    def test_gain_waits_for_its_verdict_before_going_out(self):
        n = PacketNormalizer(FakeReplay())
        self.assertEqual(n.feed(1000, pickup(JUNK), 0), [])          # 아직 출처를 모른다
        self.assertEqual(n.feed(1100, TICK, 1), [])
        out = n.feed(1200, TICK, 2)                                  # 150ms 지남 → 내보낸다
        self.assertEqual([(e.event_type, e.data['item_id'], e.data['amount']) for e in out],
                         [(E.ITEM_GAIN, JUNK, 1)])


class ZenyFlowTest(unittest.TestCase):
    def flows(self, *timeline, experimental=True):
        _, flows = run(*timeline, experimental=experimental)
        return [(f.data['amount'], f.data['kind']) for f in flows]

    def test_first_packet_is_compared_with_the_recorded_start(self):
        self.assertEqual(self.flows((0, zeny(START_ZENY - 50))), [(-50, SPEND)])       # 창고 이용료
        self.assertEqual(self.flows((0, zeny(START_ZENY))), [])

    def test_plain_income_is_other(self):
        self.assertEqual(self.flows((0, zeny(START_ZENY + 5000)), (2000, TICK)), [(5000, OTHER_INCOME)])

    def test_shop_and_vending_markers_classify_income(self):
        sold = raw(0x00cb, 3)
        self.assertEqual(self.flows((0, sold), (20, zeny(START_ZENY + 124_000))), [(124_000, NPC_SALE)])
        self.assertEqual(self.flows((0, zeny(START_ZENY + 124_000)), (30, sold)), [(124_000, NPC_SALE)])
        vend = raw(0x09e5, 18)
        self.assertEqual(self.flows((0, vend), (5, zeny(START_ZENY + 15_000_000))), [(15_000_000, VEND_SALE)])

    def test_unverified_markers_are_ignored_unless_experimental(self):
        self.assertEqual(self.flows((0, raw(0x00cb, 3)), (20, zeny(START_ZENY + 1000)), experimental=False),
                         [(1000, OTHER_INCOME)])

    def test_wrong_length_marker_is_not_trusted(self):
        self.assertEqual(self.flows((0, raw(0x09e5, 6)), (5, zeny(START_ZENY + 1000))), [(1000, OTHER_INCOME)])


class ReducerTest(unittest.TestCase):
    def test_only_earned_items_reach_the_running_totals(self):
        gains, flows = run((0, FLOOR), (5, pickup(JUNK, 2)), (1000, STORAGE), (1005, pickup(CARD)),
                           (2000, zeny(START_ZENY + 700)), (4000, TICK))
        s = GameStateReducer(PLAYER, START_ZENY)
        for e in gains + flows:
            s.apply(e)
        view = s.view()
        self.assertEqual(view['ledger'], dict(items={JUNK: 2}, income={OTHER_INCOME: 700}, spent=0,
                                              last={JUNK: gains[0].timestamp_ms}))      # 언제 마지막으로 얻었는지
        self.assertEqual([(g['seq'], g['item_id'], g['amount']) for g in view['gains']], [(1, JUNK, 2)])


class RealReplayTest(unittest.TestCase):
    """2026-10-10 실측값. 이 숫자가 바뀌면 분류 규칙이 달라진 것이다."""
    EXPECTED = {'1weq.rrf': {LOOT: 261}, '11.rrf': {FROM_STORAGE: 23}, 'qewq.rrf': {PURCHASE: 5}}

    def test_sources_match_what_was_measured(self):
        import collections
        from analyzer.normalizer import normalize
        from rrf import Replay
        checked = 0
        for name, want in self.EXPECTED.items():
            path = sample(name)
            if not path:
                continue
            pr = normalize(Replay(path), path, experimental=True)
            got = collections.Counter(e.data['source'] for e in pr.events if e.event_type == E.ITEM_GAIN)
            self.assertEqual(dict(got), want, name)
            checked += 1
        if not checked:
            self.skipTest('local replay unavailable')

    def test_purchases_show_up_as_spending(self):
        path = sample('qewq.rrf')
        if not path:
            self.skipTest('local replay unavailable')
        from analyzer.normalizer import normalize
        from rrf import Replay
        pr = normalize(Replay(path), path, experimental=True)
        flows = [e.data for e in pr.events if e.event_type == E.ZENY_FLOW]
        self.assertEqual(sum(f['amount'] for f in flows), -483_050)
        self.assertTrue(all(f['kind'] == SPEND for f in flows))


if __name__ == '__main__':
    unittest.main()
