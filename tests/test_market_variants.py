"""장비 같은 구성 비교·장착 카드 줄·전체 시세 갱신·관심 매물 추천 표·분류 자동완성. 공식 요청은 가짜 opener."""
import helpers  # noqa: F401  (sys.path 설정)
import datetime
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from core.models import ItemEntry
from core.item_category import category
from core.storage_advice import advise, advise_variant, storage_items
from services import gnjoy_market as G
from services.market_insights import MAX_DETAILS, MarketInsights
from services.deal_watch import deal_candidates
from test_gnjoy_market import deal_page, hist_page

APP = QApplication.instance() or QApplication([])
TODAY = datetime.date.today().isoformat()


def row(iid, name, price, shop='s', ssi='1', qty=1, kind='sale'):
    return ('<tr><td>바포메트</td><td class="item"><a href="#" onclick="javascript:CallItemDealView(129,2023,\'%s\',1)">'
            '<img src="https://imgc1.gnjoy.com/games/ro1/object/201306/%d.png" alt="%s" /><span>%s</span></a></td>'
            '<td class="quantity">%d</td><td class="price"><span class="priceLv3">%s</span></td>'
            '<td class="shop %s">%s</td></tr>' % (ssi, iid, name, name, qty, format(price, ','), kind, shop))


def view(cards=(), options=(), where='prt_mk'):
    li = lambda xs: ''.join('<li>%s</li>' % x for x in xs) or '-'   # noqa: E731
    return ('<table><tr><th>수량</th><td>1</td></tr><tr><th>서버(위치)</th><td>바포메트<br />(%s)</td>'
            '<th class="shop">노점 이름<br />(판매캐릭터명)</th><td>가게 (char1)</td></tr></table>'
            '<table><tr><th>슬롯정보</th><td class="listCell"><ul>%s</ul></td><th>랜덤옵션</th><td class="listCell"><ul>%s</ul></td></tr></table>'
            % (where, li(cards), li(options)))


DB = SimpleNamespace(
    items={1: dict(name='레그', description='계열 : 신발 방어 : 10'), 4001: dict(name='블러디 카드', description='계열 : 카드'),
           4002: dict(name='다른 카드', description='계열 : 카드'), 9001: dict(name='힘1', description='계열 : 특수인챈트')},
    get=lambda i: DB.items.get(i), name=lambda i: DB.items[i]['name'], categories={})


class ParseTest(unittest.TestCase):
    def test_listing_ids_name_parts_and_detail(self):
        rows, _ = G.parse_listings(deal_page([row(22197, '+9일루시온 레그 B타입[1]', 1800000, ssi='769')]))
        self.assertEqual((rows[0]['ssi'], rows[0]['map_code']), ('769', '2023'))
        self.assertEqual(G.split_name('+9일루시온 레그 B타입[1]'), (9, '', '일루시온 레그 B타입', 1))
        self.assertEqual(G.split_name('+12 [C] 하이어레벨 글레이시아 가드'), (12, 'C', '하이어레벨 글레이시아 가드', None))
        self.assertEqual(G.split_name('사과'), (0, '', '사과', None))
        # 등급 장비 공식 표기(2026-09-30 확인): 등급이 맨 앞
        self.assertEqual(G.split_name('[UNIQUE]+12러쉬 가드[1]'), (12, 'UNIQUE', '러쉬 가드', 1))
        self.assertEqual(G.split_name('[UNIQUE]러쉬 가드'), (0, 'UNIQUE', '러쉬 가드', None))
        for name in ('[UNIQUE]+12러쉬 가드[1]', '+9일루시온 레그 B타입[1]', '사과', '+10인피니티 웨폰 쉐도우'):
            self.assertEqual(G.join_name(*G.split_name(name)), name)
        d = G.parse_deal_view(view(['블러디 카드'], ['물리 공격력 +5%']))
        self.assertEqual((d['map'], d['shop'], d['character'], d['cards'], d['options']),
                         ('prt_mk', '가게', 'char1', ['블러디 카드'], ['물리 공격력 +5%']))
        self.assertEqual(G.parse_deal_view(view())['cards'], [])                # 빈 슬롯 '-'

    def test_categories(self):
        self.assertEqual(category(DB.items[4001]), '카드')
        self.assertEqual(category(DB.items[1]), '방어구')
        self.assertEqual(category(DB.items[9001]), '인챈트')
        self.assertEqual(category(dict(description='HP를 회복한다')), '소비')


