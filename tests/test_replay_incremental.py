import os
import struct
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from helpers import FakeGameData, PLAYER, ev
from analyzer.events import EventType as E
from analyzer.framing import Frame, PacketFramer
from analyzer.normalizer import normalize
from live.reader import IncrementalReplayReader
from live.replay_engine import ReplayLiveSession, offline_metrics
from live.sequencer import EventSequencer
from live.simulator import ReplayFeedSimulator
from live.state import GameStateReducer
from rrf import Replay
from live.journal import LiveJournal


def make_replay(packets):
    header = bytearray(352)
    header[100] = 5
    struct.pack_into('<HBBBBBB', header, 104, 2026, 9, 20, 0, 12, 0, 0)
    crypt = Replay.from_bytes(bytes(header))._crypt
    def meta(chunks):
        return crypt(b''.join(struct.pack('<hi', key, len(value)) + value for key, value in chunks))
    sess = meta([(1010, struct.pack('<i', PLAYER)), (1051, struct.pack('<i', 1000))])
    data = meta([(964, b'tester\0'), (965, b'field\0')])
    start = 352 + len(sess) + len(data)
    records = b''.join(struct.pack('<iiH', i, t, len(p)) + crypt(p) for i, (t, p) in enumerate(packets))
    for i, c in enumerate([(1, len(records), start), (3, len(sess), 352), (2, len(data), 352 + len(sess))]):
        struct.pack_into('<Hii', header, 112 + i * 10, *c)
    return bytes(header) + sess + data + records


def par(var, val, pid=0xb0):
    return struct.pack('<HHi', pid, var, val)


def spawn(aid=2000):
    p = bytearray(90)
    struct.pack_into('<HHBI', p, 0, 0x9ff, len(p), 5, aid)
    struct.pack_into('<H', p, 23, 1002)
    p[84:] = b'poring'
    return bytes(p)


def hit(tick=1, sid=10, amount=100, div=3):
    return struct.pack('<HHIIIiiiHhB', 0x1de, sid, PLAYER, 2000, tick, 0, 0, amount, 1, div, 0)


def synthetic():
    return make_replay([(0, par(5, 500)), (10, spawn()),
        (20, struct.pack('<HI I H H H I I B', 0x7fb, PLAYER, 2000, 0, 0, 10, 0, 100, 0)),
        (100, hit()), (110, hit(amount=200)), (120, hit(amount=300)),
        (140, struct.pack('<HIB', 0x80, 2000, 1)),
        (150, struct.pack('<HHIBiiiii', 0x983, 50, PLAYER, 1, 1000, 1000, 0, 0, 0)),
        (200, struct.pack('<HHIBiiiii', 0x983, 50, PLAYER, 1, 1000, 1000, 0, 0, 0)),
        (500, struct.pack('<HHIB', 0x196, 50, PLAYER, 0)),
        (600, struct.pack('<HIqHh', 0xacc, PLAYER, 1234, 1, 0)),
        (650, struct.pack('<HIqHh', 0xacc, PLAYER, 456, 2, 0)),
        (700, par(20, 1200, 0xb1)), (1000, par(7, 55))])


