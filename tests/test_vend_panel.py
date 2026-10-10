"""노점 개설 추천가 패널의 시세 받기·표시: 공식 검색 밀림, 내 노점 판정, 실패 표시. 네트워크는 가짜 opener."""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from helpers import ROOT  # noqa: F401  (sys.path 설정)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import vend_ocr as V  # noqa: E402
from services import gnjoy_market as G  # noqa: E402
from test_gnjoy_market import deal_page, deal_row, hist_page  # noqa: E402

APP = QApplication.instance() or QApplication([])
TODAY = time.strftime('%Y-%m-%d')


class VendPanelPriceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, 'data'))
        self.pages = {}
        self.fail = None

        def opener(url, referer):
            if self.fail:
                raise self.fail
            if 'itemDealList' in url:
                return self.pages['deal']
            return hist_page([(TODAY, 100000, 100000, 100000)] * 3)
        client = G.GnjoyClient(interval=0, opener=opener)
        import ui.vend_panel as P
        with patch.object(P, 'BASE_DIR', self.tmp.name), patch.object(P.G, 'shared_client', return_value=client):
            self.panel = P.VendPanel(SimpleNamespace(item_db=None, entries=[]))
        self.addCleanup(self.panel.store.close)
        self.panel._ids = {'사과': [512]}
        self.panel.state = dict(hwnd=0, client=(0, 0, 1, 1), region=(0, 0, 1, 1), rows=[], shop='')

    def row(self, price=90000):
        r = V.VendRow('사과', price)
        r.match = '사과'
        return r

    def test_crowded_search_is_unknown_not_no_listing(self):
        # '사과'로 검색했더니 더 싼 '풋사과'만 2쪽을 채우고 뒤 쪽이 남음 → 사과 줄이 안 보임
        self.pages['deal'] = deal_page([deal_row(12846, '풋사과', 1, 10, 'x', 'sale')] * 10, last=4)
        self.panel._fetch_price('사과')
        self.assertIsNone(self.panel.store.latest_listings('129', 512)[0])      # '노점 없음'으로 저장 안 함
        d = self.panel._row_data(self.row())
        self.assertEqual(d['low'], '확인 불가')
        self.assertIn('평균 기준', d['verdict'])                                  # 최저가 없이 평균만으로 판단했음을 밝힘
        self.assertTrue(self.panel._fresh('사과'))                               # 30분 안에는 다시 요청하지 않음
        # 뒤 쪽이 없으면(검색 결과가 다 보였으면) 진짜로 노점 없음
        self.pages['deal'] = deal_page([deal_row(12846, '풋사과', 1, 10, 'x', 'sale')])
        self.panel._fetch_price('사과')
        self.assertEqual(self.panel._row_data(self.row())['lowNote'], '노점 없음')

    def test_own_listing_needs_shop_name(self):
        self.pages['deal'] = deal_page([deal_row(512, '사과', 1, 90000, '내가게', 'sale'),
                                        deal_row(512, '사과', 1, 95000, '남', 'sale')])
        self.panel._fetch_price('사과')
        d = self.panel._row_data(self.row(90000))                                 # 내 상점명을 못 읽음
        self.assertEqual(d['low'], '90,000')
        self.assertIn('내 노점일 수 있음', d['lowNote'])
        self.panel.state['shop'] = '내가게'                                          # 상점명을 읽음 → 뺀다
        d = self.panel._row_data(self.row(90000))
        self.assertEqual(d['low'], '95,000')
        self.assertNotIn('내 노점', d['lowNote'])

    def test_failure_is_shown_not_stuck_on_loading(self):
        self.fail = OSError('네트워크 끊김')
        with self.assertRaises(OSError):
            self.panel._fetch_price('사과')
        self.panel._failed['사과'] = '시세 받기 실패'                                  # _price_loop 가 남기는 표시
        d = self.panel._row_data(self.row())
        self.assertEqual(d['low'], '시세 받기 실패')
        self.assertIn('다시 시도', d['verdict'])


