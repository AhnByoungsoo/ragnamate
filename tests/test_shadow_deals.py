"""+10 쉐도우 저가 TOP 20 · 재료 차익 · 자동 확인 알림. 공식 요청은 가짜 opener."""
import helpers  # noqa: F401  (sys.path 설정)
import datetime
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMenu, QToolButton, QVBoxLayout, QWidget

from services import gnjoy_market as G
from services import shadow_deals as SD
from test_gnjoy_market import deal_page, hist_page

APP = QApplication.instance() or QApplication([])
SHADOW = '계열 : 쉐도우 장비'
DB = SimpleNamespace(categories={}, items={
    24386: dict(name='인피니티 웨폰 쉐도우', description=SHADOW), 24733: dict(name='인피니티 쉴드 쉐도우', description=SHADOW),
    24339: dict(name='올마이티 이어링 쉐도우', description=SHADOW), 24340: dict(name='올마이티 펜던트 쉐도우', description=SHADOW),
    24675: dict(name='트루 젬스톤 아머 쉐도우', description=SHADOW), 24767: dict(name='메가 블리츠 쉴드 쉐도우', description=SHADOW),
    102812: dict(name='+10 올마이티 쉐도우 선택상자', description='상자'), 101863: dict(name='인피니티 웨폰 쉐도우 상자', description='상자'),
    101311: dict(name='인피니티 쉐도우 마법서', description='마법서'), 23474: dict(name='인피니티 쉐도우 뚝딱상자', description='상자'),
    4001: dict(name='포링 카드', description='계열 : 카드')})
WEAPON = dict(item_id=24386, name='인피니티 웨폰 쉐도우', family='인피니티')


def row(iid, name, price, shop, ssi='1', qty=1, kind='sale'):
    return ('<tr><td>바포메트</td><td class="item"><a href="#" onclick="javascript:CallItemDealView(129,2023,\'%s\',1)">'
            '<img src="https://imgc1.gnjoy.com/games/ro1/object/201306/%d.png" alt="%s" /><span>%s</span></a></td>'
            '<td class="quantity">%d</td><td class="price"><span class="priceLv3">%s</span></td>'
            '<td class="shop %s">%s</td></tr>' % (ssi, iid, name, name, qty, format(price, ','), kind, shop))


def listing(price, shop, name='+10인피니티 웨폰 쉐도우', iid=24386, kind='sale', **kw):
    return dict(dict(item_id=iid, name=name, price=price, shop=shop, kind=kind, qty=1, ssi=shop + str(price)), **kw)


TODAY = datetime.date(2026, 9, 27)


def hist(*avgs, start=0):
    """오늘부터 하루씩 거슬러 날짜별 평균 거래가."""
    return [dict(day=(TODAY - datetime.timedelta(days=start + i)).isoformat(), min=a, avg=a, max=a) for i, a in enumerate(avgs)]


def snap(*ls, pages=1, last=1, history=None):
    return {24386: dict(listings=[l for l in ls if SD.is_plus10(l, WEAPON)], pages=pages, last=last, fetched_at=1000.0,
                        history=hist(200, 200, 200) if history is None else history)}


class TargetAndParseTest(unittest.TestCase):
    def test_targets_are_real_shadow_gear_only(self):
        names = [t['name'] for t in SD.shadow_targets(DB)]
        self.assertIn('트루 젬스톤 아머 쉐도우', names)                    # '젬스톤'은 계열 이름이라 제외 안 함
        self.assertIn('메가 블리츠 쉴드 쉐도우', names)
        for bad in ('+10 올마이티 쉐도우 선택상자', '인피니티 웨폰 쉐도우 상자', '인피니티 쉐도우 마법서', '인피니티 쉐도우 뚝딱상자', '포링 카드'):
            self.assertNotIn(bad, names)
        self.assertEqual([t['name'] for t in SD.shadow_targets(DB, ['올마이티'])], ['올마이티 이어링 쉐도우', '올마이티 펜던트 쉐도우'])

    def test_exact_plus10_and_item_id(self):
        ok = lambda n, iid=24386: SD.is_plus10(dict(item_id=iid, name=n), WEAPON)   # noqa: E731
        self.assertTrue(ok('+10인피니티 웨폰 쉐도우'))
        self.assertTrue(ok('+10 인피니티 웨폰 쉐도우'))                        # 제련 뒤 공백
        self.assertFalse(ok('+9인피니티 웨폰 쉐도우'));self.assertFalse(ok('+11인피니티 웨폰 쉐도우'))
        self.assertFalse(ok('인피니티 웨폰 쉐도우'))
        self.assertFalse(ok('+10인피니티 웨폰 쉐도우 상자', 101863))            # 부분 일치로 섞인 다른 아이템 번호
        self.assertFalse(ok('+10 올마이티 쉐도우 선택상자', 102812))
        self.assertEqual(SD.shop_coords('프론시장 6시 165 46'), '165 46');self.assertEqual(SD.shop_coords('vcvv'), '')


