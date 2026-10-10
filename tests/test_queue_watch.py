"""인던 대기열 알림: 패킷 → 이벤트 → LIVE 상태 → 기준 이하로 내려온 순간 한 번."""
import struct
import unittest

import helpers  # noqa: F401  (sys.path 설정)

from analyzer import packets as P  # noqa: E402
from analyzer.events import EventType as E, ReplayEvent  # noqa: E402
from core.queue_watch import QueueWatch  # noqa: E402
from live.state import GameStateReducer  # noqa: E402

DEFS = P.PACKET_SETS[P.DEFAULT_PACKET_VER]
NAME = '바곳의 연구실'


def queue_packet(position):
    return struct.pack('<H61sH', 0x02cb, NAME.encode('cp949'), position)


class PacketTest(unittest.TestCase):
    def test_queue_packet_reads_name_and_position(self):
        d = DEFS[0x02cb]
        self.assertEqual((d.length, len(queue_packet(105))), (65, 65))
        self.assertEqual(d.decode(queue_packet(105)), dict(name=NAME, position=105))

    def test_position_only_update_is_marked_unverified(self):
        self.assertFalse(DEFS[0x02cc].verified)
        self.assertEqual(DEFS[0x02cc].decode(struct.pack('<HH', 0x02cc, 9)), dict(name='', position=9))


class StateTest(unittest.TestCase):
    def event(self, t, typ, **data):
        return ReplayEvent(t, typ, helpers.PLAYER, None, 'prontera', data=data)

    def test_queue_follows_updates_and_ends_when_instance_opens(self):
        s = GameStateReducer(helpers.PLAYER)
        self.assertIsNone(s.view()['queue'])
        s.apply(self.event(1000, E.INSTANCE_QUEUE, name=NAME, position=105))
        s.apply(self.event(2000, E.INSTANCE_QUEUE, name='', position=9))     # 순번만 온 갱신
        self.assertEqual(s.view()['queue'], dict(name=NAME, position=9, at=2000))
        s.apply(self.event(3000, E.INSTANCE_READY, name=NAME))
        self.assertIsNone(s.view()['queue'])


class WatchTest(unittest.TestCase):
    def test_estimates_between_updates_and_alerts_before_the_next_one(self):
        now = [100.0]
        w = QueueWatch(clock=lambda: now[0])
        q = lambda at, n: dict(name=NAME, position=n, at=at)
        self.assertIsNone(w.check(q(34700, 18), 10))         # 소식 하나로는 빠르기를 모른다
        now[0] += 2.9
        self.assertIsNone(w.check(q(37600, 14), 10))         # 2.9초에 4칸 = 초당 1.38칸
        now[0] += 2.0
        self.assertAlmostEqual(w.estimate(), 14 - 4 / 2.9 * 2.0, places=3)
        self.assertIsNone(w.check(q(37600, 14), 10))         # 지금쯤 11.2번째
        now[0] += 0.9
        self.assertEqual(w.check(q(37600, 14), 10),          # 다음 소식(10번째)이 오기 전에 알린다
                         '대기열이 10 이하입니다 (바곳의 연구실 · 마지막 확인 14번째, 지금쯤 10번째로 계산)')
        now[0] += 0.1
        self.assertIsNone(w.check(q(40500, 10), 10))         # 진짜 소식이 와도 다시 알리지 않는다

    def test_estimate_stops_after_one_update_gap(self):
        now = [0.0]
        w = QueueWatch(clock=lambda: now[0])
        w.check(dict(name=NAME, position=30, at=0), 10)
        now[0] = 3.0
        w.check(dict(name=NAME, position=26, at=3000), 10)
        now[0] = 60.0                                        # 소식이 끊겼다 — 줄어들고 있다고 넘겨짚지 않는다
        self.assertEqual(w.estimate(), 22)
        self.assertIsNone(w.check(dict(name=NAME, position=26, at=3000), 10))

    def test_fires_once_when_crossing_and_again_for_a_new_queue(self):
        w = QueueWatch()
        q = lambda n: dict(name=NAME, position=n)
        self.assertIsNone(w.check(None, 10))
        self.assertIsNone(w.check(q(11), 10))
        self.assertEqual(w.check(q(10), 10), '대기열이 10 이하입니다 (바곳의 연구실 · 10번째)')
        self.assertIsNone(w.check(q(7), 10))                 # 같은 대기에서는 다시 알리지 않는다
        self.assertIsNone(w.check(None, 10))                 # 입장해서 대기가 끝남
        self.assertIsNotNone(w.check(q(3), 10))              # 다음 캐릭터의 대기는 다시 알린다

    def test_off_means_no_alert(self):
        w = QueueWatch()
        self.assertIsNone(w.check(None, 10))                 # 꺼 두면 queue 를 넘기지 않는다


if __name__ == '__main__':
    unittest.main()
