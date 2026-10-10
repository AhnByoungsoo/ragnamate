"""오버레이 모양(타입): 설정 창·오른쪽 클릭 메뉴가 같은 값을 저장하고, 모양별로 보이는 것이 맞는지."""
import helpers  # noqa: F401  (sys.path 설정)
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMenu

from ui import overlay_styles as S

APP = QApplication.instance() or QApplication([])


class Settings(dict):
    saved = 0

    def save(self):
        self.saved += 1


def guide():
    step = lambda o, t, **kw: SimpleNamespace(order=o, title=t, description=kw.get('d', ''), npc=kw.get('npc', ''),   # noqa: E731
                                            map=kw.get('map', ''), x=kw.get('x'), y=kw.get('y'), beginner_tip='',
                                            navi_command=kw.get('navi', ''))
    return SimpleNamespace(title='챕터 2 종말의 파편', steps=[
        step(15, '라그스루트의 진짜 모습'), step(16, '그것을 원한다면(3)', npc='일', map='uknw_ruin', x=160, y=160,
                                               d='① 그림자 간수 20마리', navi='/navi uknw_ruin 160/160'),
        step(17, '벨레도르로'), step(18, '모험가, 할 일이 생겼다')])


class StyleStoreTest(unittest.TestCase):
    def test_get_set_legacy_keys_and_fallback(self):
        s = Settings(buff_display='icon', hud_layout='horizontal')
        self.assertEqual((S.get(s, 'buff'), S.get(s, 'hud'), S.get(s, 'quest')), ('icon', 'horizontal', 'card'))
        S.set_style(s, 'quest', 'strip');S.set_style(s, 'hud', 'tiles')
        self.assertEqual((s['overlay_style']['quest'], s['hud_layout']), ('strip', 'tiles'))
        s['overlay_style']['vend'] = '없는 모양';self.assertEqual(S.get(s, 'vend'), 'table')    # 모르는 값은 기본

    def test_menu_marks_current_saves_and_calls_back(self):
        s, called = Settings(), []
        m = QMenu();sub = S.add_menu(m, s, 'storage', lambda: called.append(1))
        acts = sub.actions()
        self.assertEqual([a.text() for a in acts], ['표', '합계 먼저'])
        self.assertTrue(acts[0].isChecked())
        acts[1].trigger()
        self.assertEqual((S.get(s, 'storage'), called, s.saved), ('summary', [1], 1))

    def test_settings_dialog_writes_same_values(self):
        from config.settings import Settings as RealSettings
        from ui.settings_dialog import SettingsDialog
        with tempfile.TemporaryDirectory() as d:
            s = RealSettings(os.path.join(d, 'settings.json'))
            dlg = SettingsDialog(s)
            self.assertEqual(set(dlg.styles), set(S.STYLES))
            box = dlg.styles['quest'];box.setCurrentIndex(box.findData('steps'))
            box = dlg.styles['buff'];box.setCurrentIndex(box.findData('urgent'))
            dlg.apply()
            again = RealSettings(os.path.join(d, 'settings.json'))                   # 다시 켜도 유지
            self.assertEqual((S.get(again, 'quest'), again['buff_display']), ('steps', 'urgent'))
            dlg.close()


class QuestOverlayTest(unittest.TestCase):
    def overlay(self, style):
        from ui.overlay import NavOverlay
        s = Settings(overlay_style={'quest': style})
        ov = NavOverlay(s, lambda m: '브리미르 1층')
        ov.show_step(guide(), 1, {0})
        self.addCleanup(ov.close)
        return ov

    def test_card_steps_strip_visibility(self):
        card = self.overlay('card')
        self.assertFalse(card.cur.isHidden());self.assertTrue(card.steps_label.isHidden());self.assertTrue(card.strip_row.isHidden())
        steps = self.overlay('steps')
        self.assertFalse(steps.steps_label.isHidden());self.assertTrue(steps.cur.isHidden())
        html = steps.steps_label.text()
        self.assertIn('그것을 원한다면(3)', html);self.assertIn('✓', html);self.assertIn('벨레도르로', html)
        strip = self.overlay('strip')
        self.assertFalse(strip.strip_row.isHidden());self.assertTrue(strip.head_row.isHidden());self.assertTrue(strip.btn_row.isHidden())
        self.assertIn('2/4', strip.strip.text());self.assertIn('160,160', strip.strip.text())

    def test_strip_shows_tracker_progress_and_menu_switches_live(self):
        from core.quest_tracker import Objective, TrackedQuest
        ov = self.overlay('strip')
        ov.set_now(TrackedQuest('그것을 원한다면 (3)', [Objective('그림자 간수 20체 파괴', 7, 20)]))
        self.assertIn('7/20', ov.strip.text())
        S.set_style(ov.s, 'quest', 'card');ov.apply_style()                        # 메뉴에서 바꾼 것과 같음
        self.assertTrue(ov.strip_row.isHidden());self.assertFalse(ov.cur.isHidden())


