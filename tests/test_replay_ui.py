"""Optional Qt smoke regression. Uses temporary DB/settings, no game client."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from helpers import FakeGameData
from test_replay_incremental import synthetic

HAS_QT = importlib.util.find_spec('PySide6') is not None


@unittest.skipUnless(HAS_QT, 'PySide6 is not installed in this test runtime')
class ReplayUITests(unittest.TestCase):
    def test_dashboard_diagnostics_and_settings(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from config.settings import Settings
        from ui.main_window import MainWindow
        from ui.settings_dialog import SettingsDialog
        from live.replay_engine import ReplayLiveSession

        app = QApplication.instance() or QApplication([])
        class QuietWindow(MainWindow):
            def startup(self):
                pass

        with tempfile.TemporaryDirectory() as d:
            settings = Settings(str(Path(d) / 'settings.json'))
            settings.update(db_path=str(Path(d) / 'test.db'), live_enabled=False, developer_mode=True,
                            experimental_packets=True)
            win = QuietWindow(settings)
            path = Path(d) / 'replay.rrf'
            path.write_bytes(synthetic())
            live = ReplayLiveSession(path, settings, FakeGameData())
            live.step(1)
            live.step(2)
            live.publish(3, force=True)
            snap = live.published.to_dict()
            win.live_page._refresh_replay(snap)
            self.assertEqual(win.live_page.kpi['kills'].value.text(), '1')
            self.assertIn('600', win.live_page.kpi['dps'].value.text())
            self.assertIn('packets_decoded', win.live_page.replay_diag.toPlainText())
            self.assertIn('Cast 1', win.live_page.replay_details.toPlainText())
            old = dict(snap, revision=snap['revision'] - 1, metrics=dict(snap['metrics'], kills=999))
            win.live_page._refresh_replay(old)
            self.assertEqual(win.live_page.kpi['kills'].value.text(), '1')
            self._buff_timers(win, snap)
            self._buff_naming(win, snap)
            self._navigator_buttons(win)
            self._overlays(win)
            dialog = SettingsDialog(settings)
            dialog.replay_timing['live_reorder_ms'].setValue(400)
            dialog.apply()
            self.assertEqual(settings['live_reorder_ms'], 400)
            dialog.close()
            win.live_page.timer.stop()
            win.buff_timer.stop()
            win.db.conn.close()
            win.deleteLater()
            app.processEvents()

    def _buff_timers(self, win, snap):
        """수동 타이머 + Replay Buff가 패널/오버레이에 함께 나오는지."""
        from live.buffs import BuffDef
        win.live_page._refresh_replay(dict(snap, revision=snap['revision'] + 1, duration_ms=1_000,
            buffs=[dict(buff_id=34, started_at=0, refreshed_at=0, expected_end=61_000, active=True,
                        confidence='CONFIRMED', ending=None)]))
        win.s['buff_overlay_under_sec'] = 0             # 오버레이에 10분 타이머도 보이게(기본은 30초 이하만)
        win.buffs.add_def(BuffDef('테스트버프', 600, 10))
        win.live_page.buff_panel.refresh_defs()
        win.buffs.start('테스트버프')
        win.toggle_buff_overlay()
        win.refresh_buffs()
        panel = win.live_page.buff_panel
        names = [r['name'] for r in panel.running.model_.rows]
        self.assertIn('테스트버프', names)
        replay = [r for r in panel.running.model_.rows if r['origin'] == 'REPLAY']
        self.assertEqual(len(replay), 1)                                   # Replay Buff도 같은 목록에
        self.assertEqual(replay[0]['remaining'], 60)
        self.assertIn('테스트버프', win.buff_overlay.body.text())
        self._buff_hotkeys(win)
        win.pause_buffs(True)
        self.assertTrue(win.buffs.paused)
        self.assertIn('일시정지', win.buff_overlay.title.text())
        win.pause_buffs(False)
        win.buffs.stop_all()
        win.refresh_buffs()
        stopped = next(r for r in panel.running.model_.rows if r['name'] == '테스트버프')
        self.assertEqual(stopped['state'], 'EXPIRED_ALERT')      # 사라지지 않고 재사용 알림으로
        self.assertEqual(stopped['label'], '재사용!!')
        win.buff_overlay.close()

    def _buff_hotkeys(self, win):
        """Ctrl+Alt+1~4 = 목록 1~4번 타이머 시작/갱신, ▲▼로 순서를 바꾸면 번호도 따라간다."""
        from live.buffs import BuffDef
        win.buffs.add_def(BuffDef('두번째버프', 120, 5))
        panel = win.live_page.buff_panel
        panel.refresh_defs()
        self.assertEqual([panel._hotkey_label(d) for d in win.buffs.defs], ['Ctrl+Alt+1', 'Ctrl+Alt+2'])
        win.buffs.stop_all()
        win._hotkey('buff2')
        self.assertEqual(list(win.buffs.timers), ['두번째버프'])
        panel.defs.selectRow(1)
        panel.move(-1)                                        # ▲ : 2번 → 1번
        self.assertEqual([d.name for d in win.buffs.defs], ['두번째버프', '테스트버프'])
        win.buffs.stop_all()
        win._hotkey('buff1')
        self.assertEqual(list(win.buffs.timers), ['두번째버프'])
        win._hotkey('buff4')                                  # 없는 번호는 아무 일도 없어야 한다
        self.assertEqual(list(win.buffs.timers), ['두번째버프'])
        win.buffs.remove_def('두번째버프')
        panel.refresh_defs()
        win.buffs.stop_all()
        win.buffs.start('테스트버프')

    def _buff_naming(self, win, snap):
        """이름 없는 Replay Buff(#993)에 이름·아이콘을 붙이고, 오버레이 표시 방식을 바꾼다."""
        import tempfile
        from PySide6.QtGui import QPixmap
        from analyzer.gamedata import _NameFile
        original = win.analysis.gamedata.buffs          # 프로젝트 이름 파일은 건드리지 않는다
        win.analysis.gamedata.buffs = _NameFile(os.path.join(tempfile.mkdtemp(), 'buff_names.json'), '버프')
        win.live_page._refresh_replay(dict(snap, revision=snap['revision'] + 5, duration_ms=1_000,
            buffs=[dict(buff_id=993, started_at=0, refreshed_at=500, expected_end=601_000, active=True,
                        confidence='CONFIRMED', ending=None)]))
        row = next(r for r in win._buff_rows() if r.get('efst') == 993)
        self.assertTrue(row['name'].startswith('알 수 없는 효과'))   # 내부 Symbol을 노출하지 않는다
        self.assertTrue(row['new'])                       # 방금 걸린 것으로 표시
        win.analysis.gamedata.buffs.set(993, '어깨동무')
        win.refresh_buffs()
        row = next(r for r in win._buff_rows() if r.get('efst') == 993)
        self.assertEqual(row['name'], '어깨동무')
        icon = os.path.join(tempfile.mkdtemp(), 'icon.png')
        QPixmap(8, 8).save(icon)
        win.buff_icons.set(row['key'], icon)
        win.s['buff_display'] = 'icon'
        win.toggle_buff_overlay()
        win.refresh_buffs()
        self.assertIn('<img', win.buff_overlay.body.text())
        self.assertIn('어깨동무', win.buff_overlay.body.text())
        win.s['buff_display'] = 'compact'
        win.refresh_buffs()
        self.assertNotIn('<img', win.buff_overlay.body.text())
        win.buff_overlay.close()
        win.s['buff_display'] = 'list'
        self.assertEqual(win.analysis.gamedata.buffs.origin(993), 'user')
        win.analysis.gamedata.buffs = original

    def _navigator_buttons(self, win):
        """LIVE → 네비게이션 연결과 주요 버튼이 실제로 동작하는지."""
        from PySide6.QtWidgets import QPushButton
        nav = win.navigator_page
        win.live_page.open_navigator()
        self.assertIs(win.main_tabs.currentWidget(), nav)
        nav.apply_live_context(dict(map='prontera', character='테스터', job_id=4262, level=250))
        self.assertIn('프론테라', nav.live_location.text())       # 내부 맵 ID가 아니라 한국어
        self.assertTrue(nav.live_guide_button.isEnabled())
        nav.search.setText('전사자의 무덤')
        nav.run_search()
        self.assertGreater(nav.results.count(), 0)
        nav.open_guide('dungeon_tomb_of_the_fallen')
        self.assertIsNotNone(nav.detail)
        labels = {b.text().strip(): b for b in nav.findChildren(QPushButton) if b.text().strip()}
        for label in ('▶ 시작하기', '☆ 즐겨찾기', '입장이 안 돼요', '📋 네비 복사', '다음 ▶', '◀ 이전'):
            match = next((b for t, b in labels.items() if t.startswith(label[:4])), None)
            self.assertIsNotNone(match, label)
            if not match.isEnabled():                         # 비활성이면 이유를 반드시 알려 준다
                self.assertTrue(match.toolTip(), label)

    def _overlays(self, win):
        """HUD(버프 줄 포함)와 창고 시세 Overlay가 실제 값으로 그려지는지."""
        from core.models import ItemEntry, PriceInfo
        from live.buffs import BuffDef
        win.buffs.add_def(BuffDef('HUD버프', 300, 10))
        win.buffs.start('hud버프')
        win.toggle_hud()
        win.update_hud(dict(state='SCREEN', status='LIVE', map='prontera', map_source='○',
                            values=dict(HP_REGION=5.0), window=None,
                            session=dict(state='ACTIVE', active=120, profit=50_000, profit_per_hour=1_500_000,
                                         exp_pct_per_hour=1.5, top=[], replay={}, alerts=[])))
        body = win.hud.body.text()
        self.assertIn('HUD버프', body)
        self.assertIn('EXP/h', body)
        self.assertNotIn('Kill/h', body)                      # Replay 없으면 Kill 줄 없음
        win.hud.hide()
        win.buffs.stop_all()

        win.entries = [ItemEntry('창고1', 984, '오리데오콘', 20), ItemEntry('카트', 501, '빨간포션', 3)]
        win.prices = {984: PriceInfo(984, 7000, 8000, 9000, 3, 0)}
        win._rebuild()
        win.toggle_storage_overlay()
        names = [r['name'] for r in win.storage_overlay.table.model_.rows]
        self.assertEqual(names, ['오리데오콘'])               # 시세 없는 포션은 '팔 만한 것만'에서 빠짐
        win.storage_overlay.only.setChecked(False)
        self.assertEqual(len(win.storage_overlay.table.model_.rows), 2)
        self.assertIsNone(win.storage_overlay.table.model_.rows[1]['value'])
        win.storage_overlay.search.setText('없는아이템')
        self.assertEqual(win.storage_overlay.table.model_.rows, [])
        win.storage_overlay.close()


if __name__ == '__main__':
    unittest.main()
