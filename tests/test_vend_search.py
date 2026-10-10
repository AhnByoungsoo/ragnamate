"""노점 검색: 공식 요청 조건·캐시·거르기·요약, 검색 창 흐름. 공식 요청은 가짜 opener."""
import helpers  # noqa: F401  (sys.path 설정)
import datetime
import os
import tempfile
import time
import unittest
import urllib.parse
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from services import gnjoy_market as G
from services import vend_search as VS
from test_gnjoy_market import deal_page, hist_page

APP = QApplication.instance() or QApplication([])


def row(iid, name, price, shop, ssi='1', qty=1, kind='sale', server='바포메트'):
    return ('<tr><td>%s</td><td class="item"><a href="#" onclick="javascript:CallItemDealView(129,2023,\'%s\',1)">'
            '<img src="https://imgc1.gnjoy.com/games/ro1/object/201306/%d.png" alt="%s" /><span>%s</span></a></td>'
            '<td class="quantity">%d</td><td class="price"><span class="priceLv3">%s</span></td>'
            '<td class="shop %s">%s</td></tr>' % (server, ssi, iid, name, name, qty, format(price, ','), kind, shop))


PAGE1 = [row(4001, '스펠 매직 어택 스톤(걸칠것)', 15000000, '돌팜', ssi='11'),
         row(4002, '+9일루시온 레그 B타입[1]', 90000000, '장비상', ssi='12', server='이프리트'),
         row(4003, '스펠 스톤(듀얼)', 86000000, '사요', ssi='13', kind='buy')]
PAGE2 = [row(4001, '스펠 매직 어택 스톤(걸칠것)', 18000000, '또돌팜', ssi='21')]


def params(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True).items()}


class HelpersTest(unittest.TestCase):
    def test_query_and_zeny(self):
        self.assertEqual(VS.build_query('  스펠   스톤 '), '스펠%스톤')
        self.assertEqual(VS.build_query('스펠 스톤', all_words=False), '스펠 스톤')
        cases = {'1억 5000만': 150000000, '15,000,000': 15000000, '1.5m': 1500000, '300k': 300000, '300만': 3000000,
                 '': None, 'abc': None, '1억5000': 100005000}
        for text, want in cases.items():
            self.assertEqual(VS.parse_zeny(text), want, text)

    def test_filter_sort_summary(self):
        rows = [VS._enrich(r, '129') for r in G.parse_listings(deal_page(PAGE1 + PAGE2))[0]]
        self.assertEqual([r['sid'] for r in rows], ['129', '729', '129', '129'])           # 전체 서버 검색도 줄마다 서버 번호
        self.assertEqual((rows[1]['refine'], rows[1]['base'], rows[1]['slots']), (9, '일루시온 레그 B타입', 1))
        sale = VS.filter_rows(rows, ('sale',))
        self.assertEqual(len(sale), 3)
        self.assertEqual(len(VS.filter_rows(rows, refine_min=9)), 1)
        self.assertEqual([r['price'] for r in VS.filter_rows(rows, price_min=16000000, price_max=90000000)],
                         [90000000, 86000000, 18000000])
        self.assertEqual(len(VS.filter_rows(rows, item_id=4001)), 2)
        self.assertEqual(VS.sort_rows(rows, '-price')[0]['price'], 90000000)
        self.assertEqual(VS.sort_rows(rows, '-refine')[0]['refine'], 9)
        # 판매가 먼저, 구매(사겠다는 노점)는 뒤 — 더 싼 구매가가 맨 앞에 와서 가격을 잘못 읽는 일이 없게
        self.assertEqual([r['kind'] for r in VS.sort_rows(rows, 'price')], ['sale', 'sale', 'sale', 'buy'])
        self.assertEqual(VS.sort_rows(rows, 'price')[0]['price'], 15000000)
        s = VS.summary(rows)
        self.assertEqual((s['count'], s['sale'], s['buy'], s['low'], s['median']), (4, 3, 1, 15000000, 18000000))
        self.assertEqual(s['buy_high'], next(r['price'] for r in rows if r['kind'] == 'buy'))
        self.assertEqual(round(VS.vs_avg(90, 100)), -10)
        self.assertIsNone(VS.vs_avg(90, None))


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.now, self.calls = 1000.0, []

        def opener(url, referer):
            self.calls.append(url)
            p = params(url)
            if 'itemDealView' in url:
                return '<th>서버</th><td>바포메트 (prt_mk)</td><th>노점 이름</th><td>돌팜(캐릭)</td>' \
                       '<th>슬롯정보</th><td><ul><li>포링 카드</li><li>-</li></ul></td><th>랜덤옵션</th><td><ul></ul></td>'
            return deal_page(PAGE1 if p['curpage'] == '1' else PAGE2, last=3)
        self.client = G.GnjoyClient(interval=0, opener=opener)
        self.vs = VS.VendSearch(self.client, clock=lambda: self.now)

    def test_request_params_pages_and_cache(self):
        r = self.vs.search('', '스펠%스톤', 'E', 'DESC', pages=2)
        self.assertEqual(len(self.calls), 2)
        p = params(self.calls[0])
        self.assertEqual((p['svrID'], p['itemFullName'], p['inclusion'], p['itemOrder'], p['curpage']),
                         ('', '스펠%스톤', 'E', 'DESC', '1'))
        self.assertEqual((len(r['rows']), r['pages'], r['last'], r['cached']), (4, 2, 3, False))
        self.now += VS.CACHE_SEC - 1
        self.assertTrue(self.vs.search('', '스펠%스톤', 'E', 'DESC', pages=2)['cached'])
        self.assertEqual(len(self.calls), 2)                                             # 30분 안 = 요청 없음
        self.vs.search('', '스펠%스톤', 'E', '', pages=2)                                  # 조건이 다르면 새로
        self.assertEqual(len(self.calls), 4)
        self.now += VS.CACHE_SEC
        self.vs.search('', '스펠%스톤', 'E', 'DESC', pages=2)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.vs.search('129', 'x', pages=99)['pages'], 3)             # 쪽을 크게 넣어도 마지막 쪽까지만

    def test_stop_between_pages_and_shared_client(self):
        import threading
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.vs.search('129', '스펠', pages=3, stop=stop)['pages'], 1)   # 받은 쪽은 보여 주고 멈춤
        self.assertIs(VS.VendSearch().client, G.shared_client())

    def test_detail_cached_in_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = G.VendingStore(os.path.join(d, 'v.db'))
            try:
                r = self.vs.search('129', '스펠')['rows'][0]
                det = self.vs.detail(store, r)
                self.assertEqual((det['map'], det['character'], det['cards']), ('prt_mk', '캐릭', ['포링 카드']))
                n = len(self.calls)
                self.vs.detail(store, r)
                self.assertEqual(len(self.calls), n)                                     # 같은 매물은 다시 안 받음
            finally:
                store.close()


