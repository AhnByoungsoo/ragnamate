"""기존 기능 보호: 실제 샘플 Replay의 창고 파싱/아이템 ID/타임스탬프/Event 정규화."""
import os
import unittest

from helpers import RO_DIR, sample

from rrf import Replay


@unittest.skipUnless(sample('20260910-1.rrf'), '샘플 Replay 없음')
class StorageRegression(unittest.TestCase):
    path = sample('20260910-1.rrf')

    def test_header_timestamp(self):
        self.assertEqual(Replay(self.path).date, (2026, 9, 10, 21, 12, 55))

    def test_storage_lists(self):
        st = Replay(self.path).storages()
        self.assertEqual([len(s) for s in st], [289, 514, 140, 149])
        first = st[0][0]
        self.assertEqual(set(first) >= {'id', 'amount', 'cards', 'refine', 'grade', 'equip'}, True)

    def test_inventory_lists_default_unchanged(self):
        rp = Replay(self.path)
        a = rp.inventory_lists()
        b = rp.inventory_lists(packets=list(rp.packets()))
        self.assertEqual(a, b)
        self.assertTrue(all(len(x) == 2 for x in a))

    @unittest.skipUnless(os.path.exists(os.path.join(RO_DIR, 'System', 'itemInfo_true.lub')), 'itemInfo 없음')
    def test_storage_service(self):
        from core.items import ItemDB
        from services import replay_service
        import tempfile
        db = ItemDB(RO_DIR, os.path.join(tempfile.gettempdir(), 'ro_test_item_db.json')).load()
        res = replay_service.load_replay(self.path, db)
        self.assertEqual(res.server, '바포메트')
        self.assertEqual(res.storage_count, 4)
        self.assertEqual(len(res.entries), 1289)
        self.assertEqual(sum(1 for e in res.entries if e.source == '창고2'), 514)
        self.assertTrue(all(not e.name.startswith('알 수 없는') for e in res.entries[:50]))


@unittest.skipUnless(sample('43ww.rrf'), '샘플 Replay 없음')
class NormalizerRegression(unittest.TestCase):
    def setUp(self):
        from analyzer.normalizer import normalize
        p = sample('43ww.rrf')
        self.pr = normalize(Replay(p), p)

    def test_session_meta(self):
        pr = self.pr
        self.assertEqual(pr.player_aid, 11122259)
        self.assertEqual(pr.character, '일산상인뱅스요')
        self.assertEqual((pr.job_id, pr.base_level), (4078, 115))
        self.assertEqual(pr.server, '바포메트')

    def test_map_changes(self):
        maps = [e.map_name for e in self.pr.events if e.event_type == 'MAP_CHANGE']
        self.assertEqual(maps, ['prontera', 'prt_fild08', 'prontera', 'prt_mk'])

    def test_zeny_matches_meta(self):
        z = [e.data['value'] for e in self.pr.events if e.event_type == 'ZENY_CHANGE']
        self.assertEqual(z, [294501763])
        self.assertEqual(self.pr.start_zeny - z[0], 50)

    def test_unknown_packets_kept(self):
        self.assertGreater(sum(self.pr.unknown_counts.values()), 0)
        self.assertEqual(sum(self.pr.unsupported_counts.values()), 0)


@unittest.skipUnless(sample('4.rrf'), '샘플 Replay 없음')
class VitalsRegression(unittest.TestCase):
    def test_hp_never_exceeds_max(self):
        """varID 매핑 검증: 같은 시각의 패킷 묶음이 끝난 뒤 현재 HP(5) ≤ 최대 HP(6).
        (맵 로딩 시 최대 HP가 장비 반영으로 여러 단계에 걸쳐 올라가므로 묶음 중간은 제외)"""
        from analyzer.normalizer import normalize
        p = sample('4.rrf')
        pr = normalize(Replay(p), p)
        cur = mx = None
        hp = [e for e in pr.events if e.event_type == 'HP_CHANGE']
        for i, e in enumerate(hp):
            if 'max' in e.data:
                mx = e.data['max']
            else:
                cur = e.data['value']
            batch_end = i + 1 == len(hp) or hp[i + 1].timestamp_ms - e.timestamp_ms > 50
            if batch_end and cur is not None and mx is not None:
                self.assertLessEqual(cur, mx)
        self.assertIsNotNone(cur)

    def test_hp_regen_on_current_var(self):
        """현재 HP(var5)는 6초 주기 자연회복으로 단독 증가한다."""
        from analyzer.normalizer import normalize
        p = sample('4.rrf')
        pr = normalize(Replay(p), p)
        first = [e.data['value'] for e in pr.events if e.event_type == 'HP_CHANGE' and 'value' in e.data][:4]
        self.assertEqual(first, sorted(first))
        self.assertEqual(len(set(first)), 4)


class RecordingInProgressTest(unittest.TestCase):
    """녹화 중(패킷 컨테이너가 아직 없는) Replay를 열면 이유가 보이는 오류가 나야 한다."""

    def test_missing_packet_container(self):
        import struct
        from test_replay_incremental import synthetic
        data = bytearray(synthetic())
        for i in range(24):
            t, ln, off = struct.unpack_from('<Hii', data, 112 + i * 10)
            if t == 1:
                struct.pack_into('<Hii', data, 112 + i * 10, 1, 0, 0)      # 아직 안 쓰인 상태
        with self.assertRaises(ValueError) as cm:
            list(Replay.from_bytes(bytes(data)).packets())
        self.assertIn('녹화', str(cm.exception))

    def test_normal_file_still_reads(self):
        from test_replay_incremental import synthetic
        self.assertGreater(len(list(Replay.from_bytes(synthetic()).packets())), 0)


if __name__ == '__main__':
    unittest.main()