class EvaluateTest(unittest.TestCase):
    def ev(self, *ls, percent=15, **kw):
        return SD.evaluate(snap(*ls, **{k: v for k, v in kw.items() if k in ('pages', 'last', 'history')}), [WEAPON], percent,
                           kw.get('own', ()), today=TODAY)

    def test_base_is_seven_day_plus10_average(self):
        h = hist(100, 200, 300) + hist(9999, start=7) + [dict(day='2026-09-28', min=1, avg=1, max=1)]   # 8일 전·미래 제외
        self.assertEqual(SD.seven_day_base(h, TODAY), (200, 3))
        r = self.ev(listing(150, 'A'), listing(260, 'B'), history=h)
        top = r['top'][0]
        self.assertEqual((top['shop'], top['base'], top['days']), ('A', 200, 3))
        self.assertAlmostEqual(top['discount'], 25);self.assertEqual(top['diff'], 50)
        self.assertIn('최근 7일 거래 평균(거래 3일)보다 25% 저렴', top['reason'])
        self.assertEqual(len(r['top']), 1)                                           # 기준가보다 비싼 B 는 제외

    def test_fewer_than_three_trade_days_is_insufficient(self):
        r = self.ev(listing(100, 'A'), listing(120, 'B'), history=hist(200, 200) + hist(0, start=2))   # 평균 0 인 날 제외
        self.assertEqual((r['top'], r['insufficient']), ([], 2))
        self.assertEqual(len(self.ev(listing(100, 'A'), history=hist(200, 200, 200))['top']), 1)        # 3일이면 확정

    def test_listing_count_does_not_change_base(self):
        bulk = [listing(1000, 'D', ssi='d%d' % i) for i in range(10)]                 # 비싼 매물이 많아도 기준은 거래 평균
        r = self.ev(listing(100, 'A'), *bulk)
        self.assertEqual(r['top'][0]['base'], 200)
        dup = self.ev(listing(100, 'A', ssi='a1'), listing(100, 'A', ssi='a2'))
        self.assertEqual(len(dup['top']), 1)                                         # 같은 상점·같은 가격은 한 줄

    def test_options_do_not_affect_ranking(self):
        base = [listing(100, 'A'), listing(150, 'E'), listing(160, 'B')]
        plain = [(x['shop'], round(x['discount'], 3)) for x in self.ev(*base)['top']]
        opt = [dict(l, options=['물리 +5%'], cards=['x']) if l['shop'] == 'E' else l for l in base]
        self.assertEqual(plain, [(x['shop'], round(x['discount'], 3)) for x in self.ev(*opt)['top']])

    def test_top20_numeric_sort_limit_threshold_and_zero(self):
        ls = [listing(100 + i, 'S%02d' % i) for i in range(40)] + [listing(1, 'ONE'), listing(20, 'TEN')]
        r = self.ev(*ls)
        self.assertEqual(len(r['top']), 20)
        d = [x['discount'] for x in r['top']];self.assertEqual(d, sorted(d, reverse=True))
        self.assertEqual([x['rank'] for x in r['top']], list(range(1, 21)))
        self.assertTrue(all(x['discount'] >= 15 for x in r['candidates']))          # 할인 없는 매물로 채우지 않음
        ten = next(x for x in r['top'] if x['shop'] == 'TEN');self.assertTrue(ten['zero'])     # 기준가의 1/10
        self.assertIn('0 누락 의심', ten['reason']);self.assertFalse(next(x for x in r['top'] if x['shop'] == 'ONE')['zero'])
        self.assertEqual(len(self.ev(listing(100, 'A'), listing(190, 'B'))['top']), 1)          # 실제 개수만
        tie = self.ev(listing(150, 'A'), listing(150, 'B'), percent=1)
        self.assertEqual({x['shop'] for x in tie['top']}, {'A', 'B'})

    def test_scope_partial_unchecked_and_own(self):
        s = snap(listing(100, 'A'), pages=5, last=8)
        r = SD.evaluate(s, [WEAPON, dict(item_id=24733, name='인피니티 쉴드 쉐도우', family='인피니티')], 15, today=TODAY)
        self.assertEqual((r['partial'], r['unchecked']), (['인피니티 웨폰 쉐도우'], ['인피니티 쉴드 쉐도우']))
        self.assertEqual(self.ev(listing(100, 'A'), own=[dict(name='인피니티 웨폰 쉐도우', price=100, shop='A')])['top'], [])
        self.assertEqual(len(self.ev(listing(100, 'A'), own=[dict(name='인피니티 웨폰 쉐도우', price=100, shop='')])['top']), 1)


class ArbitrageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'));self.addCleanup(self.store.close)

    def save(self, iid, rows):
        self.store.save_listings('129', iid, [dict(price=p, qty=q, shop=s, kind=k, name='오리데오콘') for p, q, s, k in rows], now=100)

    def test_threshold_boundary(self):
        self.save(1, [(100, 999999, 'S', 'sale'), (101, 999999, 'B', 'buy')])            # 차익 999,999
        self.save(2, [(100, 1000000, 'S', 'sale'), (101, 1000000, 'B', 'buy')])          # 차익 1,000,000
        got = SD.arbitrage(self.store, '129')
        self.assertEqual([(r['item_id'], r['profit']) for r in got], [(2, 1000000)])

    def test_quantities_not_double_counted(self):
        # 판매 10개(100)·5개(150), 매입 8개(300)·20개(200): 싼 판매부터 비싼 매입 순으로 한 번씩만
        self.save(3, [(100, 10, 'S1', 'sale'), (150, 5, 'S2', 'sale'), (300, 8, 'B1', 'buy'), (200, 20, 'B2', 'buy')])
        r = SD.arbitrage(self.store, '129', min_profit=1)[0]
        self.assertEqual(r['qty'], 15)                                               # 판매 수량 합계를 넘지 않음
        self.assertEqual(r['profit'], 8 * 200 + 2 * 100 + 5 * 50)
        self.assertEqual((r['sell_shops'], r['buy_shops']), (['S1', 'S2'], ['B1', 'B2']))
        self.save(4, [(100, 1, 'S', 'sale'), (100000, 1, 'B', 'buy')])
        self.store.save_listings('129', 5, [dict(price=10, qty=5, shop='S', kind='sale', name='+10인피니티 웨폰 쉐도우'),
                                            dict(price=99999999, qty=5, shop='B', kind='buy', name='+10인피니티 웨폰 쉐도우')], now=100)
        self.assertNotIn(5, [x['item_id'] for x in SD.arbitrage(self.store, '129', min_profit=1)])   # 장비(제련) 줄 제외


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'));self.addCleanup(self.store.close)
        self.now = 10000.0;self.calls = []

        def opener(url, referer):
            self.calls.append(url)
            rows = [row(24386, '+10인피니티 웨폰 쉐도우', 100 + i, 'S%d' % i, ssi=str(i)) for i in range(10)]
            rows.append(row(101863, '+10인피니티 웨폰 쉐도우 상자', 1, 'X'))
            if 'itemPriceExtendList' in url:
                return hist_page([(TODAY.isoformat(), 150, 150, 150)])
            return deal_page(rows, last=2)
        self.client = G.GnjoyClient(interval=0, opener=opener)
        self.scanner = SD.ShadowScanner(self.store, self.client, lambda: self.now)

    def test_page_cache_scope_and_rotation(self):
        r = self.scanner.fetch('129', WEAPON)
        # 공식 검색은 가격 낮은 순 — 1쪽에서 이 아이템 매물을 찾으면 그게 최저가라 더 받지 않는다(목록 1쪽 + 이력 1쪽)
        self.assertEqual(len(self.calls), 2);self.assertEqual((r['pages'], r['last'], r['early']), (1, 2, True))
        self.assertIn('itemPriceExtendList', self.calls[1]);self.assertIn('%2B10', self.calls[1])       # +10 전용 이력
        self.assertEqual(r['history'][0]['avg'], 150)
        self.assertTrue(all(l['item_id'] == 24386 for l in r['listings']))           # 부분 일치 상자 제외
        self.assertIn('%2B10', self.calls[0])                                        # '+10이름'으로 검색
        self.now += 1799;self.scanner.fetch('129', WEAPON);self.assertEqual(len(self.calls), 2)   # 30분 캐시(이력 포함)
        shield = dict(item_id=24733, name='인피니티 쉴드 쉐도우', family='인피니티')
        self.assertEqual(self.scanner.next_due('129', [WEAPON, shield])['item_id'], 24733)       # 안 본 것부터
        self.scanner.fetch('129', shield);self.assertIsNone(self.scanner.next_due('129', [WEAPON, shield]))
        self.now += 1;self.assertEqual(self.scanner.next_due('129', [WEAPON, shield])['item_id'], 24386)
        self.assertEqual(self.scanner.fetch('129', WEAPON, network=False)['pages'], 1)          # 캐시만 읽기

    def test_stop_on_match_can_be_turned_off(self):
        """전체 매물 수가 필요하면 끝까지 받는다."""
        r = self.scanner.fetch('129', WEAPON, stop_on_match=False)
        self.assertEqual((r['pages'], r['early']), (2, False))
        self.assertEqual(len(self.calls), 3)

    def test_history_is_skipped_without_sale_listings(self):
        """판매 매물이 없으면 기준가(거래 이력)를 받을 이유가 없다 — 요청을 아낀다."""
        def opener(url, referer):
            self.calls.append(url)
            if 'itemPriceExtendList' in url:
                return hist_page([(TODAY.isoformat(), 150, 150, 150)])
            return deal_page([row(24386, '+10인피니티 웨폰 쉐도우', 100, 'B', kind='buy')], last=1)
        sc = SD.ShadowScanner(self.store, G.GnjoyClient(interval=0, opener=opener), lambda: self.now)
        self.calls.clear()
        r = sc.fetch('129', WEAPON)
        self.assertEqual(len(self.calls), 1)                       # 목록만, 이력 요청 없음
        self.assertEqual(r['history'], [])
        self.assertTrue(r['listings'])                             # 구매 매물은 그대로 남는다(재료 차익용)

    def test_last_page_does_not_shrink_on_final_page(self):
        pages = {1: 3, 2: 3, 3: 2}                                                     # 3쪽 응답은 '1 2' 링크만 → 2로 읽힘

        def opener(url, referer):
            page = int(url.split('curpage=')[1].split('&')[0])
            return deal_page([row(24386, '+10인피니티 웨폰 쉐도우', 100 * page, 'S%d' % page)], last=pages[page])
        sc = SD.ShadowScanner(self.store, G.GnjoyClient(interval=0, opener=opener), lambda: self.now)
        r = sc.fetch('129', WEAPON, stop_on_match=False)
        self.assertEqual((r['pages'], r['last']), (3, 3))
        self.assertEqual(SD.evaluate({24386: r}, [WEAPON])['partial'], [])            # 다 봤는데 '뒤 쪽 남음'이라 하지 않음

    def test_default_client_is_shared_and_429_pauses_all(self):
        self.assertIs(SD.ShadowScanner(self.store).client, G.shared_client())
        # 빠르게 받되 바닥은 지킨다 — 막히면(429) 5분 전체 중지라 결국 더 느려진다
        self.assertGreaterEqual(G.shared_client().interval, G.FLOOR_INTERVAL)
        self.assertGreaterEqual(G.FLOOR_INTERVAL, 1.0)
        calls = []

        def limited(*a):
            calls.append(a);raise G.RateLimited('429')
        client = G.GnjoyClient(opener=limited);sc = SD.ShadowScanner(self.store, client, lambda: self.now)
        before = client.interval
        with self.assertRaises(G.RateLimited):sc.fetch('129', WEAPON)
        with self.assertRaises(G.RateLimited):client.listings('129', '사과')
        self.assertEqual(len(calls), 1);self.assertGreater(client.retry_after, 290)
        self.assertGreater(client.interval, before)                      # 막힌 뒤에는 더 천천히

    def test_interval_speeds_up_while_it_works_but_never_below_floor(self):
        import time as _t
        client = G.GnjoyClient(interval=2.0, opener=lambda *a: deal_page([]), floor=1.0)
        real_sleep, now = _t.sleep, [0.0]
        _t.sleep = lambda s: now.__setitem__(0, now[0] + s)              # 실제로 쉬지 않고 시계만 돌린다
        real_mono, _t.monotonic = _t.monotonic, lambda: now[0]
        try:
            for _ in range(G.SPEEDUP_AFTER * 6):
                client.get('itemDealList.asp', dict(curpage=1), 'dealSearch.asp')
        finally:
            _t.sleep, _t.monotonic = real_sleep, real_mono
        self.assertLess(client.interval, 2.0)                            # 잘 되는 동안 조금씩 당긴다
        self.assertGreaterEqual(client.interval, 1.0)                    # 바닥 아래로는 안 내려간다

    def test_interval_counts_from_request_start(self):
        """응답을 기다린 시간은 이미 쉰 것 — 거기에 간격을 또 더하지 않는다."""
        now = [100.0]
        client = G.GnjoyClient(interval=1.0, opener=lambda *a: deal_page([]))
        client._last = now[0]
        import time as _t
        slept = []
        real_sleep, real_mono = _t.sleep, _t.monotonic
        _t.sleep = lambda s: slept.append(s)
        _t.monotonic = lambda: now[0]
        try:
            client.get('itemDealList.asp', dict(curpage=1), 'dealSearch.asp')   # 바로 직전에 보냈으므로 1초 대기
            now[0] += 3.0                                                        # 응답이 3초 걸린 셈
            client.get('itemDealList.asp', dict(curpage=2), 'dealSearch.asp')    # 이미 3초 지남 → 더 안 쉰다
        finally:
            _t.sleep, _t.monotonic = real_sleep, real_mono
        self.assertEqual([round(s, 2) for s in slept], [1.0])