class MorePagesTest(unittest.TestCase):
    """공식 사이트가 알려 주는 '전체 쪽 수'는 실제보다 적을 수 있다(2026-10-05 실측: 3쪽이라더니 4쪽까지 매물이 있었다).
    꽉 찬 쪽이면 더 받고, 늘려 부를 때는 못 받은 쪽만 받는다."""

    def setUp(self):
        self.calls = []
        self.full_pages = 4                      # 실제로 10건씩 꽉 찬 쪽 수

        def opener(url, referer):
            self.calls.append(url)
            page = int(params(url)['curpage'])
            rows = [row(4001, '글레이시아 가드', 1000 * page + i, '가게%d' % i, ssi='%d%d' % (page, i))
                    for i in range(10 if page <= self.full_pages else 3)]
            return deal_page(rows, last=2)       # 공식은 2쪽이라고 알려 준다(실제와 다름)
        self.vs = VS.VendSearch(G.GnjoyClient(interval=0, opener=opener), clock=lambda: 1000.0)

    def pages_asked(self):
        return [params(u)['curpage'] for u in self.calls]

    def test_full_page_means_there_may_be_more(self):
        r = self.vs.search('129', '글레이시아 가드', pages=4)
        self.assertEqual(self.pages_asked(), ['1', '2', '3', '4'])    # 공식이 2쪽이라 해도 꽉 찼으면 더 받는다
        self.assertEqual((len(r['rows']), r['pages'], r['more']), (40, 4, True))

    def test_more_only_fetches_the_missing_pages(self):
        self.vs.search('129', '글레이시아 가드', pages=2)
        self.assertEqual(self.pages_asked(), ['1', '2'])
        r = self.vs.search('129', '글레이시아 가드', pages=4)         # [더 받기]
        self.assertEqual(self.pages_asked(), ['1', '2', '3', '4'])   # 1·2쪽은 다시 요청하지 않음
        self.assertEqual(len(r['rows']), 40)
        self.assertEqual(len({x['ssi'] for x in r['rows']}), 40)     # 같은 매물이 두 번 들어가지 않음
        self.assertFalse(r['cached'])
        again = self.vs.search('129', '글레이시아 가드', pages=4)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(again['cached'])

    def test_partial_last_page_means_done(self):
        self.full_pages = 1
        r = self.vs.search('129', '글레이시아 가드', pages=5)
        self.assertEqual(self.pages_asked(), ['1', '2'])              # 2쪽이 덜 찼으면 거기서 끝
        self.assertEqual((len(r['rows']), r['more']), (13, False))


class Settings(dict):
    def save(self):
        self.saved = True