class OtherOverlaysTest(unittest.TestCase):
    def test_buff_urgent_mode(self):
        from ui.buff_overlay import BuffOverlay
        s = Settings(buff_display='urgent', status_warn_sec=30, buff_overlay_under_sec=0, buff_max_rows=0,
                     buff_hide_expired=False, buff_hide_long=False)
        ov = BuffOverlay(s);self.addCleanup(ov.close)
        rows = [dict(key='a', name='임포시티오 마누스', remaining=7, total=60, state='WARN'),
                dict(key='b', name='증가 AGI', remaining=24, total=240, state='OK'),
                dict(key='c', name='블레싱', remaining=200, total=240, state='OK')]
        with patch('ui.buff_overlay.visible_rows', lambda r, s: r):
            ov.update_rows(rows)
        t = ov.body.text()
        self.assertIn('임포시티오 마누스', t);self.assertIn('증가 AGI', t);self.assertNotIn('블레싱', t);self.assertIn('외 1개 정상', t)
        with patch('ui.buff_overlay.visible_rows', lambda r, s: r):
            ov.update_rows(rows[2:])
        self.assertIn('곧 끝나는 버프 없음', ov.body.text())

    def test_hud_tiles_layout(self):
        from ui.live_hud import LiveHud
        s = Settings(hud_layout='tiles', hud_preset='hunt', hud_scale=1.0)
        hud = LiveHud(s);self.addCleanup(hud.close)
        rows = [dict(label='DPS', value='48.2K', color=None, src='패킷'), dict(label='처치/분', value='18.4', color=None, src='패킷'),
                dict(label='EXP/h', value='312M', color='#7cb8ff', src='화면')]
        with patch('ui.live_hud.build_rows', return_value=('LIVE', rows)):
            hud.update_from({})
        t = hud.body.text()
        self.assertEqual(t.count('<tr>'), 2);self.assertIn('48.2K', t);self.assertIn('bgcolor', t)   # 2칸씩 타일

    def test_storage_summary_style(self):
        from ui.storage_overlay import StorageOverlay
        s = Settings(overlay_style={'storage': 'table'}, storage_overlay_prices=['sale'], hud_scale=1.0)
        ov = StorageOverlay(s);self.addCleanup(ov.close)
        rows = [dict(item_id=1, name='스펠 서킷', quantity=1, locations='창고', unit=90000000, average=None, value=90000000,
                     updated_at=None)]
        ov.set_rows(rows);self.assertTrue(ov.total.isHidden());n_table = len(ov.table.model_.cols)
        S.set_style(s, 'storage', 'summary');ov.bind(lambda: ov.set_rows(rows));ov.apply_style()
        self.assertFalse(ov.total.isHidden());self.assertIn('90,000,000', ov.total.text())
        self.assertEqual(len(ov.table.model_.cols), 3);self.assertGreater(n_table, 3)


class VendCardsTest(unittest.TestCase):
    def test_cards_expand_problem_rows_only(self):
        import ui.vend_panel as P
        from core import vend_ocr as V
        from services import gnjoy_market as G
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'data'))
            with patch.object(P, 'BASE_DIR', d), patch.object(P.G, 'shared_client', return_value=G.GnjoyClient(opener=lambda *a: '')):
                panel = P.VendPanel(SimpleNamespace(item_db=None, entries=[], s=Settings(overlay_style={'vend': 'cards'})))
            try:
                data = [dict(row=None, name='스펠 매직 어택 스톤', mine=1755000, low='15,000,000', lowNote='', avg='17,555,555',
                             avgNote='최근 7일', rec=14850000, kind='danger', verdict='너무 쌈 -88%'),
                        dict(row=None, name='제네릭 스톤(하단)', mine=9500000, low='9,900,000', lowNote='', avg='-', avgNote='',
                             rec=9800000, kind='ok', verdict='적정')]
                panel.state = dict(rows=[V.VendRow('a'), V.VendRow('b')], shop='', client=(0, 0, 1, 1), region=(0, 0, 1, 1), hwnd=0)
                with patch.object(panel, '_row_data', side_effect=data):
                    panel._render()
                self.assertTrue(panel.table.isHidden());self.assertFalse(panel.cards_area.isHidden())
                from PySide6.QtWidgets import QPushButton
                buttons = panel.cards.findChildren(QPushButton)
                self.assertEqual(len(buttons), 1)                                     # 적정 줄은 한 줄(복사 버튼 없음)
                buttons[0].click();self.assertEqual(QApplication.clipboard().text(), '14850000')
                self.assertEqual(panel.width(), 420)
            finally:
                panel.store.close();panel.close()


if __name__ == '__main__':
    unittest.main()