class ShadowAlertTest(unittest.TestCase):
    def _controller(self, tray):
        from ui.economy_controller import EconomyController

        class Settings(dict):
            def save(self):pass

            def abs_path(self, k):return self[k]
        tmp = tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store = G.VendingStore(os.path.join(tmp.name, 'v.db'))
        app = QWidget();self.addCleanup(app.close)
        app.s = Settings(db_path=os.path.join(tmp.name, 'm.db'), deal_watch={},
                         shadow_watch=dict(enabled=True, server='129', families=['인피니티'], percent=15))
        app.storage_page = QWidget(app);QVBoxLayout(app.storage_page)
        app.btn_price = QToolButton(app);app.btn_price.setMenu(QMenu(app))
        app.entries = [];app.item_db = DB;app.tray = tray;app.status = None
        app.analysis = SimpleNamespace(gamedata=None);app.replay = None
        client = G.GnjoyClient(opener=lambda *a: '')
        with patch('ui.economy_controller.BASE_DIR', tmp.name), patch('ui.economy_controller.VendingStore', return_value=self.store), \
                patch('ui.economy_controller.shared_client', return_value=client):
            c = EconomyController(app)
        c.timer.stop();self.addCleanup(c.stop)
        return app, c

    RESULT = dict(server='129', top=[dict(item_id=24386, name='인피니티 웨폰 쉐도우', price=100, base=210, discount=52.4, diff=110,
                                          shop='A', reason='동일 +10 장비의 다른 3개 상점 중앙값보다 52% 저렴', coords='', map='')])

    def test_no_duplicate_and_no_alert_after_change_or_stop(self):
        tray = MagicMock();tray.isVisible.return_value = True
        app, c = self._controller(tray)
        c._sync_shadow();c._shadow_task_gen = c.shadow_gen
        c._shadow_done(self.RESULT);c._shadow_done(self.RESULT)
        self.assertEqual(tray.showMessage.call_count, 1)                             # 같은 매물 한 번만
        self.assertIn('+10 인피니티 웨폰 쉐도우', tray.showMessage.call_args[0][1])
        other = dict(self.RESULT, top=[dict(self.RESULT['top'][0], shop='B')])
        app.s['shadow_watch'] = dict(app.s['shadow_watch'], percent=30)               # 작업 중 설정 변경
        c._shadow_done(other);self.assertEqual(tray.showMessage.call_count, 1)
        c._sync_shadow();c._shadow_task_gen = c.shadow_gen;c.stop()
        c._shadow_done(other);self.assertEqual(tray.showMessage.call_count, 1)      # 종료 뒤 알림 없음


if __name__ == '__main__':
    unittest.main()