class StorageVariantTest(unittest.TestCase):
    def item(self, **kw):
        return dict(dict(item_id=1, name='레그', quantity=1, variant=True, refine=9, card_names=[], options=[]), **kw)

    def test_socketed_cards_are_separate_reference_rows(self):
        e = ItemEntry('창고1', 1, '레그', 2, refine=9, cards=[4001, 9001, 0], is_equip=True)
        rows, _ = storage_items([e, ItemEntry('창고1', 4001, '블러디 카드', 1)], DB)
        socketed = [r for r in rows if r.get('socketed')]
        self.assertEqual([(r['name'], r['quantity'], r['host']) for r in socketed], [('블러디 카드', 2, '+9 레그')])  # 인챈트 줄 없음
        self.assertEqual(rows[0]['card_names'], ['블러디 카드', '힘1'])
        hist = [dict(day=TODAY, min=100, avg=100, max=100)] * 3
        r = advise(socketed[0], hist, [dict(price=100, kind='sale', qty=1, shop='a')], fetched_at=time.time(), history_at=time.time())
        self.assertEqual(r['recommendation'], '참고');self.assertIsNone(r['net'])          # 장비 값과 이중 계산 안 함
        self.assertEqual(r['recommended'], 99)

    def test_same_refine_same_cards_is_reference_price(self):
        now = time.time()
        ls = [dict(price=1000000, kind='sale', listing_name='+7레그[1]', ssi='a'),
              dict(price=2000000, kind='sale', listing_name='+9레그[1]', ssi='b'),
              dict(price=2500000, kind='sale', listing_name='+9레그[1]', ssi='c')]
        item = self.item(card_names=['블러디 카드'])
        before = advise_variant(item, ls, {}, fetched_at=now, now=now)
        self.assertIn('확인 전', before['reason']);self.assertEqual(before['now_min'], 2000000);self.assertIsNone(before['net'])
        details = {'b': dict(cards=['다른 카드'], options=[]), 'c': dict(cards=['블러디 카드'], options=[])}
        r = advise_variant(item, ls, details, fetched_at=now, now=now)
        self.assertEqual(r['recommendation'], '참고가');self.assertEqual(r['now_min'], 2500000)   # +7·다른 카드는 제외
        self.assertEqual(r['recommended'], 2470000);self.assertEqual(r['net'], 2470000)
        diff = advise_variant(item, ls, {'b': details['b'], 'c': dict(cards=['다른 카드'], options=[])}, fetched_at=now, now=now)
        self.assertEqual(diff['recommendation'], '보류');self.assertIn('모두 카드·옵션 다름', diff['reason'])

    def test_card_prefix_name_is_different_without_detail(self):
        now = time.time()
        ls = [dict(price=100, kind='sale', listing_name='+9마술의레그', ssi='p'), dict(price=300, kind='sale', listing_name='+9레그', ssi='q')]
        r = advise_variant(self.item(), ls, {}, fetched_at=now, now=now)
        self.assertEqual((r['recommendation'], r['identical_count']), ('참고가', 1));self.assertIn('최저 300', r['reason'])

    def test_options_slotless_crowded_and_stale(self):
        now = time.time()
        ls = [dict(price=500000, kind='sale', listing_name='+9레그', ssi='x')]
        self.assertEqual(advise_variant(self.item(), ls, {}, fetched_at=now, now=now)['recommendation'], '참고가')  # 슬롯 없음
        opt = advise_variant(self.item(options=[dict(id=1, value=5)]), ls, {'x': dict(cards=[], options=['a'])}, fetched_at=now, now=now)
        self.assertIn('랜덤옵션이 있어', opt['reason']);self.assertIsNone(opt['recommended'])
        crowded = advise_variant(self.item(refine=12), ls, {}, fetched_at=now, now=now, listing_more=True)
        self.assertIn('확인 불가', crowded['reason'])
        self.assertIn('판매 매물 없음', advise_variant(self.item(refine=12), ls, {}, fetched_at=now, now=now)['reason'])
        stale = advise_variant(self.item(), ls, {}, fetched_at=now - 1800, now=now)
        self.assertEqual(stale['recommendation'], '보류');self.assertIn('오래된 자료', stale['reason'])
        self.assertIn('아직 받지 않음', advise_variant(self.item(), [], {}, fetched_at=None, now=now)['reason'])


class FetchVariantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'));self.addCleanup(self.store.close)
        self.calls = [];self.now = 1000.0

        def opener(url, referer):
            self.calls.append(url)
            if 'itemDealView' in url:return view(['블러디 카드'])
            if 'itemDealList' in url:
                rows = [row(1, '+9레그[1]', 1000 + i, ssi=str(i)) for i in range(8)] + [row(1, '+7레그[1]', 10, ssi='z')]
                return deal_page(rows, last=1)
            return hist_page([(TODAY, 100, 100, 100)])
        self.market = MarketInsights(self.store, G.GnjoyClient(interval=0, opener=opener), lambda: self.now)

    def test_details_limited_to_cheapest_same_refine_and_cached(self):
        item = dict(item_id=1, name='레그', refine=9, card_names=['블러디 카드'], options=[])
        data = self.market.fetch_variant('129', item)
        views = [c for c in self.calls if 'itemDealView' in c]
        self.assertEqual(len(views), MAX_DETAILS)                              # 같은 제련 중 싼 순 5건만
        self.assertNotIn("ssi=z", ''.join(views))                               # +7 은 상세를 받지 않음
        self.assertEqual(len(self.market.details('129', data['listings'])), MAX_DETAILS)
        n = len(self.calls);self.market.fetch_variant('129', item);self.assertEqual(len(self.calls), n)   # 캐시 재사용
        names = {l['listing_name'] for l in self.store.latest_listings('129', 1)[1]}
        self.assertEqual(names, {'+9레그[1]', '+7레그[1]'})                       # 목록 이름(제련) 저장


class DealCandidatesAndDialogTest(unittest.TestCase):
    def test_candidate_states_and_dialog(self):
        with tempfile.TemporaryDirectory() as d:
            store = G.VendingStore(os.path.join(d, 'v.db'))
            try:
                now = time.time()
                store.save_history('129', 1, [dict(day=TODAY, min=100, avg=100, max=100)], now=now)
                store.save_listings('129', 1, [dict(price=80, qty=1, shop='싼곳', kind='sale', name='+9레그[1]')], now=now)
                store.save_history('129', 2, [dict(day=TODAY, min=100, avg=100, max=100)], now=now)
                store.save_listings('129', 2, [dict(price=95, qty=1, shop='보통', kind='sale')], now=now)
                items = [dict(item_id=2, name='b', percent=15), dict(item_id=1, name='a', percent=15), dict(item_id=3, name='c', percent=15)]
                rows = deal_candidates(store, '129', items)
                self.assertEqual([(r['item_id'], r['state']) for r in rows], [(1, '추천'), (2, '기준 미달'), (3, '아직 안 받음')])
                self.assertEqual((rows[0]['listing_name'], round(rows[0]['discount'])), ('+9레그[1]', 20))
                from ui.deal_watch_dialog import DealWatchDialog
                db = SimpleNamespace(items={1: dict(name='a레그', description='계열 : 신발'), 2: dict(name='b카드', description='계열 : 카드')},
                                     categories={})
                settings = dict(deal_watch=dict(enabled=True, server='129', items=[dict(item_id=1, name='a레그', percent=15)]))
                dlg = DealWatchDialog(None, settings, db, store);self.addCleanup(dlg.close)
                dlg.category.setCurrentText('카드')
                self.assertEqual(dlg.model.stringList(), ['b카드 · 2'])                 # 분류로 자동완성 목록이 줄어듦
                dlg.search.setText('카드');dlg._add()                                  # 이름 일부 → 하나뿐이면 등록
                self.assertEqual([i['item_id'] for i in dlg._items()], [1, 2])
                self.assertEqual(dlg.deals.row_at(dlg.deals.proxy.index(0, 0))['state'], '추천')
            finally:
                store.close()


class StorageFetchAllTest(unittest.TestCase):
    def test_fetch_everything_updates_progress_and_table(self):
        from ui.storage_advice_dialog import StorageAdviceDialog
        with tempfile.TemporaryDirectory() as d:
            store = G.VendingStore(os.path.join(d, 'v.db'))
            calls = []

            def opener(url, referer):
                calls.append(url)
                if 'itemDealList' in url:return deal_page([row(4001, '블러디 카드', 900, ssi='k')])
                return hist_page([(TODAY, 1000, 1000, 1000)] * 3)
            try:
                entries = [ItemEntry('창고1', 4001, '블러디 카드', 1), ItemEntry('창고1', 4002, '다른 카드', 1)]
                dlg = StorageAdviceDialog(None, entries, DB, store, G.GnjoyClient(interval=0, opener=opener))
                dlg._fetch_everything();t = time.time()
                while dlg.task.future is not None and time.time() - t < 10:
                    APP.processEvents();time.sleep(0.05)
                APP.processEvents()
                self.assertEqual(dlg.progress[:2], [2, 2])
                got = {r['item_id']: r for r in dlg._rows}
                self.assertEqual(got[4001]['now_min'], 900);self.assertIn('이번에 2/2종', dlg.status.text())
                n = len(calls);dlg._fetch_everything()
                while dlg.task.future is not None:APP.processEvents();time.sleep(0.05)
                self.assertEqual(len(calls), n)                                        # 30분 안에는 다시 요청 안 함
                dlg.close()
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
