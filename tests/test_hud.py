"""HUD 표시 규칙 테스트 (Qt 없이)."""
import unittest

from helpers import ROOT  # noqa: F401  (sys.path 설정)

from live.hud_model import DANGER, OK, WARN, build_rows, visible_items

SNAP = dict(state='SCREEN', status='LIVE', map='prontera', map_source='○', values={},
            session=dict(state='ACTIVE', active=3_600, map='prontera', profit=1_200_000,
                         profit_per_hour=1_200_000, exp_pct_per_hour=2.5, top=[], replay={}, alerts=[]))


def rows_of(snap, settings, buffs=()):
    return {r['label']: r for r in build_rows(snap, settings, buffs)[1]}


class VisibleItemTest(unittest.TestCase):
    def test_preset_and_user_override(self):
        self.assertNotIn('hp', visible_items(dict(hud_preset='hunt')))
        self.assertIn('hp', visible_items(dict(hud_preset='hunt', hud_items={'hp': True})))
        self.assertNotIn('dps', visible_items(dict(hud_preset='hunt', hud_items={'dps': False})))


class ScreenRowTest(unittest.TestCase):
    def test_known_values_only(self):
        rows = rows_of(SNAP, dict(hud_preset='hunt'))
        self.assertEqual(rows['Time']['value'], '1:00:00')
        self.assertEqual(rows['EXP/h']['value'], '+2.50%')
        self.assertEqual(rows['Profit/h']['src'], '○')
        self.assertNotIn('Kill/h', rows)                 # Replay 없으면 Kill/DPS 줄 자체가 없다
        self.assertNotIn('DPS', rows)
        self.assertNotIn('HP', rows)                     # hunt 프리셋에는 상태 줄 없음

    def test_status_colors(self):
        s = dict(hud_preset='status', hud_warn_pct=30, hud_danger_pct=10, alert_weight=85)
        snap = dict(SNAP, values=dict(HP_REGION=8, SP_REGION=25, AP_REGION=100, WEIGHT_REGION=90))
        rows = rows_of(snap, s)
        self.assertEqual(rows['HP']['color'], DANGER)
        self.assertEqual(rows['SP']['color'], WARN)
        self.assertEqual(rows['AP']['color'], OK)
        self.assertEqual(rows['WT']['color'], DANGER)    # 무게는 높을수록 위험

    def test_paused_title(self):
        snap = dict(SNAP, session=dict(SNAP['session'], state='PAUSED'))
        self.assertIn('PAUSED', build_rows(snap, dict(hud_preset='hunt'))[0])

    def test_kill_per_hour_from_replay_stats(self):
        snap = dict(SNAP, session=dict(SNAP['session'], replay=dict(kills=60, dps=1234)))
        rows = rows_of(snap, dict(hud_preset='hunt'))
        self.assertEqual(rows['Kill/h']['value'], '60')
        self.assertEqual(rows['Kill/h']['src'], '●')
        self.assertEqual(rows['DPS']['src'], '●')


class ReplayRowTest(unittest.TestCase):
    def test_replay_branch_keeps_source_marks(self):
        snap = dict(SNAP, replay_live=dict(stage='CONFIRMED', quality='정상', map='pay_dun00', metrics=dict(kills=7),
                                           combat_available=True, combat_dps=5000, rolling_dps={'1': 9000},
                                           resources=dict(HP=dict(value=500, max_value=10_000)),
                                           exp_per_hour=None, profit_per_hour=300_000))
        title, rows = build_rows(snap, dict(hud_preset='detailed', hud_warn_pct=30, hud_danger_pct=10))
        by = {r['label']: r for r in rows}
        self.assertIn('CONFIRMED', title)
        self.assertEqual(by['Kills']['value'], '7')
        self.assertEqual(by['Kills']['src'], '●')
        self.assertEqual(by['HP']['color'], DANGER)      # 500/10,000 = 5%
        self.assertNotIn('EXP/h', by)                    # 값이 없으면 줄을 만들지 않는다


class BuffRowTest(unittest.TestCase):
    def test_buff_rows_appended_with_source_and_color(self):
        buffs = [dict(name='블레싱', remaining=95, state='DANGER', icon='○'),
                 dict(name='버프 #34', remaining=None, state='RUNNING', icon='●')]
        rows = rows_of(SNAP, dict(hud_preset='hunt'), buffs)
        self.assertEqual(rows['블레싱']['value'], '1:35')
        self.assertEqual(rows['블레싱']['color'], DANGER)
        self.assertEqual(rows['버프 #34']['value'], '?')  # 남은 시간을 모르면 '?'
        self.assertEqual(rows['버프 #34']['src'], '●')

    def test_buffs_hidden_when_item_off(self):
        rows = rows_of(SNAP, dict(hud_preset='hunt', hud_items={'buffs': False}),
                       [dict(name='블레싱', remaining=95, state='DANGER', icon='○')])
        self.assertNotIn('블레싱', rows)


if __name__ == '__main__':
    unittest.main()
