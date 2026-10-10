"""분석기 단위 테스트 (합성 이벤트)."""
import os
import unittest

from helpers import PLAYER, FakeGameData, ev, mob_actor, parsed

from analyzer.analyzers import buff, combat, exp_zeny, loot, skill
from analyzer.analyzers.context import Context
from analyzer.events import EventType as E
from analyzer.report import analyze, compare
from core.models import PriceInfo

MOB1, MOB2 = 5001, 5002
SETTINGS = {'combat_gap_ms': 3000, 'rare_item_value': 1_000_000, 'price_basis': 'lowest'}


def hunt_events():
    evs = [
        ev(0, E.MAP_CHANGE, PLAYER, map_name='field'),
        ev(1000, E.MONSTER_SPAWN, MOB1, class_id=1002, name='포링'),
        ev(1000, E.MONSTER_SPAWN, MOB2, class_id=1002, name='포링'),
        ev(1000, E.BUFF_START, PLAYER, efst=10),
        ev(2000, E.SKILL_CAST_START, PLAYER, MOB1, skill=100, cast_ms=500),
        ev(2500, E.DAMAGE, PLAYER, MOB1, amount=1000, skill=100, miss=False),
        ev(3000, E.DAMAGE, PLAYER, MOB1, amount=500, skill=None, miss=False, hit='critical'),
        ev(3500, E.MONSTER_DEATH, MOB1, class_id=1002),
        ev(10000, E.DAMAGE, PLAYER, MOB2, amount=2000, skill=100, miss=False),
        ev(10500, E.DAMAGE, PLAYER, MOB2, amount=0, skill=None, miss=True),
        ev(11000, E.DAMAGE, MOB2, PLAYER, amount=300, miss=False),
        ev(12000, E.MONSTER_DEATH, MOB2, class_id=1002),
        ev(20000, E.BUFF_END, PLAYER, efst=10),
        ev(25000, E.BUFF_START, PLAYER, efst=10),
        ev(5000, E.BASE_EXP_GAIN, PLAYER, amount=100),
        ev(13000, E.BASE_EXP_GAIN, PLAYER, amount=100),
        ev(13000, E.JOB_EXP_GAIN, PLAYER, amount=50),
        ev(30000, E.ZENY_CHANGE, PLAYER, value=1500),
    ]
    inv = [(0, [dict(id=501, amount=10, equip=False)], 1000),
           (0, [dict(id=501, amount=7, equip=False), dict(id=909, amount=5, equip=False),
                dict(id=4001, amount=1, equip=False)], 41000)]
    actors = {MOB1: mob_actor(), MOB2: mob_actor()}
    return parsed(evs, 40000, actors, inv, start_zeny=1000)


def prices(ids):
    table = {501: 50, 909: 10, 4001: 2_000_000}
    return {i: PriceInfo(i, table[i], table[i], table[i], 3, 0) for i in ids if i in table}


class CombatTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context.build(hunt_events(), FakeGameData(), SETTINGS, 0, 40000)

    def test_totals(self):
        r = combat.analyze(self.ctx)
        self.assertEqual(r['total_damage'], 3500)
        self.assertEqual(r['hit_count'], 3)
        self.assertEqual(r['miss'], 1)
        self.assertEqual(r['critical'], 1)
        self.assertEqual((r['max_hit'], r['min_hit']), (2000, 500))
        self.assertEqual(r['kills'], 2)
        self.assertAlmostEqual(r['kill_time_avg'], (1.0 + 2.0) / 2)
        self.assertEqual(r['taken_total'], 300)

    def test_dps_bases(self):
        r = combat.analyze(self.ctx)
        # 전투시간: 2500→3000(0.5s) + 3000→10000(gap 3s) + 10000→10500(0.5s) = 4s
        self.assertEqual(r['combat_ms'], 4000)
        self.assertAlmostEqual(r['dps_combat'], 3500 / 4)
        self.assertAlmostEqual(r['dps_total'], 3500 / 40)
        self.assertEqual(r['burst'][1]['dps'], 2000)

    def test_max_window(self):
        self.assertEqual(combat.max_window_sum([(0, 1), (400, 2), (1300, 4)], 1000)[0], 6)


class SkillTest(unittest.TestCase):
    def test_skill_rows(self):
        r = skill.analyze(Context.build(hunt_events(), FakeGameData(), SETTINGS, 0, 40000))
        s = r['skills'][0]
        self.assertEqual(s['skill_id'], 100)
        self.assertEqual(s['total_damage'], 3000)
        self.assertAlmostEqual(s['share'], 3000 / 3500 * 100)
        self.assertEqual(s['uses'], 2)
        self.assertEqual(s['cast_ms_avg'], 500)
        self.assertAlmostEqual(s['avg_interval'], 7.5)

    def test_expected_delay(self):
        st = dict(SETTINGS, expected_delays={'100': 5.0})
        s = skill.analyze(Context.build(hunt_events(), FakeGameData(), st, 0, 40000))['skills'][0]
        self.assertAlmostEqual(s['efficiency_expected'], 5.0 / 7.5 * 100)


