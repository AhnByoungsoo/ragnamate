"""국민 옵션(가장 많이 올라온 장비 구성)과 그 적용: 관심 매물 알림·고가 매물 대상·노점 검색. 공식 요청은 가짜 opener."""
import helpers  # noqa: F401  (sys.path 설정)
import datetime
import os
import tempfile
import time
import unittest
import urllib.parse
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QLineEdit

from services import deal_watch as DW
from services import gnjoy_market as G
from services import popular_option as PO
from services import resale_watch as R
from test_gnjoy_market import deal_page, hist_page
from test_vend_search import row

APP = QApplication.instance() or QApplication([])
ARMOR = '계열 : 갑옷'
DB = SimpleNamespace(categories={}, items={
    450000: dict(name='러쉬 가드', description=ARMOR, slot='1'), 450001: dict(name='러쉬 가드', description=ARMOR, slot='1'),
    4001: dict(name='포링 카드', description='계열 : 카드'), 512: dict(name='사과', description='HP를 회복')})
NAME = '[UNIQUE]+12러쉬 가드[1]'
VIEW = ('<th>서버</th><td>바포메트 (prt_mk)</td><th>노점 이름</th><td>가게(캐릭)</td>'
        '<th>슬롯정보</th><td><ul>%s</ul></td><th>랜덤옵션</th><td><ul></ul></td>')


def view(*cards):
    return VIEW % ''.join('<li>%s</li>' % c for c in cards)


def params(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True).items()}


LISTING = [row(450000, NAME, 600000000, 'A', ssi='1'), row(450000, NAME, 610000000, 'B', ssi='2'),
           row(450000, NAME, 620000000, 'C', ssi='3'), row(450000, '[UNIQUE]+11러쉬 가드[1]', 300000000, 'D', ssi='4'),
           row(450000, '+12러쉬 가드[1]', 500000000, 'E', ssi='5'), row(450000, NAME, 1, 'F', ssi='6', kind='buy'),
           row(450000, '[UNIQUE]+12마술의러쉬 가드[1]', 610000000, 'G', ssi='7'),
           row(450000, '[UNIQUE]+12마술의러쉬 가드[1]', 620000000, 'H', ssi='8')]   # 카드 접두사 = 다른 구성
CARDS = {'1': ('요르문간드 카드', '힘 3'), '2': ('요르문간드 카드', '힘 3'), '3': ('포링 카드',)}


class PopularTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'))
        self.addCleanup(self.store.close)
        self.now, self.calls = 1000.0, []

        def opener(url, referer):
            self.calls.append(url)
            p = params(url)
            if 'itemDealView' in url:
                return view(*CARDS[p['ssi']])
            return deal_page(LISTING)
        self.client = G.GnjoyClient(interval=0, opener=opener)
        self.po = PO.PopularOption(self.store, self.client, clock=lambda: self.now)

    def test_equipment_and_config_count(self):
        self.assertTrue(PO.is_equipment(DB, 450000))
        self.assertFalse(PO.is_equipment(DB, 4001))
        self.assertFalse(PO.is_equipment(DB, 512))
        rows = G.parse_listings(deal_page(LISTING))[0]
        p = PO.popular_config(rows, 450000)
        self.assertEqual((p['refine'], p['grade'], p['slots'], p['base'], p['count'], p['total'], p['name']),
                         (12, 'UNIQUE', 1, '러쉬 가드', 3, 7, NAME))          # 구매 노점·접두사 구성은 따로
        self.assertIsNone(PO.popular_config(rows, 999))

    def test_cards_need_two_same(self):
        self.assertEqual(PO.popular_cards([dict(cards=['a']), dict(cards=['a']), dict(cards=['b'])]), (['a'], 2))
        self.assertEqual(PO.popular_cards([dict(cards=['a']), dict(cards=['b'])]), (None, 1))
        self.assertEqual(PO.popular_cards([]), (None, 0))

    def test_find_requests_cache_and_describe(self):
        opt = self.po.find('129', 450000, '러쉬 가드')
        lists = [u for u in self.calls if 'itemDealList' in u]
        views = [u for u in self.calls if 'itemDealView' in u]
        self.assertEqual((len(lists), len(views)), (1, 3))                    # 목록 1쪽(마지막 쪽) + 같은 구성 상세 3건
        self.assertEqual((opt['name'], opt['cards'], opt['cards_seen'], opt['details']),
                         (NAME, ['요르문간드 카드', '힘 3'], 2, 3))
        self.assertIn('카드/인챈트 요르문간드 카드, 힘 3 (2/3건)', PO.describe(opt))
        self.now += PO.CACHE_SEC - 1
        self.po.find('129', 450000, '러쉬 가드')
        self.assertEqual(len(self.calls), 4)                                   # 30분 안 = 요청 없음
        self.now += 2
        self.po.find('129', 450000, '러쉬 가드')
        self.assertEqual(len(self.calls), 5)                                   # 상세는 저장돼 있어 목록만 다시
        self.assertIn('정하지 못했', PO.describe(dict(total=0)))

    def test_exact_market_history_by_full_name(self):
        today = datetime.date.today()
        self.client._open = lambda url, ref: (self.calls.append(url) or hist_page(
            [((today - datetime.timedelta(days=i)).isoformat(), 100, 200, 300) for i in range(3)]))
        m = PO.ExactMarket(self.store, self.client, clock=lambda: self.now)
        self.assertIsNone(m.avg7('129', NAME))                                 # 받기 전: 요청 없이 None
        self.assertEqual(self.calls, [])
        m.history('129', NAME)
        self.assertEqual(params(self.calls[0])['itemFullName'], NAME)          # 슬롯·등급 포함 전체 이름
        self.assertEqual(m.avg7('129', NAME), 200)


class WatchVariantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'))
        self.addCleanup(self.store.close)
        self.now, self.calls = 10000.0, []
        today = datetime.date.today()
        self.hist = [((today - datetime.timedelta(days=i)).isoformat(), 800000000, 800000000, 800000000) for i in range(3)]

        def opener(url, referer):
            self.calls.append(url)
            if 'itemPriceExtendList' in url:
                return hist_page(self.hist)
            if 'itemDealView' in url:
                return view(*CARDS[params(url)['ssi']])
            return deal_page(LISTING)
        self.watch = DW.DealWatch(self.store, G.GnjoyClient(interval=0, opener=opener), clock=lambda: self.now)

    def test_validate_keeps_config(self):
        items = DW.validate_watchlist([dict(item_id=450000, name='러쉬 가드', percent=15, query=NAME, cards=['a']),
                                       dict(item_id=450000, name='러쉬 가드', percent=15)])
        self.assertEqual(items[0]['query'], NAME)
        self.assertEqual(items[0]['cards'], ['a'])
        self.assertNotIn('query', items[1])                                    # 구성이 다르면 같은 번호도 둘 다 가능
        with self.assertRaises(ValueError):
            DW.validate_watchlist([dict(item_id=1, name='a', query='x', cards='a')])

    def test_equipment_compares_only_same_config_and_cards(self):
        item = dict(item_id=450000, name='러쉬 가드', percent=15, query=NAME, cards=['요르문간드 카드', '힘 3'])
        alerts = self.watch.check('129', [item])
        hist_urls = [u for u in self.calls if 'itemPriceExtendList' in u]
        self.assertEqual(params(hist_urls[0])['itemFullName'], NAME)            # 같은 구성 이력
        self.assertEqual(params(self.calls[0])['itemFullName'], NAME)
        # 7일 평균 8억 → 15% 이상 싼 +12 UNIQUE[1] 판매 매물 A·B·C 중 카드가 같은 A·B만(+11·등급 없는 E·구매 F 제외)
        self.assertEqual(sorted(a['shop'] for a in alerts), ['A', 'B'])
        self.assertFalse(any(a.get('cards_unknown') for a in alerts))
        text = DW.alert_text(alerts[0])
        self.assertIn(NAME, text)
        rows = DW.deal_candidates(self.store, '129', [item])
        self.assertEqual((rows[0]['name'], rows[0]['price']), (NAME, 600000000))   # 요청 없이 같은 구성 최저가


