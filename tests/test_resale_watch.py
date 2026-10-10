"""고가 매물 기준/예상 차익/캐시/중복 알림. 네트워크는 가짜 opener만 사용."""
import helpers  # noqa
import copy
import datetime
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services import gnjoy_market as G, resale_watch as R, shadow_deals as SD
from test_shadow_deals import row as html_row
from test_gnjoy_market import hist_page

TODAY = datetime.date(2026, 9, 30)
NOW = datetime.datetime(2026, 9, 30, 12).timestamp()
TARGET = dict(item_id=24386, name='인피니티 웨폰 쉐도우', query='+10인피니티 웨폰 쉐도우')
DB = SimpleNamespace(items={24386:dict(name=TARGET['name'], description='계열 : 쉐도우 장비')}, categories={})


def listing(price=80_000_000, shop='떨이점', **kw):
    return dict(dict(item_id=24386, name=TARGET['query'], kind='sale', price=price, qty=1, shop=shop, ssi=shop), **kw)


def snapshot(base=100_000_000, **kw):
    return dict(dict(listings=[listing(), listing(105_000_000, '비교점A'), listing(110_000_000, '비교점B')],
                fetched_at=NOW, history_at=NOW, pages=1, last=1,
                history=[dict(day=(TODAY-datetime.timedelta(days=i)).isoformat(), avg=base) for i in range(3)]), **kw)


def evaluate(snap=None, **kw):
    return R.evaluate({TARGET['query']:snap or snapshot()}, [TARGET], R.config(), now=NOW, today=TODAY, **kw)