class VendPanelEquipmentTest(unittest.TestCase):
    """제련·카드가 붙은 장비: 같은 제련 매물로 견주고, 장착 카드 시세를 따로 보여 준다."""

    def setUp(self):
        from core.models import ItemEntry
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, 'data'))
        client = G.GnjoyClient(interval=0, opener=lambda *a: b'')
        import ui.vend_panel as P
        with patch.object(P, 'BASE_DIR', self.tmp.name), patch.object(P.G, 'shared_client', return_value=client):
            self.panel = P.VendPanel(SimpleNamespace(item_db=None, entries=[]))
        self.addCleanup(self.panel.store.close)
        self.panel._ids = {'글레이시아 가드': [1], '포링 카드': [4001]}
        self.panel.state = dict(hwnd=0, client=(0, 0, 1, 1), region=(0, 0, 1, 1), rows=[], shop='')
        db = {1: dict(name='글레이시아 가드'), 4001: dict(name='포링 카드')}
        self.panel.app = SimpleNamespace(
            item_db=SimpleNamespace(items=db, get=db.get,
                                    name=lambda i: (db.get(i) or {}).get('name', '아이템 #%s' % i)),
            entries=[ItemEntry('창고1', 1, '글레이시아 가드', 1, refine=12, cards=[4001], is_equip=True)])

    def equip_row(self, price=100_000_000, refine=12, prefix=''):
        r = V.VendRow('+%d 글레이시아 가드' % refine, price)
        r.match, r.refine, r.prefix = '글레이시아 가드', refine, prefix
        return r

    def test_same_refine_listings_are_compared(self):
        now = time.time()
        self.panel.store.save_listings('129', 1, [
            dict(price=120_000_000, qty=1, shop='남', kind='sale', name='+12글레이시아 가드', ssi='a'),
            dict(price=50_000_000, qty=1, shop='남', kind='sale', name='+9글레이시아 가드', ssi='b')], now=now)
        d = self.panel._row_data(self.equip_row())
        self.assertEqual(d['name'], '+12 글레이시아 가드')
        self.assertEqual(d['low'], '120,000,000')              # +9 매물은 섞지 않는다
        self.assertIn('같은 제련 1건 · 구성 다름(참고)', d['lowNote'])   # 숫자를 '최저가'로 단정하지 않는다
        self.assertNotIn('제련·옵션 달라 참고만', d['verdict'])

    def test_socketed_card_price_is_shown_separately(self):
        now = time.time()
        self.panel.store.save_listings('129', 1, [dict(price=120_000_000, qty=1, shop='남', kind='sale',
                                                       name='+12글레이시아 가드', ssi='a')], now=now)
        self.panel.store.save_listings('129', 4001, [dict(price=6_000_000, qty=1, shop='남', kind='sale',
                                                          name='포링 카드', ssi='c')], now=now)
        d = self.panel._row_data(self.equip_row())
        self.assertIn('포링 카드', d['card'])
        self.assertIn('6,000,000', d['card'])
        self.assertIn('따로 팔아야', d['cardTip'])              # 합계에 더하지 않는다고 분명히 적는다

    def test_card_prefix_without_replay_says_unconfirmed(self):
        self.panel.app.entries = []                            # 리플레이가 없으면 어떤 카드인지 알 수 없다
        d = self.panel._row_data(self.equip_row(prefix='마술의'))
        self.assertIn('확인 필요', d['card'])
        self.assertIn('카드 접두사', d['lowNote'])

    def test_equipment_is_queued_with_variant_fetch(self):
        calls = []
        self.panel.insights.fetch_variant = lambda server, item, stop=None: calls.append(item)
        self.panel._equip['글레이시아 가드'] = dict(item_id=1, name='글레이시아 가드', refine=12,
                                             card_names=['포링 카드'], options=[])
        self.panel._fetch_price('글레이시아 가드')
        self.assertEqual(calls[0]['refine'], 12)
        self.assertEqual(calls[0]['card_names'], ['포링 카드'])


class VendPanelInlineTest(unittest.TestCase):
    """'아이템 옆 한 줄' 모양: 게임 노점 창의 줄 y에 맞춰 추천가를 하나씩 붙인다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, 'data'))
        import ui.vend_panel as P
        self.P = P
        client = G.GnjoyClient(interval=0, opener=lambda url, referer: '')
        self.settings = {'overlay_style': {'vend': 'inline'}}
        with patch.object(P, 'BASE_DIR', self.tmp.name), patch.object(P.G, 'shared_client', return_value=client):
            self.panel = P.VendPanel(SimpleNamespace(item_db=None, entries=[], s=self.settings))
        self.addCleanup(self.panel.store.close)
        self.addCleanup(self.panel.close)

    def rows(self):
        out = []
        for name, y, price in (('사과', 40, 1000), ('바나나', 96, 0)):
            r = V.VendRow(name, price)
            r.match, r.y = name, y
            out.append(r)
        return out

    def state(self):
        return dict(hwnd=0, client=(100, 50, 800, 600), region=(10, 20, 300, 420), rows=self.rows(), shop='')

    def render(self):
        self.panel.state = self.state()
        self.panel._render()
        return [b for b in self.panel.inline_rows if b.isVisibleTo(self.panel.inline)]

    def test_one_line_per_item_at_the_same_height(self):
        shown = self.render()
        self.assertEqual(len(shown), 2)
        dpr = self.panel.devicePixelRatioF() or 1
        for b, r in zip(shown, self.panel.state['rows']):
            self.assertAlmostEqual(b.y() + b.height() / 2, r.y / dpr, delta=2)   # 아이템 줄 높이에 맞음
        self.assertEqual(self.panel.width(), self.P.INLINE_W)                    # 좁은 띠
        self.assertFalse(self.panel.head_w.isVisibleTo(self.panel))              # 머리줄·표는 감춘다
        self.assertFalse(self.panel.table.isVisibleTo(self.panel))

    def test_fewer_items_hides_leftover_lines(self):
        self.render()
        self.panel.state = dict(self.state(), rows=self.rows()[:1])
        self.panel._render()
        self.assertEqual(sum(1 for b in self.panel.inline_rows if b.isVisibleTo(self.panel.inline)), 1)

    def test_clicking_a_line_copies_the_recommended_price(self):
        from PySide6.QtGui import QGuiApplication
        shown = self.render()
        self.panel._inline_data[shown[0]]['rec'] = 1234
        shown[0].click()
        self.assertEqual(QGuiApplication.clipboard().text(), '1234')

    def test_table_style_still_works(self):
        self.settings['overlay_style'] = {'vend': 'table'}
        self.panel.state = self.state()
        self.panel._render()
        self.assertTrue(self.panel.table.isVisibleTo(self.panel))
        self.assertFalse(self.panel.inline.isVisibleTo(self.panel))
        self.assertEqual(self.panel.table.rowCount(), 2)


if __name__ == '__main__':
    unittest.main()
