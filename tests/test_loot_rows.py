"""획득 아이템 표: 노점가·상점가(오버차지)·팔 곳·가치, 아이콘 자리, 상점 기준가 색인."""
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401  (sys.path 설정)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import loot_rows as LR  # noqa: E402
from tools import build_item_prices as B  # noqa: E402

APP = QApplication.instance() or QApplication([])
CARD, SCARF, ORE = 4504, 7216, 25731
NAMES = {CARD: '봉인된 에드가 카드', SCARF: '빨간 머플러', ORE: '제르늄'}


def vend(item_id):
    return {CARD: (27_720_000, '지금 판매 최저가'), SCARF: (300, '최근 7일 거래 평균')}.get(item_id, (None, ''))


class NpcPriceTest(unittest.TestCase):
    def prices(self, sell):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, 'p.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(dict(built_at='2026-10-10', sell={str(k): v for k, v in sell.items()}), f)
        return LR.NpcPrices(path)

    def test_overcharge_is_applied_and_floored(self):
        p = self.prices({SCARF: 330, CARD: 10, ORE: 0})
        self.assertEqual(p.base(SCARF), 330)
        self.assertEqual(p.sell(SCARF), 409)                     # 330 × 1.24 = 409.2 → 내림
        self.assertEqual(p.sell(SCARF, 0), 330)                  # 오버차지 없음
        self.assertEqual(p.sell(SCARF, 7), 353)                  # Lv1
        self.assertEqual(p.sell(CARD), 12)

    def test_unknown_or_zero_is_none_not_zero(self):
        p = self.prices({ORE: 0})
        self.assertIsNone(p.sell(ORE))                           # 기준가 0 = 값을 매길 수 없음
        self.assertIsNone(p.sell(999_999))
        self.assertIsNone(LR.NpcPrices(os.path.join('없는', '파일.json')).sell(SCARF))   # 파일이 없어도 죽지 않는다

    def test_index_built_from_rathena_yml(self):
        yml = ['  - Id: 7216', '    AegisName: Red_Scarf', '    Buy: 660',
               '  - Id: 501', '    Buy: 10', '    Sell: 3',              # Sell 이 적혀 있으면 그 값
               '  - Id: 25731', '    AegisName: Zelunium']               # 값이 없으면 색인에 넣지 않는다
        self.assertEqual(B.parse(yml), {7216: 330, 501: 3})

    def test_bundled_index_has_todays_loot(self):
        p = LR.default_npc_prices(helpers.ROOT)
        if not os.path.isfile(p.path):
            self.skipTest('item_prices.json 없음')
        self.assertEqual(p.base(7216), 330)                      # 빨간 머플러: rAthena Buy 660 → 330
        self.assertTrue(p.built_at)


class RowTest(unittest.TestCase):
    def rows(self, npc=None):
        return LR.build({SCARF: 7, CARD: 1, ORE: 2}, NAMES.get, vend, (npc or {SCARF: 409, CARD: 12}).get)

    def test_better_side_wins_per_item(self):
        rows = {r['item_id']: r for r in self.rows()}
        self.assertEqual((rows[CARD]['sell_at'], rows[CARD]['unit'], rows[CARD]['value']), (LR.VEND, 27_720_000, 27_720_000))
        self.assertEqual((rows[SCARF]['sell_at'], rows[SCARF]['unit'], rows[SCARF]['value']), (LR.NPC, 409, 2863))
        self.assertEqual((rows[SCARF]['vend_value'], rows[SCARF]['npc_value']), (2100, 2863))
        self.assertEqual((rows[ORE]['sell_at'], rows[ORE]['value']), ('', None))       # 둘 다 모름 → 0 아님

    def test_sorted_by_value_and_totals_skip_unknowns(self):
        rows = self.rows()
        self.assertEqual([r['item_id'] for r in rows], [CARD, SCARF, ORE])
        t = LR.totals(rows)
        self.assertEqual((t['count'], t['quantity'], t['unpriced'], t['no_vend']), (3, 10, 1, 1))
        self.assertEqual((t['vend'], t['npc'], t['best']), (27_722_100, 2875, 27_722_863))

    def test_totals_are_none_when_nothing_is_known(self):
        t = LR.totals(LR.build({ORE: 2}, NAMES.get))
        self.assertEqual((t['vend'], t['npc'], t['best'], t['unpriced']), (None, None, None, 1))
        self.assertEqual(LR.totals([])['best'], None)