class DialogTest(unittest.TestCase):
    def setUp(self):
        from ui.vend_search_dialog import VendSearchDialog
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'))
        self.addCleanup(self.store.close)
        self.calls = []

        def opener(url, referer):
            self.calls.append(url)
            if 'itemPriceExtendList' in url:
                today = datetime.date.today()
                return hist_page([((today - datetime.timedelta(days=i)).isoformat(), 20000000, 20000000, 20000000)
                                  for i in range(3)])
            return deal_page(PAGE1 + PAGE2)
        db = SimpleNamespace(items={4001: dict(name='스펠 매직 어택 스톤(걸칠것)')}, name=lambda i: {4001: '스펠 매직 어택 스톤(걸칠것)'}.get(i, str(i)))
        self.s = Settings(deal_watch={'enabled': False, 'server': '129', 'items': []})
        self.watch_changed = []
        self.d = VendSearchDialog(None, self.s, db, self.store, G.GnjoyClient(interval=0, opener=opener),
                                  map_name=lambda c: {'prt_mk': '프론테라 노점'}.get(c, c),
                                  on_watch_changed=lambda: self.watch_changed.append(1))
        self.addCleanup(self.d.close)
        self.d.show()

    def wait(self):
        t0 = time.time()
        while self.d.task.future is not None and time.time() - t0 < 5:
            APP.processEvents()
            time.sleep(0.02)
        APP.processEvents()

    def test_search_filter_history_watch_and_recent(self):
        d = self.d
        self.assertEqual(d.completer.model().stringList(), ['스펠 매직 어택 스톤(걸칠것)'])   # 보일 때 자동완성 채움
        d.query.setText('스펠 스톤')
        d.search()
        self.wait()
        self.assertIn('%25', self.calls[0])                                             # '스펠%스톤'
        self.assertEqual(d.table.model_.rowCount(), 4)
        self.assertIn('매물 4건', d.summary.text())
        self.assertEqual(self.s['vend_search']['recent'], ['스펠 스톤'])
        d.buy.setChecked(False)
        d.pmax.setText('2000만')
        self.assertEqual([r['price'] for r in d.table.model_.rows], [15000000, 18000000])
        self.assertIn('거르기로 2건 숨김', d.summary.text())
        d.pmin.setText('이상한값')
        self.assertIn('FF7A7A', d.pmin.styleSheet())
        d.pmin.setText('')
        d.table.selectRow(0)
        self.assertEqual(d.current['price'], 15000000)
        self.assertIn('저장된 7일 시세 없음', d.detail.text())
        d.fetch_history()
        self.wait()
        self.assertEqual(d.current['vs'], -25.0)                                         # 15M vs 7일 평균 20M
        m = d.table.model_
        self.assertEqual(m.data(m.index(0, 7)), '▼ -25%')
        self.assertIn('▼ -25%', d.detail.text())
        d.add_watch()
        self.assertEqual(self.s['deal_watch']['items'][0]['item_id'], 4001)
        self.assertEqual(self.watch_changed, [1])
        d.add_watch()
        self.assertEqual(len(self.s['deal_watch']['items']), 1)                         # 두 번 넣지 않음
        d._copy('price')
        self.assertEqual(QApplication.clipboard().text(), '15000000')

    def test_more_button_continues_from_where_it_stopped(self):
        """[더 받기]: 이미 받은 쪽은 다시 묻지 않고 뒤쪽만 이어 받는다(비싼 매물은 뒤쪽에 있다)."""
        d = self.d
        asked = []

        def full(url, ref):
            if 'itemDealList' not in url:
                return deal_page([])
            page = params(url)['curpage']
            asked.append(page)
            return deal_page([row(4001, '글레이시아 가드', 1000 + i, '가게%d' % i, ssi='%s%d' % (page, i))
                              for i in range(10)], last=1)          # 공식은 1쪽이라 하지만 꽉 차 있다
        d.engine.client._open = full
        d.pages.setValue(1)
        d.query.setText('글레이시아 가드')
        d.search()
        self.wait()
        self.assertEqual(asked, ['1'])
        self.assertTrue(d.btn_more.isEnabled())
        self.assertIn('더 받기', d.summary.text())
        d.btn_more.click()
        self.wait()
        self.assertEqual(asked, ['1', '2', '3', '4'])
        self.assertEqual(d.table.model_.rowCount(), 40)
        self.assertEqual(d.pages.value(), 4)

    def test_history_without_recent_trades_says_so(self):
        d = self.d
        d.query.setText('스펠')
        d.search()
        self.wait()
        d.table.selectRow(0)
        old = [('2026-01-0%d' % i, 100, 100, 100) for i in range(1, 4)]
        d.engine.client._open = lambda url, ref: hist_page(old)
        d.fetch_history()
        self.wait()
        self.assertIn('최근 7일 거래가 없습니다(가장 최근 거래일 2026-01-03)', d.status.text())
        self.assertIsNone(d.current['vs'])

    def test_rate_limited_blocks_request(self):
        self.d.engine.client._paused_until = time.monotonic() + 100
        self.d.query.setText('스펠')
        self.d.search()
        self.assertIsNone(self.d.task.future)
        self.assertIn('429', self.d.status.text())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
