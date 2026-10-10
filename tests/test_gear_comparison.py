"""착용 여부·시간·피해 대상·신뢰도와 처치선 계산을 검증한다."""
import helpers  # noqa: F401
import math
import unittest
from types import SimpleNamespace

from analyzer.events import ESTIMATE, MEASURED, EventType as E
from services import gear_comparison as C
from test_equipment_details import database


def event(t, kind, actor=None, target=None, confidence=MEASURED, **data):
    return SimpleNamespace(timestamp_ms=t, event_type=kind, actor_id=actor, target_id=target, data=data, confidence=confidence)


def parsed(events=(), inventories=None):
    item = dict(id=1, equip=True, wear_state=2, refine=12, grade=4, cards=[2, 999999, 0], options=[], index=5)
    lists = inventories if inventories is not None else [(0, [item, dict(item, id=2, wear_state=0)], 1000)]
    return SimpleNamespace(inventory_lists=lists, packets=[(1000, b'')], events=list(events), player_aid=1,
                           character='검증 캐릭터', base_level=250, path='example.rrf')


class GearTest(unittest.TestCase):
    def test_only_worn_inventory_latest_snapshot_keeps_unknown_cards(self):
        raw = dict(id=1, equip=True, wear_state=2, refine=10, grade=3, cards=[999999], options=[dict(id=7, value=12)], index=4)
        p = parsed(inventories=[(0, [dict(raw, id=2)], 1000), (2, [raw], 8000),
                                (0, [raw, dict(raw, id=2, wear_state=0)], 3000)])
        snap = C.worn_snapshot(p, database())
        self.assertTrue(snap['known'])
        self.assertEqual(snap['time_ms'], 2000)
        self.assertEqual(len(snap['items']), 1)
        self.assertEqual(snap['items'][0]['cards'], [999999])
        self.assertEqual(snap['items'][0]['options'], [dict(id=7, value=12)])
        self.assertEqual(raw['cards'], [999999])

    def test_storage_only_and_empty_inventory_are_different(self):
        self.assertFalse(C.worn_snapshot(parsed(inventories=[(2, [], 1000)]), database())['known'])
        snap = C.worn_snapshot(parsed(inventories=[(0, [], 1000)]), database())
        self.assertTrue(snap['known'])
        self.assertEqual(snap['items'], [])

    def test_actor_reuse_map_change_and_same_tick_grouping(self):
        p = parsed([
            event(0, E.MONSTER_SPAWN, 10, class_id=100, name='몬스터', max_hp=1000),
            event(50, E.DAMAGE, 1, 10, skill=6513, amount=100, div=10, tick=55),
            event(51, E.DAMAGE, 1, 10, skill=6513, amount=200, div=10, tick=55),
            event(60, E.DAMAGE, 2, 10, skill=6513, amount=999, tick=66),
            event(70, E.MAP_CHANGE),
            event(80, E.DAMAGE, 1, 10, skill=6513, amount=999, tick=88),
            event(90, E.MONSTER_SPAWN, 10, class_id=200, name='다른 몬스터', max_hp=500),
            event(100, E.DAMAGE, 1, 10, skill=6513, amount=400, tick=55),
        ])
        observation = C.damage_observations(p)
        first = C.observed_range(observation, 100, 6513)
        self.assertEqual((first['min'], first['max'], first['count']), (300, 300, 1))
        self.assertEqual(C.observed_range(observation, 200, 6513)['min'], 400)
        self.assertIsNone(C.observed_range(observation, 100, 6514)['min'])

    def test_unverified_is_opt_in_and_misses_do_not_become_minimum_damage(self):
        p = parsed([event(0, E.MONSTER_SPAWN, 10, class_id=100, max_hp=1000),
                    event(50, E.DAMAGE, 1, 10, skill=6513, amount=0),
                    event(60, E.DAMAGE, 1, 10, skill=6513, amount=200, confidence=ESTIMATE)])
        observation = C.damage_observations(p)
        self.assertEqual(C.observed_range(observation, 100, 6513)['count'], 0)
        r = C.observed_range(observation, 100, 6513, include_unverified=True)
        self.assertEqual((r['count'], r['misses'], r['unverified'], r['min']), (1, 1, 1, 200))

    def test_before_snapshot_damage_excluded_and_inconsistent_hp_unknown(self):
        p = parsed([event(0, E.MONSTER_SPAWN, 10, class_id=100, max_hp=1000),
                    event(40, E.DAMAGE, 1, 10, skill=6513, amount=100),
                    event(60, E.DAMAGE, 1, 10, skill=6513, amount=200),
                    event(90, E.MONSTER_SPAWN, 11, class_id=100, max_hp=500)])
        result = C.damage_observations(p, from_ms=50)
        self.assertEqual(C.observed_range(result, 100, 6513)['min'], 200)
        self.assertIsNone(result['targets'][0]['hp'])

    def test_invalid_amount_does_not_create_a_zero_sample(self):
        p = parsed([event(0, E.MONSTER_SPAWN, 10, class_id=100, max_hp=math.inf)] +
                   [event(i + 1, E.DAMAGE, 1, 10, skill=6513, amount=v) for i, v in enumerate((None, -2, math.nan, math.inf, True))])
        result = C.damage_observations(p)
        self.assertEqual(result['samples'], [])
        self.assertIsNone(result['targets'][0]['hp'])

    def test_kill_threshold_exact_edge_and_damage_range(self):
        self.assertEqual(C.hits_to_kill(1000, 500, 1000)['fast'], 1)
        self.assertEqual(C.hits_to_kill(1000, 500, 1000)['safe'], 2)
        self.assertFalse(C.hits_to_kill(1000, 500, 1000)['guaranteed_one'])
        self.assertTrue(C.hits_to_kill(1000, 1000, 1200)['guaranteed_one'])
        self.assertEqual(C.hits_to_kill(1001, 500, 1000)['fast'], 2)
        for values in ((0, 100, 200), (100, 0, 200), (100, 201, 200), (math.inf, 100, 200), (True, 100, 200)):
            self.assertIsNone(C.hits_to_kill(*values))

    def test_description_difference_keeps_conditional_text(self):
        diff = C.description_difference('ATK + 5\n[A등급] 5% 증가', 'ATK + 10\n[A등급] 5% 증가\n세트 조건')
        self.assertEqual(diff['removed'], ['ATK + 5'])
        self.assertEqual(diff['added'], ['ATK + 10', '세트 조건'])

    def test_checked_weapon_thresholds_grade_and_scope_are_explicit(self):
        at10 = C.checked_refinement_summary(560060, 10)
        at12 = C.checked_refinement_summary(560060, 12)
        self.assertIn('ATK 추가 +125, ATK +5%', at10)
        self.assertIn('등급 없음/D 30%, C/B/A 45%', at10)
        self.assertIn('ATK 추가 +150, ATK +6%', at12)
        self.assertIn('등급 없음/D 50%, C/B/A 65%', at12)
        self.assertIn('등급 없음 7, D/C/B 12, A 30', at12)
        self.assertIn('전체 피해 증가율이 아닙니다', at12)
        self.assertEqual(C.checked_refinement_summary(560061, 12), '')
        self.assertEqual(C.checked_refinement_summary(560060, None), '')


if __name__ == '__main__':
    unittest.main()