class TableTest(unittest.TestCase):
    def table(self, **kw):
        from ui.loot_table import LootTable
        t = LootTable({'rare_item_value': 1_000_000}, **kw)
        t.set_rows(LR.build({SCARF: 7, CARD: 1, ORE: 2}, NAMES.get, vend, {SCARF: 409, CARD: 12}.get,
                            extra={SCARF: dict(last=61_000)}))
        return t

    def cells(self, t, row):
        return [t.proxy.index(row, c).data() for c in range(t.model_.columnCount())]

    def test_columns_show_both_prices_and_where_to_sell(self):
        t = self.table()
        self.assertEqual([c.title for c in t.model_.cols], ['아이템', '수량', '노점가 ○', '상점가 △', '팔 곳', '가치'])
        self.assertEqual(self.cells(t, 0), ['★ 봉인된 에드가 카드', '1', '27,720,000', '12', '노점', '27,720,000'])
        self.assertEqual(self.cells(t, 1), ['빨간 머플러', '7', '300', '409', '상점', '2,863'])
        self.assertEqual(self.cells(t, 2), ['제르늄', '2', '-', '-', '', '-'])
        self.assertNotIn('번호', [c.title for c in t.model_.cols])       # 아이템 번호는 풍선말에만
        self.assertIn(str(CARD), t.proxy.index(0, 0).data(Qt.ToolTipRole))

    def test_winning_price_is_coloured_and_rare_is_gold(self):
        from ui.loot_table import BLUE, GOLD, GREEN
        t = self.table()
        colour = lambda r, c: t.proxy.index(r, c).data(Qt.ForegroundRole)                # noqa: E731
        self.assertEqual((colour(0, 2), colour(0, 3)), (BLUE, None))      # 카드: 노점가 쪽에 색
        self.assertEqual((colour(1, 2), colour(1, 3)), (None, GREEN))     # 머플러: 상점가 쪽에 색
        self.assertEqual((colour(0, 0), colour(1, 0)), (GOLD, None))      # 100만 이상은 ★ 금색

    def test_value_bar_scales_to_the_biggest_row(self):
        t = self.table()
        self.assertEqual(t.bar.top, 27_720_000)
        self.assertIs(t.itemDelegateForColumn(t.value_col), t.bar)
        self.assertGreaterEqual(t.verticalHeader().defaultSectionSize(), 30)   # 아이콘이 들어갈 줄 높이

    def test_missing_icon_keeps_a_blank_slot(self):
        t = self.table()
        icon = t.proxy.index(1, 0).data(Qt.DecorationRole)
        self.assertFalse(icon.isNull())                                   # 아이콘이 없어도 이름 줄이 어긋나지 않게

    def test_consumable_table_has_no_shop_columns(self):
        t = self.table(sell=False)
        self.assertEqual([c.title for c in t.model_.cols], ['아이템', '수량', '노점가 ○', '가치'])
        self.assertEqual(self.cells(t, 1), ['빨간 머플러', '7', '300', '2,100'])   # 소모품은 노점가 × 수량

    def test_live_table_orders_by_last_pickup(self):
        t = self.table(last=True)
        self.assertEqual(t.model_.cols[-1].title, '마지막 획득')
        self.assertEqual(self.cells(t, 0)[0], '빨간 머플러')              # 방금 얻은 것이 맨 위
        self.assertEqual(self.cells(t, 0)[-1], '00:01:01')

    def test_overcharge_rate_is_explained_in_the_tooltip(self):
        t = self.table()
        t.set_overcharge(24)
        tip = t.proxy.index(1, 3).data(Qt.ToolTipRole)
        self.assertIn('+24%', tip)
        self.assertIn('rAthena', tip)                                     # 미검증 자료임을 밝힌다
        t.set_overcharge(0)
        self.assertIn('오버차지 없음', t.proxy.index(1, 3).data(Qt.ToolTipRole))


if __name__ == '__main__':
    unittest.main()
