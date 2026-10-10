"""Buff Timer 테스트 (게임/Qt 없이 논리만)."""
import os
import unittest

from helpers import ROOT  # noqa: F401  (sys.path 설정)

from live.buffs import (DANGER, EXPIRED, MANUAL, REPLAY, RUNNING, BuffDef, BuffTimerManager, replay_rows,
                        visible_rows)


class FakeSettings(dict):
    saved = 0

    def save(self):
        self.saved += 1


def manager(**over):
    s = FakeSettings(buff_timers=[], buff_keep_expired=False, buff_warn_pct=30, buff_danger_pct=10)
    s.update(over)
    fired = []
    m = BuffTimerManager(s, alert=lambda k, msg, snd: fired.append((k, msg, snd)), clock=lambda: 0)
    m.fired = fired
    return m


class DefTest(unittest.TestCase):
    def test_add_replace_remove_persists(self):
        m = manager()
        m.add_def(BuffDef('블레싱', 240, 10))
        m.add_def(BuffDef('블레싱', 300, 20))            # 같은 이름은 교체
        self.assertEqual([d.duration for d in m.defs], [300])
        self.assertEqual(m.s['buff_timers'][0]['alert_sec'], 20)
        m.add_def(BuffDef('속도증가', 600))
        m.remove_def('블레싱')
        self.assertEqual([d.name for d in m.defs], ['속도증가'])

    def test_unknown_key_does_not_start(self):
        m = manager()
        self.assertIsNone(m.start('없는버프'))


class HotkeyDefaultTest(unittest.TestCase):
    def test_old_settings_file_gets_new_hotkeys(self):
        """예전 설정 파일(hud/compact/nav만 있음)에도 새 단축키가 채워져야 등록된다."""
        import json
        import tempfile
        from config.settings import DEFAULTS, Settings
        path = os.path.join(tempfile.mkdtemp(), 'settings.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'hotkeys': {'hud': 'Ctrl+Alt+F1'}}, f)
        s = Settings(path)
        self.assertEqual(s['hotkeys']['hud'], 'Ctrl+Alt+F1')        # 사용자가 바꾼 값은 유지
        self.assertEqual(s['hotkeys']['buff1'], DEFAULTS['hotkeys']['buff1'])
        self.assertIn('storage', s['hotkeys'])


class TimerTest(unittest.TestCase):
    def test_start_refresh_and_remaining(self):
        m = manager()
        m.add_def(BuffDef('블레싱', 100, alert_sec=10))
        t = m.start('블레싱', now=0)
        self.assertEqual(t.remaining(30), 70)
        m.start('블레싱', now=50)                        # 다시 걸기
        self.assertEqual(m.timers['블레싱'].remaining(50), 100)
        self.assertEqual(len(m.timers), 1)

    def test_warn_then_expire_alerts_once(self):
        m = manager()
        m.add_def(BuffDef('블레싱', 100, alert_sec=10))
        m.start('블레싱', now=0)
        self.assertEqual(m.tick(80), [])
        self.assertEqual(len(m.tick(92)), 1)             # 10초 전 알림
        self.assertEqual(m.tick(95), [])                 # 같은 알림 반복 없음
        fired = m.tick(101)
        self.assertEqual(len(fired), 1)
        self.assertIn('종료', fired[0][1])
        self.assertNotIn('블레싱', m.timers)             # keep_expired=False면 목록에서 제거
        self.assertEqual([f[0] for f in m.fired], ['buff_블레싱', 'buff_블레싱'])

    def test_keep_expired_keeps_row(self):
        m = manager(buff_keep_expired=True)
        m.add_def(BuffDef('블레싱', 10, alert_sec=0))
        m.start('블레싱', now=0)
        m.tick(11)
        self.assertEqual(m.rows(11)[0]['state'], EXPIRED)
        self.assertEqual(m.rows(11)[0]['remaining'], 0)
        self.assertEqual(m.tick(20), [])                 # 종료 알림은 한 번만

    def test_pause_holds_remaining(self):
        m = manager()
        m.add_def(BuffDef('블레싱', 100, alert_sec=5))
        m.start('블레싱', now=0)
        m.set_paused(True, now=40)
        self.assertEqual(m.rows(999)[0]['remaining'], 60)
        self.assertEqual(m.tick(999), [])                # 일시정지 중에는 알리지 않는다
        m.set_paused(False, now=1000)
        self.assertEqual(m.timers['블레싱'].remaining(1000), 60)
        self.assertEqual(m.timers['블레싱'].total(), 100)

    def test_row_states_by_ratio(self):
        m = manager()
        m.add_def(BuffDef('블레싱', 100, alert_sec=0))
        m.start('블레싱', now=0)
        self.assertEqual(m.rows(10)[0]['state'], RUNNING)
        self.assertEqual(m.rows(75)[0]['state'], 'WARN')
        self.assertEqual(m.rows(95)[0]['state'], DANGER)
        self.assertEqual(m.rows(10)[0]['source'], MANUAL)


class VisibleRowsTest(unittest.TestCase):
    """화면에 내보낼 줄 고르기 — 실제 화면에서 만료 0:00과 400시간 짜리가 잔뜩 보이던 문제."""

    ROWS = [dict(name='만료', remaining=0, state=EXPIRED), dict(name='상시', remaining=1_440_000, state=RUNNING),
            dict(name='짧은', remaining=30, state=DANGER), dict(name='보통', remaining=600, state=RUNNING),
            dict(name='모름', remaining=None, state=RUNNING)]

    def test_hidden_by_default_and_sorted(self):
        rows = visible_rows(self.ROWS, {})
        self.assertEqual([r['name'] for r in rows], ['짧은', '보통', '모름'])

    def test_can_show_everything(self):
        rows = visible_rows(self.ROWS, dict(buff_hide_expired=False, buff_hide_long=False))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]['name'], '만료')

    def test_max_rows(self):
        self.assertEqual(len(visible_rows(self.ROWS, dict(buff_max_rows=2))), 2)
        self.assertEqual(len(visible_rows(self.ROWS, dict(buff_max_rows=0))), 3)