class ResaleCardsTest(unittest.TestCase):
    def test_target_by_id_and_cards_check(self):
        t = R.target_from_item(DB, 450001, 12, 'UNIQUE', 1, ['요르문간드 카드', '힘 3'], base='러쉬 가드')
        self.assertEqual((t['item_id'], t['query'], t['cards']), (450001, NAME, ['요르문간드 카드', '힘 3']))
        cfg = R.config(dict(shadows=False, targets=[t]))
        self.assertEqual(R.targets_for(DB, cfg), [t])                           # 같은 이름 여러 번호여도 번호로 유지
        with self.assertRaises(ValueError):
            R.target_from_query(DB, '+12러쉬 가드[1]')                           # 이름만으로는 번호를 못 정함
        today = datetime.date.today()
        hist = [dict(day=(today - datetime.timedelta(days=i)).isoformat(), min=1, avg=800000000, max=1) for i in range(3)]
        t0 = dict(t, item_id=450000)
        listings = [dict(item_id=450000, name=NAME, price=p, qty=1, shop=s, kind='sale', ssi=s) for p, s in
                    ((600000000, 'A'), (780000000, 'B'), (790000000, 'C'))]
        snap = dict(listings=listings, history=hist, history_at=1000.0, fetched_at=1000.0, pages=1, last=1)
        cfg = R.config(dict(min_profit=1_000_000))
        res = R.evaluate({NAME: dict(snap, details={})}, [t0], cfg, now=1001.0, today=today)
        a = next(r for r in res['rows'] if r['shop'] == 'A')
        self.assertEqual((a['eligible'], a['cards_pending'], a['reason']), (False, True, '카드·인챈트 확인 전'))
        res = R.evaluate({NAME: dict(snap, details={'A': dict(cards=['포링 카드'])})}, [t0], cfg, now=1001.0, today=today)
        a = next(r for r in res['rows'] if r['shop'] == 'A')
        self.assertIn('정한 구성과 다름', a['reason'])
        self.assertFalse(a['cards_pending'])
        res = R.evaluate({NAME: dict(snap, details={'A': dict(cards=['요르문간드 카드', '힘 3'])})}, [t0], cfg,
                         now=1001.0, today=today)
        self.assertTrue(next(r for r in res['rows'] if r['shop'] == 'A')['eligible'])

    def test_scan_fetches_pending_details_then_reevaluates(self):
        with tempfile.TemporaryDirectory() as d:
            store = G.VendingStore(os.path.join(d, 'v.db'))
            try:
                today = datetime.date.today()
                calls = []
                page = [row(450000, NAME, 600000000, 'A', ssi='1'), row(450000, NAME, 780000000, 'B', ssi='2'),
                        row(450000, NAME, 790000000, 'C', ssi='3')]

                def opener(url, referer):
                    calls.append(url)
                    if 'itemPriceExtendList' in url:
                        return hist_page([((today - datetime.timedelta(days=i)).isoformat(), 1, 800000000, 1)
                                          for i in range(3)])
                    if 'itemDealView' in url:
                        return view('요르문간드 카드', '힘 3')
                    return deal_page(page)
                sc = R.ResaleScanner(store, G.GnjoyClient(interval=0, opener=opener), clock=lambda: 5000.0)
                t = dict(item_id=450000, name='러쉬 가드', query=NAME, cards=['요르문간드 카드', '힘 3'])
                res = sc.scan('129', [t], R.config(dict(min_profit=1_000_000)))
                views = [u for u in calls if 'itemDealView' in u]
                self.assertEqual(len(views), 1)                                  # 가격 조건 맞는 A만 상세
                a = next(r for r in res['rows'] if r['shop'] == 'A')
                self.assertTrue(a['eligible'])
            finally:
                store.close()


class CompleterTest(unittest.TestCase):
    def test_contains_case_insensitive_and_update(self):
        from ui.completer import attach_completer, set_words
        e = QLineEdit()
        c = attach_completer(e, ['러쉬 가드', 'Poring Card', '러쉬 가드', None, ' '])
        self.assertEqual(c.model().stringList(), ['Poring Card', '러쉬 가드'])   # 중복·빈 값 제거
        c.setCompletionPrefix('card')
        self.assertEqual(c.currentCompletion(), 'Poring Card')                  # 가운데 일치·대소문자 무시
        set_words(c, ['사과'])
        self.assertEqual(c.model().stringList(), ['사과'])

    def test_storage_overlay_words_follow_rows(self):
        from ui.storage_overlay import StorageOverlay

        class S(dict):
            def save(self):
                pass
        ov = StorageOverlay(S(storage_overlay_prices=['sale'], hud_scale=1.0))
        self.addCleanup(ov.close)
        ov.set_rows([dict(item_id=1, name='스펠 서킷', quantity=1, locations='창고', unit=1, average=None, value=1,
                          updated_at=None)])
        self.assertEqual(ov.search_completer.model().stringList(), ['스펠 서킷'])


class VendSearchPopularTest(unittest.TestCase):
    def test_equipment_results_default_to_popular_config(self):
        from ui.vend_search_dialog import VendSearchDialog

        class S(dict):
            def save(self):
                pass
        with tempfile.TemporaryDirectory() as d:
            store = G.VendingStore(os.path.join(d, 'v.db'))
            calls = []
            client = G.GnjoyClient(interval=0, opener=lambda url, ref: calls.append(url) or (
                hist_page([]) if 'itemPriceExtendList' in url else deal_page(LISTING)))
            dlg = VendSearchDialog(None, S(), DB, store, client)
            try:
                dlg.query.setText('러쉬 가드')
                dlg.search()
                t0 = time.time()
                while dlg.task.future is not None and time.time() - t0 < 5:
                    APP.processEvents()
                    time.sleep(0.02)
                APP.processEvents()
                self.assertFalse(dlg.only_popular.isHidden())
                self.assertIn(NAME, dlg.only_popular.text())
                self.assertEqual(sorted(r['shop'] for r in dlg.table.model_.rows), ['A', 'B', 'C', 'F'])  # 구매도 같은 구성이면
                dlg.only_popular.setChecked(False)
                self.assertEqual(len(dlg.table.model_.rows), 8)
                dlg.table.selectRow(0)
                dlg.fetch_history()
                while dlg.task.future is not None and time.time() - t0 < 5:
                    APP.processEvents()
                    time.sleep(0.02)
                hist = [u for u in calls if 'itemPriceExtendList' in u]
                self.assertEqual(params(hist[0])['itemFullName'], dlg.current['name'])   # 매물 전체 이름으로 이력
                dlg.add_watch()
                self.assertEqual(dlg.s['deal_watch']['items'][0]['query'], NAME)         # 장비는 국민 옵션 구성으로 추가
            finally:
                dlg.close()
                store.close()


if __name__ == '__main__':
    unittest.main()
