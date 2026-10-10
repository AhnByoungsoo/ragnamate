"""카드·인챈트 이름 대조와 효과 창. 공식 요청은 가짜 opener, DB는 임시 파일만 사용."""
import helpers  # noqa: F401
import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services import gnjoy_market as G
from services.equipment_details import OptionIndex, clean_description, option_summary, spec_text
from services.vend_search import _enrich
from ui.equipment_details import EquipmentOptions, OptionDescriptionDialog
from ui.vend_search_dialog import VendSearchDialog

APP = QApplication.instance() or QApplication([])


def database():
    return SimpleNamespace(items={
        1: dict(name='심연의 기사 카드', description='보스형 적에게 주는 물리 데미지 25% 증가.\n계열 : 카드'),
        2: dict(name='바르문트의 결정(화염) Lv3', description='화속성 효과 Lv3\n[A등급]일 때 추가 효과'),
        3: dict(name='바르문트의 결정(화염) Lv4', description='화속성 효과 Lv4'),
        4: dict(name='중복 카드', description='첫 번째 효과'),
        5: dict(name='중복카드', description='다른 효과'),
        6: dict(name='설명 없는 카드', description=''),
        7: dict(name='비슷한 카드', description='유사 이름을 잘못 선택하면 안 됨')})


class MatchingTest(unittest.TestCase):
    def test_spaces_only_normalized_level_preserved(self):
        index = OptionIndex(database())
        self.assertEqual(index.option('심연의기사카드')['kind'], '카드')
        opt = index.option('바르문트의결정(화염)Lv3')
        self.assertEqual(opt['kind'], '인챈트')
        self.assertEqual([m['item_id'] for m in opt['matches']], [2])
        self.assertEqual(index.find('바르문트의결정(화염)Lv2'), [])
        self.assertEqual(index.find('비슷한'), [])

    def test_duplicate_does_not_choose_effect(self):
        option = OptionIndex(database()).option('중복 카드')
        self.assertEqual(len(option['matches']), 2)
        dialog = OptionDescriptionDialog(None, option)
        self.addCleanup(dialog.close)
        self.assertIn('적용 효과를 확정할 수 없습니다', dialog.body.toPlainText())
        self.assertIn('모두 적용되는 효과가 아닙니다', dialog.body.toPlainText())

    def test_database_can_load_after_dialog_created(self):
        db = SimpleNamespace(items={})
        index = OptionIndex(db)
        self.assertEqual(index.find('심연의 기사 카드'), [])
        db.items = database().items
        self.assertEqual(len(index.find('심연의 기사 카드')), 1)

    def test_unknown_slots_not_zero_and_raw_grade_kept(self):
        row = _enrich(dict(name='[UNIQUE]+12장비[2]'), '129')
        self.assertEqual(spec_text(row), '+12 제련 · 등급 UNIQUE · 슬롯 2')
        self.assertIn('슬롯 미확인', spec_text(_enrich(dict(name='+10쉐도우'), '129')))
        self.assertIn('미조회', option_summary(None))
        self.assertIn('없음', option_summary({'cards': []}))

    def test_color_codes_removed(self):
        self.assertEqual(clean_description('^FF0000효과\n^000000다음'), '효과\n다음')


class WidgetTest(unittest.TestCase):
    def test_click_opens_correct_effect_and_preserves_conditions(self):
        widget = EquipmentOptions(database())
        self.addCleanup(widget.close)
        widget.set_detail(dict(cards=['심연의기사카드', '바르문트의 결정(화염) Lv3'], options=['ATK + 5%']))
        widget.show()
        APP.processEvents()
        QTest.mouseClick(widget.buttons[1], Qt.LeftButton)
        dialog = widget.description_dialog
        self.addCleanup(dialog.close)
        self.assertIn('[A등급]일 때 추가 효과', dialog.body.toPlainText())
        self.assertNotIn('Lv4', dialog.body.toPlainText())
        self.assertFalse(dialog.body.openExternalLinks())

    def test_unknown_missing_description_and_html_are_safe(self):
        for name, expected in [('새 인챈트', '효과 미확인'), ('설명 없는 카드', '저장된 효과 설명 없음')]:
            dialog = OptionDescriptionDialog(None, OptionIndex(database()).option(name))
            self.addCleanup(dialog.close)
            self.assertIn(expected, dialog.body.toPlainText())
        dialog = OptionDescriptionDialog(None, dict(name='<img src="https://invalid/x">', matches=[
            dict(item_id=9, name='<b>이름</b>', description='<a href="https://invalid">설명</a>')]))
        self.addCleanup(dialog.close)
        self.assertIn('<a href=', dialog.body.toPlainText())
        self.assertIn('<img src=', dialog.body.toPlainText())

    def test_official_detail_cache_selection_and_click_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = G.VendingStore(os.path.join(tmp, 'test.db'))
            calls = []
            def opener(url, referer):
                calls.append(url)
                return '<th>슬롯정보</th><td><ul><li>심연의 기사 카드</li><li>바르문트의 결정(화염) Lv3</li></ul></td>'
            dialog = VendSearchDialog(None, {}, database(), store, G.GnjoyClient(interval=0, opener=opener))
            self.addCleanup(dialog.close)
            row = _enrich(dict(name='[UNIQUE]+12장비[2]', item_id=100, server='바포메트', kind='sale',
                               price=240000000, qty=1, shop='테스트', ssi='1'), '129')
            dialog.rows = [row]
            dialog.table.set_rows([row])
            dialog.table.selectRow(0)
            detail = dialog.engine.detail(store, row)
            dialog._done(('detail', row, detail))
            self.assertIs(dialog.current, row)
            self.assertEqual(len(dialog.equipment_options.buttons), 2)
            self.assertIn('심연의 기사 카드', dialog.table.model_.data(dialog.table.model_.index(0, 8)))
            dialog.equipment_options.buttons[0].click()
            self.addCleanup(dialog.equipment_options.description_dialog.close)
            self.assertIn('25%', dialog.equipment_options.description_dialog.body.toPlainText())
            dialog.engine.detail(store, row)
            self.assertEqual(len(calls), 1)
            # 늦게 끝난 다른 매물의 상세가 선택한 매물을 덮어쓰지 않아야 한다.
            other = dict(row, ssi='2', name='다른 장비')
            dialog._done(('detail', other, {'cards': ['새 인챈트']}))
            self.assertIn('심연의 기사 카드', dialog.equipment_options.buttons[0].text())
            dialog.table.clearSelection()
            self.assertEqual(dialog.equipment_options.buttons, [])
            dialog.close()
            store.close()


if __name__ == '__main__':
    unittest.main()