class IncrementalTests(unittest.TestCase):
    def test_random_chunk_100_offline_final(self):
        sim = ReplayFeedSimulator(synthetic(), FakeGameData(), {'experimental_packets': True})
        for seed in range(100):
            with self.subTest(seed=seed):
                result = sim.run(seed, duplicate=True)
                self.assertTrue(result['equal'], result)
                self.assertEqual(result['metrics']['damage']['live'], 600)
                self.assertEqual(result['metrics']['kills']['live'], 1)
                self.assertEqual(result['metrics']['base_exp']['live'], 1234)
                self.assertEqual(result['metrics']['job_exp']['live'], 456)
                self.assertEqual(result['bytes_read'], result['file_bytes'])

    def test_every_byte_boundary(self):
        result = ReplayFeedSimulator(synthetic(), FakeGameData(), {'experimental_packets': True}).run(
            chunks=[1] * len(synthetic()), duplicate=True)
        self.assertTrue(result['equal'], result)

    def test_partial_17_then_11(self):
        record = struct.pack('<iiH', 0, 50, 18) + b'\xff\xff' + b'a' * 16
        f = PacketFramer(lambda b: bytes(b), 100)
        self.assertEqual(f.feed(record[:17], 100), [])
        self.assertEqual(len(f.pending), 17)
        frames = f.feed(record[17:], 117)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].end_offset, 128)

    def test_duplicate_overlap_same_attack_allowed(self):
        record = struct.pack('<iiH', 0, 5, 8) + par(5, 1)
        f = PacketFramer(lambda b: bytes(b))
        self.assertEqual(len(f.feed(record, 0)), 1)
        self.assertEqual(f.feed(record, 0), [])
        self.assertEqual(len(f.feed(record + record, 0)), 1)
        self.assertEqual(f.read_offset, 2 * len(record))

    def test_unknown_and_bad_payload_keep_boundary(self):
        data = make_replay([(0, b'\xff\xffaaaa'), (1, b'\xb0\x00xx'), (2, par(5, 44))])
        rp = Replay.from_bytes(data)
        p = normalize(rp, 'test')
        self.assertEqual(p.unknown_counts[0xffff], 1)
        self.assertEqual(p.unsupported_counts[0xb0], 1)
        self.assertEqual([e.data['value'] for e in p.events if e.event_type == E.HP_CHANGE], [44])

    def test_safe_resync(self):
        good = struct.pack('<iiH', 0, 10, 8) + par(5, 1)
        f = PacketFramer(lambda b: bytes(b))
        frames = f.feed(b'\xff' * 10 + good + good)
        self.assertEqual(len(frames), 2)
        self.assertEqual((f.resync, f.rejected), (1, 10))

    def test_truncate_replace(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.rrf'
            p.write_bytes(synthetic())
            r = IncrementalReplayReader(p)
            r.poll()
            first = r.session_id
            p.write_bytes(synthetic()[:100])
            self.assertTrue(r.poll().reset)
            self.assertNotEqual(first, r.session_id)
            p.write_bytes(synthetic())
            r.poll()
            second = r.session_id
            q = Path(d) / 'b.rrf'
            q.write_bytes(synthetic())
            os.replace(q, p)
            self.assertTrue(r.poll().reset)
            self.assertNotEqual(second, r.session_id)

    def test_open_container_header_update(self):
        data = bytearray(synthetic())
        struct.pack_into('<i', data, 114, 0)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.rrf'
            p.write_bytes(data[:-4])
            r = IncrementalReplayReader(p)
            r.poll()
            self.assertTrue(r.framer.pending)
            sid = r.session_id
            with p.open('ab') as f:
                f.write(data[-4:])
            self.assertEqual(len(r.poll().frames), 1)
            with p.open('r+b') as f:
                f.seek(114)
                f.write(synthetic()[114:118])
            self.assertFalse(r.poll().reset)
            self.assertEqual(r.session_id, sid)

    def test_same_size_inplace_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.rrf'
            p.write_bytes(synthetic())
            r = IncrementalReplayReader(p)
            r.poll()
            old = r.session_id
            changed = bytearray(synthetic())
            changed[-1] ^= 1
            p.write_bytes(changed)
            self.assertTrue(r.poll().reset)
            self.assertNotEqual(r.session_id, old)

    def test_final_resume_append(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.rrf'
            raw = bytearray(synthetic())
            struct.pack_into('<i', raw, 114, 0)
            p.write_bytes(raw)
            s = ReplayLiveSession(p, {'experimental_packets': True}, FakeGameData())
            s.step(1)
            self.assertTrue(s.finalize(70))
            packet = par(20, 1300, 0xb1)
            with p.open('ab') as f:
                f.write(struct.pack('<iiH', 20, 1200, len(packet)) + s.reader.replay._crypt(packet))
            s.step(71)
            s.step(72)
            self.assertEqual(s.status, 'ACTIVE')
            self.assertIsNone(s.accuracy)
            self.assertEqual(s.reducer.metrics()['zeny'], 300)

    def test_journal_recovery_reconstructs_state(self):
        with tempfile.TemporaryDirectory() as d:
            p, db = Path(d) / 'a.rrf', Path(d) / 'live.db'
            p.write_bytes(synthetic())
            j = LiveJournal(str(db))
            self.assertTrue(j.ready.wait(3))
            s = ReplayLiveSession(p, {'experimental_packets': True}, FakeGameData(), j)
            s.step(10)
            s.step(11)
            expected, sid = s.reducer.metrics(), s.reader.session_id
            j.checkpoint(s.reader.checkpoint())
            j.close()
            self.assertEqual(j.error, '')
            j = LiveJournal(str(db))
            self.assertTrue(j.ready.wait(3))
            recovered = ReplayLiveSession(p, {'experimental_packets': True}, FakeGameData(), j)
            recovered.step(20)
            recovered.step(21)
            self.assertTrue(recovered.recovered)
            self.assertEqual(recovered.reader.session_id, sid)
            self.assertEqual(recovered.reducer.metrics(), expected)
            self.assertTrue(recovered.finalize(90))
            j.close()
            import sqlite3
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM live_accuracy_reports').fetchone()[0], 1)

    def test_nonzero_inventory_quantity(self):
        def inventory(tm, amount):
            item = bytearray(34)
            struct.pack_into('<HIBHI', item, 0, 1, 909, 3, amount, 0)
            packet = struct.pack('<HHB', 0xb09, 39, 0) + item
            return [(tm, struct.pack('<HHB', 0xb08, 5, 0)), (tm + 1, packet),
                    (tm + 2, struct.pack('<HH', 0xb0b, 0))]
        data = make_replay(inventory(0, 2) + inventory(1000, 9))
        result = ReplayFeedSimulator(data, FakeGameData()).run(chunks=[1] * len(data))
        self.assertTrue(result['equal'], result)
        self.assertEqual(result['metrics']['loot']['live'], {909: 7})

    def test_stall_final_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.rrf'
            p.write_bytes(synthetic())
            s = ReplayLiveSession(p, {'experimental_packets': True, 'live_stall_sec': 5, 'live_finalize_sec': 60}, FakeGameData())
            s.step(1)
            s.step(10)
            self.assertEqual(s.status, 'STALLED')
            s.step(62)
            self.assertEqual(s.status, 'FINISHED')
            self.assertTrue(s.accuracy['equal'])
            snap = s.published
            view = snap.to_dict()
            view['metrics']['damage'] = -1
            self.assertEqual(snap.to_dict()['metrics']['damage'], 600)
            # Final results must survive unchanged polls.
            s.step(63)
            self.assertEqual(s.status, 'FINISHED')

    def test_no_final_for_partial(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.rrf'
            p.write_bytes(synthetic()[:-1])
            s = ReplayLiveSession(p, {}, FakeGameData())
            s.step(1)
            self.assertFalse(s.finalize(100))
            self.assertIsNone(s.accuracy)


class StateTests(unittest.TestCase):
    def test_reorder_and_late(self):
        q = EventSequencer(100)
        f = lambda offset, tm: Frame(offset, offset + 1, tm, b'xx', 1)
        out = []
        for offset, tm in enumerate((100, 150, 120, 300)):
            out += q.offer(f(offset, tm))
        self.assertEqual([x.timestamp for x in out], [100, 120, 150])
        q.offer(f(4, 10))
        self.assertEqual(q.late, 1)
        self.assertEqual(q.offer(f(4, 10)), [])
        self.assertEqual(q.duplicates, 1)

    def test_multihit_cast_is_not_three_casts(self):
        s = GameStateReducer(PLAYER)
        s.apply(ev(0, E.SKILL_CAST_START, PLAYER, 2, skill=10, cast_ms=100))
        for tm in (100, 110, 120):
            s.apply(ev(tm, E.DAMAGE, PLAYER, 2, skill=10, amount=100, div=3))
        self.assertEqual(s.metrics()['skills'][10], dict(casts=1, hits=3, uses=1))
        self.assertEqual(s.skills[10]['connected_hits'], 9)

    def test_map_change_id_reuse_kill(self):
        s = GameStateReducer(PLAYER)
        s.apply(ev(0, E.MONSTER_SPAWN, 2, class_id=1002))
        generation = s.entities.entities[2]['generation']
        s.apply(ev(1, E.DAMAGE, PLAYER, 2, amount=10))
        s.apply(ev(2, E.MAP_CHANGE, PLAYER, map_name='new'))
        self.assertFalse(s.entities.entities)
        s.apply(ev(3, E.MONSTER_SPAWN, 2, class_id=1002))
        self.assertGreater(s.entities.entities[2]['generation'], generation)
        s.apply(ev(4, E.MONSTER_DEATH, 2))
        self.assertEqual(s.kills, 0)

    def test_buff_refresh_expiry_and_reconciliation(self):
        s = GameStateReducer(PLAYER)
        s.apply(ev(10, E.BUFF_START, PLAYER, efst=1, remain_ms=100))
        s.apply(ev(50, E.BUFF_START, PLAYER, efst=1, remain_ms=100))
        self.assertEqual(len(s.buffs), 1)
        s.advance(120)
        self.assertTrue(s.buffs[1]['active'])
        s.advance(150)
        self.assertEqual(s.buffs[1]['ending'], 'ESTIMATED')
        s.apply(ev(160, E.BUFF_END, PLAYER, efst=1))
        self.assertEqual(s.buffs[1]['ending'], 'CONFIRMED')
        s.apply(ev(170, 'AP_CHANGE', PLAYER, value=50))
        s.apply(ev(180, 'AP_CHANGE', PLAYER, delta=10))
        s.apply(ev(190, 'AP_CHANGE', PLAYER, value=53))
        self.assertEqual(s.resources['AP'].value, 53)
        self.assertEqual(s.corrections, 1)

    def test_long_session_bounded_rolling(self):
        s = GameStateReducer(PLAYER)
        for i in range(100000):
            s.apply(ev(i * 10, E.DAMAGE, PLAYER, 2, amount=1))
        self.assertEqual(s.damage, 100000)
        self.assertLessEqual(len(s.rolling), 1000)
        self.assertEqual(len(s.recent), 500)
        self.assertEqual(s.view()['rolling_dps']['1'], 100)
        s.advance(s.clock + 10001)
        self.assertEqual(s.view()['rolling_dps']['10'], 0)
        self.assertEqual(s.combat_state, 'OUT_OF_COMBAT')


if __name__ == '__main__':
    unittest.main()
