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