class EvaluationTest(unittest.TestCase):
    def test_inclusive_reference_and_ratio_threshold_and_tax(self):
        r=evaluate()['rows'][0]
        self.assertTrue(r['eligible']); self.assertEqual(r['base'], 100_000_000)
        self.assertEqual((r['resale'], r['net'], r['profit']), (95_000_000, 90_250_000, 10_250_000))
        self.assertTrue(r['lowest'])
        snap=snapshot(); snap['listings'][0]['price']+=1
        self.assertEqual(evaluate(snap)['rows'], [])
        self.assertEqual(evaluate(snapshot(base=99_999_999))['rows'], [])

    def test_purchase_under_one_hundred_million_still_qualifies(self):
        r=evaluate()['rows'][0]
        self.assertLess(r['price'], 100_000_000); self.assertTrue(r['eligible'])

    def test_stale_history_or_listings_never_alert(self):
        for field in ('history_at','fetched_at'):
            snap=snapshot(**{field:NOW-1800})
            self.assertFalse(evaluate(snap)['rows'][0]['eligible'])
            snap[field]=NOW+1
            self.assertFalse(evaluate(snap)['rows'][0]['eligible'])

    def test_days_are_unique_not_trade_count_and_exclude_future(self):
        snap=snapshot(); snap['history']=[snap['history'][0]]*4
        self.assertEqual(evaluate(snap)['rows'], [])
        h=snapshot()['history']+[dict(day='2026-10-01',avg=999_000_000),dict(day='2026-09-23',avg=999_000_000)]
        self.assertEqual(R.base_price(h,TODAY),(100_000_000,3))

    def test_distinct_peer_shops_and_no_mutation(self):
        snap=snapshot(); snap['listings'][2]['shop']='비교점A'; original=copy.deepcopy(snap)
        row=evaluate(snap)['rows'][0]
        self.assertFalse(row['eligible']); self.assertIn('다른 상점 2곳 미만',row['reason'])
        self.assertEqual(snap,original)

    def test_peer_floor_prevents_artificial_profit(self):
        snap=snapshot(); snap['listings'][1]['price']=75_000_000
        row=next(r for r in evaluate(snap)['rows'] if r['shop']=='떨이점')
        self.assertFalse(row['eligible']); self.assertLess(row['profit'],0)
        self.assertFalse(row['lowest'])

    def test_wrong_refine_grade_slot_or_item_id_not_used(self):
        for extra in (dict(name='+9인피니티 웨폰 쉐도우'), dict(name='+10[A]인피니티 웨폰 쉐도우'),
                      dict(name='+10인피니티 웨폰 쉐도우[1]'),dict(item_id=10)):
            snap=snapshot(); snap['listings'][0].update(extra)
            self.assertEqual(evaluate(snap)['rows'],[])

    def test_buy_orders_and_zero_price_or_qty_excluded(self):
        for extra in (dict(kind='buy'),dict(price=0),dict(qty=0)):
            snap=snapshot(); snap['listings'][0].update(extra)
            self.assertEqual(evaluate(snap)['rows'],[])

    def test_partial_or_ambiguous_query_is_review_only(self):
        for extra in (dict(last=2),dict(ambiguous=True)):
            self.assertFalse(evaluate(snapshot(**extra))['rows'][0]['eligible'])

    def test_own_shop_and_server_are_respected(self):
        own=[dict(item_id=24386,shop='떨이점',price=80_000_000,server='129')]
        self.assertEqual(evaluate(own=own)['rows'],[])
        own[0]['server']='229'
        self.assertTrue(evaluate(own=own)['rows'][0]['eligible'])
        own[0].update(server='129',shop='')
        self.assertFalse(evaluate(own=own)['rows'][0]['eligible'])

    def test_profit_is_per_item_not_inflated_by_quantity(self):
        snap=snapshot(); snap['listings'][0]['qty']=9
        self.assertEqual(evaluate(snap)['rows'][0]['profit'],10_250_000)

    def test_canonical_query_targets_and_default_config_independence(self):
        t=R.target_from_query(DB,'+10 인피니티 웨폰 쉐도우')
        self.assertEqual(t['query'],TARGET['query'])
        cfg=R.config(); cfg['targets'].append(t)
        self.assertEqual(len(R.targets_for(DB,cfg)),1)
        self.assertEqual(R.config()['targets'],[])
        self.assertEqual(R.config(dict(min_base='bad',max_ratio=99))['max_ratio'],80)
        with self.assertRaises(ValueError): R.target_from_query(DB,'인피니티')


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=G.VendingStore(str(Path(self.tmp.name)/'v.db')); self.addCleanup(self.store.close)
        self.calls=[]
        def opener(url,referer):
            self.calls.append(url)
            if 'itemDealList' in url:
                return '<tbody>'+html_row(24386,TARGET['query'],80_000_000,'매장')+'</tbody>'
            return hist_page([('2026.09.30',100_000_000,100_000_000,100_000_000)])
        self.client=G.GnjoyClient(interval=0,opener=opener)
        self.clock=NOW
        self.scanner=R.ResaleScanner(self.store,self.client,clock=lambda:self.clock)

    def test_opener_cache_and_no_network_for_cached_view(self):
        self.scanner.fetch('129',TARGET); self.assertEqual(len(self.calls),2)
        self.scanner.fetch('129',TARGET); self.scanner.fetch('129',TARGET,network=False)
        self.assertEqual(len(self.calls),2)
        self.assertIsNone(self.scanner.next_due('129',[TARGET]))
        self.clock+=1800
        self.assertEqual(self.scanner.next_due('129',[TARGET]),TARGET)
        self.scanner.fetch('129',TARGET); self.assertEqual(len(self.calls),4)

    def test_shared_shadow_cache_and_concurrent_requests(self):
        shadow=SD.ShadowScanner(self.store,self.client,clock=lambda:self.clock)
        t=dict(TARGET,family='인피니티')
        with ThreadPoolExecutor(2) as pool:
            jobs=[pool.submit(shadow.fetch,'129',t),pool.submit(self.scanner.fetch,'129',TARGET)]
            for job in jobs:job.result()
        self.assertEqual(len(self.calls),2)

    def test_rate_limit_pauses_shared_client_for_five_minutes(self):
        self.client._open=lambda *args:(_ for _ in ()).throw(G.RateLimited('429'))
        with self.assertRaises(G.RateLimited): self.scanner.fetch('129',TARGET)
        self.assertGreater(self.client.retry_after,299)
        before=self.client.requests
        with self.assertRaises(G.RateLimited): self.client.history('129',TARGET['query'])
        self.assertEqual(self.client.requests,before)

    def test_cancelled_scan_and_item_interval(self):
        event=threading.Event();event.set()
        self.scanner.scan('129',[TARGET],R.config(),stop=event)
        self.assertEqual(self.calls,[])
        self.store.set_meta(self.scanner.checked_key('129',TARGET),NOW)
        self.assertIsNone(self.scanner.next_due('129',[TARGET]))

    def test_seen_survives_new_instance_and_distinguishes_qty_server_refine(self):
        r=evaluate()['rows'][0]
        self.assertFalse(self.scanner.seen(r)); self.scanner.mark_seen(r)
        other=R.ResaleScanner(self.store,self.client)
        self.assertTrue(other.seen(r))
        for delta in (dict(qty=2),dict(server='229'),dict(name='+9인피니티 웨폰 쉐도우'),dict(price=79_000_000)):
            self.assertFalse(other.seen(dict(r,**delta)))


if __name__=='__main__': unittest.main()