class ReplayBuffTest(unittest.TestCase):
    def test_remaining_from_expected_end(self):
        snap = dict(duration_ms=10_000, state='ACTIVE',
                    buffs=[dict(buff_id=34, expected_end=70_000, refreshed_at=10_000, ending=None),
                           dict(buff_id=99, expected_end=None, ending=None)])
        rows = replay_rows(snap, name_fn=lambda i: '버프%s' % i)
        self.assertEqual(rows[0]['remaining'], 60)
        self.assertEqual(rows[0]['source'], REPLAY)
        self.assertEqual(rows[0]['icon'], '●')
        self.assertIsNone(rows[1]['remaining'])          # remain time이 없으면 숫자를 만들지 않는다
        self.assertIn('미수신', rows[1]['note'])

    def test_real_recording_edge_cases(self):
        """실제 녹화(123.rrf)에서 나온 두 경우: 예상 만료 시각이 지난 Buff, 24시간 넘는 remain time."""
        snap = dict(duration_ms=24_631, state='ACTIVE',
                    buffs=[dict(buff_id=738, expected_end=12_000, ending='ESTIMATED'),
                           dict(buff_id=942, expected_end=596_132_000, ending=None),
                           dict(buff_id=993, expected_end=430_631, ending=None)])
        rows = replay_rows(snap)
        self.assertEqual(rows[0]['remaining'], 0)
        self.assertIn('종료 패킷 미수신', rows[0]['note'])
        self.assertIn('24시간 이상', rows[1]['note'])
        self.assertEqual(rows[2]['note'], '패킷 remain time 기준 예상 만료')
        self.assertEqual(round(rows[2]['remaining']), 406)

    def test_new_flag_and_efst(self):
        """방금 걸린 Buff는 '새로 걸림'으로 표시해 어떤 스킬인지 사용자가 짚을 수 있게 한다."""
        snap = dict(duration_ms=100_000, state='ACTIVE',
                    buffs=[dict(buff_id=993, refreshed_at=95_000, expected_end=200_000),
                           dict(buff_id=994, refreshed_at=10_000, expected_end=200_000)])
        rows = replay_rows(snap)
        self.assertEqual(rows[0]['efst'], 993)
        self.assertTrue(rows[0]['new'])
        self.assertFalse(rows[1]['new'])

    def test_stalled_replay_marked(self):
        snap = dict(duration_ms=0, state='STALLED', buffs=[dict(buff_id=1, expected_end=5_000)])
        self.assertIn('정체', replay_rows(snap)[0]['note'])

    def test_no_snapshot_no_rows(self):
        self.assertEqual(replay_rows(None), [])


if __name__ == '__main__':
    unittest.main()


class OverlayUnderSecTest(unittest.TestCase):
    """오버레이는 남은 시간 N초 이하 + '재사용!!'만 (게임 화면을 덜 가리게)."""

    def test_only_short_and_reuse(self):
        from live.buffs import visible_rows
        rows = [dict(name='음식', remaining=1790), dict(name='블레싱', remaining=25), dict(name='모름', remaining=None),
                dict(name='끝남', remaining=40, state='EXPIRED_ALERT'), dict(name='곧', remaining=5)]
        names = [r['name'] for r in visible_rows(rows, {'buff_overlay_under_sec': 30})]
        self.assertEqual(names, ['곧', '블레싱', '끝남'])
        self.assertEqual(len(visible_rows(rows, {'buff_overlay_under_sec': 0})), 5)        # 0 = 전부