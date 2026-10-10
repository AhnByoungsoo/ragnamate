"""장비 원문 자동 표시, 리플레이 연결, 미검증·수동 계산값의 구분을 검사한다."""
import helpers  # noqa: F401
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QWidget
from analyzer.events import ESTIMATE, EventType as E
from services.vend_search import _enrich
from services.gnjoy_market import GnjoyClient, VendingStore
from ui.gear_comparison_dialog import GearComparisonDialog
from ui.vend_result_cards import VendResultCards
from ui.vend_search_dialog import VendSearchDialog
from test_gear_comparison import event, parsed
from test_vend_search import Settings

APP = QApplication.instance() or QApplication([])


class FakeApp(QWidget):
    def __init__(self, with_replay=True):
        super().__init__()
        self.item_db = SimpleNamespace(items={
            100: dict(name='시험 너클', description='ATK + 30.\n3제련 당 멸화격 데미지 5% 증가.\n[A등급] P.ATK + 3.\n함께 장착 시 세트 효과.'),
            200: dict(name='확인 카드', description='보스형 대상 물리 데미지 25% 증가.\n계열 : 카드')})
        equip = dict(id=100, equip=True, wear_state=2, refine=10, grade=4, cards=[200, 99999], options=[], index=5)
        pr = parsed([event(0, E.MONSTER_SPAWN, 10, class_id=22371, name='잠식당한 탄', max_hp=1000),
                     event(200, E.DAMAGE, 1, 10, skill=6513, amount=200, tick=1, confidence=ESTIMATE)],
                    inventories=[(0, [equip, dict(equip, wear_state=0)], 1000)])
        self.replay_page = SimpleNamespace(result=SimpleNamespace(parsed=pr) if with_replay else None)
        skills = SimpleNamespace(names={6513: '제3격 : 멸화격'}, name=lambda sid: '제3격 : 멸화격')
        self.analysis = SimpleNamespace(gamedata=SimpleNamespace(skills=skills))


def candidate():
    return _enrich(dict(item_id=100, name='[UNIQUE]+12시험 너클[2]', price=240000000, qty=1, kind='sale',
                        shop='예시 상점', server='바포메트', _detail=dict(cards=['확인 카드'], options=[])), '129')


class ComparisonUiTest(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.addCleanup(self.app.close)
        self.dialog = GearComparisonDialog(self.app, candidate())
        self.addCleanup(self.dialog.close)

    def test_card_automatically_contains_full_base_options_without_click(self):
        cards = VendResultCards(self.app.item_db, lambda code: code, lambda *args: None)
        self.addCleanup(cards.close)
        cards.set_rows([candidate()])
        body = cards.cards[0].base_options.toPlainText()
        self.assertIn('3제련 당 멸화격', body)
        self.assertIn('[A등급] P.ATK', body)
        self.assertIn('함께 장착 시', body)
        self.assertFalse(cards.cards[0].base_options.openExternalLinks())

    def test_current_worn_only_and_candidate_effects_are_both_visible(self):
        d = self.dialog
        self.assertEqual(d.worn.count(), 2)  # 안내 항목 + 실제 착용 1개
        self.assertEqual(d.worn.currentData()['refine'], 10)
        self.assertIn('99999', d.current_options.toPlainText())
        self.assertIn('보스형 대상', d.candidate_options.toPlainText())
        self.assertIn('원문 옵션은 같음', d.diff.toPlainText())
        self.assertIn('자동 피해 계산 미지원', d.result.text())

    def test_unverified_opt_in_and_manual_after_damage_sources_are_explicit(self):
        d = self.dialog
        self.assertIn('유효 피해 기록이 없습니다', d.result.text())
        d.allow_unverified.setChecked(True)
        self.assertIn('5~5회', d.result.text())
        self.assertIn('미검증 포함 1개', d.result.text())
        d.manual_after.setChecked(True)
        d.after_min.setValue(600)
        d.after_max.setValue(700)
        self.assertIn('교체 후 · 외부 계산값 직접 입력', d.result.text())
        self.assertIn('2~2회', d.result.text())

    def test_custom_target_or_skill_cannot_reuse_other_observation(self):
        d = self.dialog
        d.allow_unverified.setChecked(True)
        self.assertIn('5~5회', d.result.text())
        d.monster.setEditText('다른 몬스터')
        self.assertEqual(d.hp.value(), 0)
        self.assertIn('유효 피해 기록이 없습니다', d.result.text())
        d.monster.setCurrentIndex(0)
        d.skill.setEditText('다른 스킬')
        self.assertIn('유효 피해 기록이 없습니다', d.result.text())

    def test_manual_damage_range_and_hp_threshold_include_source(self):
        d = self.dialog
        d.hp.setValue(3000)
        d.manual_current.setChecked(True)
        d.current_min.setValue(1000)
        d.current_max.setValue(1500)
        self.assertIn('직접 입력', d.hp_source.text())
        self.assertIn('현재 · 직접 입력', d.result.text())
        self.assertIn('2~3회', d.result.text())
        d.current_min.setValue(2000)  # 최소 > 최대일 때 결과 생성 금지
        self.assertIn('최소 ≤ 최대', d.result.text())

    def test_missing_replay_and_export_do_not_claim_automatic_simulation(self):
        empty = FakeApp(with_replay=False)
        self.addCleanup(empty.close)
        d = GearComparisonDialog(empty, candidate())
        self.addCleanup(d.close)
        self.assertIn('착용 상태가 확인된', d.current_options.toPlainText())
        d.copy_inputs()
        data = json.loads(QApplication.clipboard().text())
        self.assertFalse(data['automatic_swap_damage_supported'])
        self.assertEqual(data['worn'], [])
        self.assertEqual(data['candidate']['item_id'], 100)

    def test_listing_comparison_uses_replay_and_refreshes_late_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VendingStore(os.path.join(tmp, 'v.db'))
            calls = []
            client = GnjoyClient(interval=0, opener=lambda *args: calls.append(args))
            search = VendSearchDialog(self.app, Settings(), self.app.item_db, store, client)
            item = candidate()
            item.pop('_detail')
            search.rows = [item]
            search.table.set_rows([item])
            search.cards.set_rows([item])
            try:
                search._card_action(item, 'compare')
                d = search.comparison_dialog
                self.assertEqual(d.worn.currentData()['refine'], 10)
                self.assertIn('자동 조회 대기', d.candidate_options.toPlainText())
                search._display_detail(item, dict(cards=['확인 카드'], options=[]))
                self.assertIn('보스형 대상 물리 데미지 25%', d.candidate_options.toPlainText())
                self.assertNotIn('자동 조회 대기', d.candidate_options.toPlainText())
                self.assertEqual(calls, [])
                d.close()
            finally:
                search.close()
                search.task.close()
                search.auto_task.close()
                store.close()


if __name__ == '__main__':
    unittest.main()