class BuffTest(unittest.TestCase):
    def test_uptime_and_gap(self):
        pts = [(2500, 1000), (3000, 500), (10000, 2000)]
        r = buff.analyze(Context.build(hunt_events(), FakeGameData(), SETTINGS, 0, 40000), pts)
        b = r['buffs'][0]
        self.assertEqual(b['active_ms'], 19000 + 15000)
        self.assertAlmostEqual(b['uptime'], 34000 / 40000 * 100)
        self.assertEqual(len(b['gaps']), 1)
        self.assertEqual(b['gaps'][0]['sec'], 5.0)
        self.assertEqual(b['dps_note'], '데이터 부족')


class ExpZenyTest(unittest.TestCase):
    def test_exp(self):
        r = exp_zeny.analyze_exp(Context.build(hunt_events(), FakeGameData(), SETTINGS, 0, 40000))
        self.assertEqual((r['base_total'], r['job_total']), (200, 50))
        self.assertAlmostEqual(r['base_per_hour'], 200 * 90)

    def test_zeny(self):
        r = exp_zeny.analyze_zeny(Context.build(hunt_events(), FakeGameData(), SETTINGS, 0, 40000))
        self.assertEqual((r['start'], r['end'], r['net']), (1000, 1500, 500))


class LootProfitTest(unittest.TestCase):
    def test_inventory_diff_loot(self):
        ctx = Context.build(hunt_events(), FakeGameData(), SETTINGS, 0, 40000)
        r = loot.analyze(ctx, prices)
        self.assertTrue(r['available'])
        got = {x['item_id']: x['quantity'] for x in r['loot']}
        self.assertEqual(got, {909: 5, 4001: 1})
        self.assertEqual({x['item_id']: x['quantity'] for x in r['consumables']}, {501: 3})
        self.assertEqual(r['loot_value'], 50 + 2_000_000)
        self.assertEqual(r['consumable_cost'], 150)
        self.assertEqual([x['item_id'] for x in r['rare']], [4001])
        z = exp_zeny.analyze_zeny(ctx)
        p = loot.profit(ctx, r, z)
        self.assertEqual(p['profit'], 2_000_050 + 500 - 150)

    def test_pickup_packets_beat_the_inventory_diff(self):
        """획득 패킷이 있으면 그것으로 센다. 창고에서 꺼낸 카드(4001)는 인벤토리에는 늘었지만 획득이 아니다."""
        pr = hunt_events()
        pr.events += [ev(15000, E.ITEM_GAIN, PLAYER, item_id=909, amount=5, source='LOOT'),
                      ev(16000, E.ITEM_GAIN, PLAYER, item_id=4001, amount=1, source='STORAGE')]
        r = loot.analyze(Context.build(pr, FakeGameData(), SETTINGS, 0, 40000), prices)
        self.assertEqual({x['item_id']: x['quantity'] for x in r['loot']}, {909: 5})
        self.assertEqual(r['conf'], '●')
        self.assertEqual({x['item_id']: x['quantity'] for x in r['consumables']}, {501: 3})   # 소모는 그대로 인벤토리 비교

    def test_storage_only_session_has_no_loot(self):
        pr = hunt_events()
        pr.events.append(ev(16000, E.ITEM_GAIN, PLAYER, item_id=4001, amount=1, source='STORAGE'))
        r = loot.analyze(Context.build(pr, FakeGameData(), SETTINGS, 0, 40000), prices)
        self.assertTrue(r['available'])
        self.assertEqual((r['loot'], r['loot_value']), ([], 0))

    def test_no_snapshot(self):
        pr = hunt_events()
        pr.inventory_lists = []
        r = loot.analyze(Context.build(pr, FakeGameData(), SETTINGS, 0, 40000), prices)
        self.assertFalse(r['available'])


class ReportCompareTest(unittest.TestCase):
    def test_report_and_compare(self):
        gd = FakeGameData()
        a, _, _ = analyze(hunt_events(), gd, SETTINGS, prices, 'a')
        pr_b = hunt_events()
        for e in pr_b.events:
            if e.event_type == E.DAMAGE and e.actor_id == PLAYER:
                e.data['amount'] *= 2
        b, _, _ = analyze(pr_b, gd, SETTINGS, prices, 'b')
        self.assertTrue(a['segments'][0]['is_hunt'])
        self.assertEqual(a['window']['label'].split(' · ')[0], '구간 1')
        c = compare([a, b], ['A', 'B'])
        row = next(r for r in c['rows'] if r['metric'] == 'Combat DPS')
        self.assertAlmostEqual(row['diffs'][1], 100.0)
        sk = next(s for s in c['skill_rows'] if s['skill_id'] == 100)
        self.assertEqual(sk['damage'], [3000, 6000])
        self.assertEqual(c['warnings'], [])

    def test_compare_warnings(self):
        gd = FakeGameData()
        a, _, _ = analyze(hunt_events(), gd, SETTINGS, prices, 'a')
        pr_b = hunt_events()
        pr_b.base_level = 200
        for e in pr_b.events:
            e.map_name = 'other'
        b, _, _ = analyze(pr_b, gd, SETTINGS, prices, 'b')
        w = ' '.join(compare([a, b], ['A', 'B'])['warnings'])
        self.assertIn('사냥터', w)
        self.assertIn('레벨', w)

    def test_no_combat_is_honest(self):
        pr = parsed([ev(0, E.MAP_CHANGE, PLAYER)], 20000)
        r, _, _ = analyze(pr, FakeGameData(), SETTINGS, prices, 'x')
        self.assertFalse(r['combat']['available'])
        self.assertIsNone(r['kpi']['combat_dps'])
        self.assertFalse(r['exp']['available'])


class PacketTest(unittest.TestCase):
    def test_registry_excludes_unverified_by_default(self):
        from analyzer.packets import registry
        self.assertNotIn(0x01de, registry())
        self.assertIn(0x01de, registry(experimental=True))
        self.assertTrue(all(d.verified for d in registry().values()))

    def test_normalizer_length_mismatch(self):
        import struct
        from analyzer.normalizer import normalize

        class FakeRp:
            date = (2026, 9, 20, 1, 2, 3)

            def meta_chunks(self, t):
                return {1010: struct.pack('<i', PLAYER)} if t == 3 else {}

            def packets(self):
                yield 0, struct.pack('<HIB', 0x0080, 7, 1)             # 정상 vanish
                yield 10, struct.pack('<HI', 0x0080, 7)                # 길이 불일치
                yield 20, struct.pack('<HH', 0x7777, 0)                # 미해석
                yield 30, struct.pack('<HIIIiiiBHBi', 0x08c8, PLAYER, 9, 0, 0, 0, 1234, 0, 1, 10, 0)

            def inventory_lists(self, packets=None, with_time=False):
                return []

            def server(self):
                return None

        pr = normalize(FakeRp(), 'fake.rrf')
        self.assertEqual(pr.unsupported_counts[0x0080], 1)
        self.assertEqual(pr.unknown_counts[0x7777], 1)
        d = [e for e in pr.events if e.event_type == E.DAMAGE][0]
        self.assertEqual((d.actor_id, d.target_id, d.data['amount'], d.data['hit']), (PLAYER, 9, 1234, 'critical'))


class NameFileTest(unittest.TestCase):
    def test_import_json_names(self):
        """스킬/버프 이름 가져오기: JSON은 {"id": "이름"}, 기존 이름은 덮어쓰지 않는다."""
        import json
        import tempfile
        from analyzer.gamedata import _NameFile
        d = tempfile.mkdtemp()
        names = _NameFile(os.path.join(d, 'skill_names.json'), '스킬')
        self.assertEqual(names.name(5), '스킬 #5')
        path = os.path.join(d, 'src.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'5': '기본공격', '12': '스톰거스트', 'bad': 'x'}, f)
        self.assertEqual(names.import_file(path), 2)
        self.assertEqual(names.name(12), '스톰거스트')
        self.assertEqual(_NameFile(names.path, '스킬').name(5), '기본공격')     # 저장됨
        self.assertEqual(names.import_file(path), 0)                            # 이미 있으면 추가 없음

    def test_user_set_name(self):
        """이름을 모르면 번호로 두고, 사용자가 확인한 이름만 저장한다."""
        import tempfile
        from analyzer.gamedata import _NameFile
        path = os.path.join(tempfile.mkdtemp(), 'buff_names.json')
        names = _NameFile(path, '버프')
        self.assertEqual(names.name(993), '버프 #993')
        names.set(993, ' 어깨동무 ')
        self.assertEqual(_NameFile(path, '버프').name(993), '어깨동무')
        names.set(993, '')
        self.assertEqual(_NameFile(path, '버프').name(993), '버프 #993')


if __name__ == '__main__':
    unittest.main()
