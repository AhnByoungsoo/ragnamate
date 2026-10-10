import helpers
import datetime,os,tempfile,unittest
from unittest.mock import patch
from test_gnjoy_market import deal_page,deal_row,hist_page
from services import gnjoy_market as G
from services.market_insights import MarketInsights
from services.deal_watch import DealWatch,validate_watchlist,alert_text

TODAY=datetime.date(2026,9,27)
class DealWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=G.VendingStore(os.path.join(self.tmp.name,'v.db'));self.addCleanup(self.store.close)
        self.now=100000;self.calls=[]
        self.price=85;self.shop='특가';self.qty=2
        self.views=[]
        def opener(url,referer):
            if 'itemDealView' in url:
                self.views.append(url)
                return '<th>서버(위치)</th><td>바포메트<br />(prt_mk)</td><th>슬롯정보</th><td class="listCell"><ul><li>블러디 카드</li></ul></td>'
            self.calls.append(url)
            if 'itemDealList' in url:return deal_page([deal_row(1,'a',self.qty,self.price,self.shop,'sale'),deal_row(2,'other',1,1,'x','sale')])
            return hist_page([('2026-09-27',100,100,100),('2026-09-26',100,100,100),('2026-09-25',100,100,100)])
        self.client=G.GnjoyClient(interval=0,opener=opener)
        self.watch=DealWatch(self.store,self.client,clock=lambda:self.now)
        self.items=[dict(item_id=1,name='a',percent=15)]
    def check(self,**kw):return self.watch.check('129',self.items,today=TODAY,**kw)
    def test_threshold_minimum_interval_and_persisted_dedup(self):
        alerts=self.check();self.assertEqual(len(alerts),1);self.assertEqual(alerts[0]['discount'],15)
        self.assertIn('특가',alert_text(alerts[0]))
        self.assertTrue(self.watch.mark_seen(alerts[0]))          # 전달한 뒤 기록(화면 쪽에서 부름)
        self.check();self.assertEqual(len(self.calls),2)
        self.now+=599;self.check();self.assertEqual(len(self.calls),2)
        self.now+=1;self.assertEqual(self.check(),[]);self.assertEqual(len(self.calls),3)
        again=DealWatch(self.store,self.client,clock=lambda:self.now)
        self.now+=600;self.assertEqual(again.check('129',self.items,today=TODAY),[])
        self.qty=3;self.now+=600;self.assertEqual(len(self.check()),1)

    def test_detail_location_and_cards_once_per_listing(self):
        a=self.check()[0]
        self.assertEqual(a['location'],'prt_mk');self.assertEqual(a['cards'],['블러디 카드'])
        text=alert_text(a);self.assertIn('위치: prt_mk',text);self.assertIn('카드: 블러디 카드',text)
        self.now+=600;self.check();self.assertEqual(len(self.views),1)            # 같은 매물은 상세를 다시 받지 않음
        self.assertTrue(self.watch.mark_seen(a))
        from services.deal_watch import recent_alerts
        r=recent_alerts(self.store,'129');self.assertEqual((r[0]['price'],r[0]['shop'],round(r[0]['discount'])),(85,'특가',15))
    def test_not_recorded_until_delivered(self):
        """전달 전에 끄거나 앱이 닫혀도 매물을 잃지 않는다: check 는 기록하지 않고, 기록 전이면 다음 확인에 다시 나온다."""
        first=self.check();self.assertEqual(len(first),1)
        self.now+=600;again=self.check();self.assertEqual(len(again),1)    # 아직 전달 안 함 → 다시 후보
        self.assertTrue(self.watch.mark_seen(again[0]));self.assertFalse(self.watch.mark_seen(again[0]))
        self.now+=600;self.assertEqual(self.check(),[])

    def test_own_unknown_shop_is_flagged_not_dropped(self):
        own=[dict(item_id=1,price=85,shop='',server='129')]             # 내 상점명을 못 읽음
        alerts=self.check(own=own)
        self.assertEqual(len(alerts),1);self.assertTrue(alerts[0]['maybe_own'])
        self.assertIn('확인 못 함',alert_text(alerts[0]))
        self.now+=600;own[0]['shop']='다른가게'                            # 상점명이 다르면 내 것 아님
        a=self.check(own=own);self.assertEqual(len(a),1);self.assertFalse(a[0]['maybe_own'])

    def test_crowded_search_is_unknown_not_empty(self):
        """공식 검색은 '포함' 일치·가격순: 더 싼 다른 아이템이 2쪽을 채우고 뒤 쪽이 남으면 '노점 없음'으로 저장하지 않는다."""
        calls=[]
        def crowded(url,referer):
            calls.append(url)
            if 'itemDealList' in url:return deal_page([deal_row(2,'a2',1,10,'x','sale')]*10,last=5)
            return hist_page([('2026-09-27',100,100,100)])
        market=MarketInsights(self.store,G.GnjoyClient(interval=0,opener=crowded),lambda:self.now)
        data=market.fetch('129',1,'a')
        self.assertTrue(data['listing_unknown']);self.assertEqual(data['listings'],[])
        self.assertIsNone(self.store.latest_listings('129',1)[0])               # '노점 없음' 기록이 생기지 않음
        n=len(calls);market.fetch('129',1,'a');self.assertEqual(len(calls),n)   # 그래도 30분 안에는 다시 요청 안 함
        self.now+=1800;market.fetch('129',1,'a');self.assertGreater(len(calls),n)

    def test_stop_between_pages_saves_nothing_partial(self):
        import threading
        stop=threading.Event();calls=[]
        def paged(url,referer):
            calls.append(url);stop.set()                                         # 첫 쪽을 받는 사이 창을 닫음
            return deal_page([deal_row(2,'a2',1,10,'x','sale')]*10,last=3)
        market=MarketInsights(self.store,G.GnjoyClient(interval=0,opener=paged),lambda:self.now)
        market.fetch('129',1,'a',stop=stop)
        self.assertEqual(len(calls),1)                                           # 2쪽·과거 기록은 요청 안 함
        self.assertIsNone(self.store.latest_listings('129',1)[0])
        self.assertIsNone(self.store.meta('listing_unknown:129:1'))
    def test_same_item_fetched_once_when_two_workers_race(self):
        import threading,time as _t
        calls=[]
        def slow(url,referer):
            calls.append(url);_t.sleep(0.05)
            if 'itemDealList' in url:return deal_page([deal_row(1,'a',1,90,'s','sale')])
            return hist_page([('2026-09-27',100,100,100)])
        market=MarketInsights(self.store,G.GnjoyClient(interval=0,opener=slow),lambda:self.now)
        ts=[threading.Thread(target=market.fetch,args=('129',1,'a')) for _ in range(3)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(len(calls),2)                                          # 목록 1 + 기록 1 — 캐시 확인과 요청이 한 번만
    def test_own_listing_excluded_and_other_server_not_excluded(self):
        own=[dict(item_id=1,price=85,shop='특가',server='129')]
        self.assertEqual(self.check(own=own),[])
        self.now+=600;own[0]['server']='229';self.assertEqual(len(self.check(own=own)),1)
    def test_above_threshold_no_average_no_alert(self):
        self.price=86;self.assertEqual(self.check(),[])
        self.store.conn.execute('DELETE FROM history');self.store.conn.commit()
        self.price=1;self.now+=600;self.assertEqual(self.check(),[])
    def test_cache_empty_history_is_fresh_and_refresh_after_30_minutes(self):
        self.store.save_history('129',1,[],now=self.now)
        self.store.save_listings('129',1,[],now=self.now)
        market=MarketInsights(self.store,self.client,lambda:self.now)
        market.fetch('129',1,'a');self.assertEqual(self.calls,[])
        self.now+=1800;market.fetch('129',1,'a');self.assertEqual(len(self.calls),2)
    def test_watchlist_limits_and_duplicates(self):
        with self.assertRaises(ValueError):validate_watchlist([dict(item_id=i+1,name='a') for i in range(21)])
        with self.assertRaises(ValueError):validate_watchlist(self.items*2)
        with self.assertRaises(ValueError):validate_watchlist([dict(item_id=1,name='a',percent=100)])
    def test_shared_429_cooldown_no_network_during_pause(self):
        calls=[]
        def limited(*args):calls.append(args);raise G.RateLimited('429')
        client=G.GnjoyClient(opener=limited)
        with patch.object(G.time,'monotonic',return_value=100):
            with self.assertRaises(G.RateLimited):client.listings('129','a')
            with self.assertRaises(G.RateLimited):client.history('229','b')
            self.assertEqual(client.retry_after,300);self.assertEqual(len(calls),1)
        with patch.object(G.time,'monotonic',return_value=400):
            with self.assertRaises(G.RateLimited):client.listings('129','a')
            self.assertEqual(len(calls),2)
    def test_shared_default_interval(self):
        self.assertGreaterEqual(G.shared_client().interval,G.FLOOR_INTERVAL)   # 바닥 아래로는 보내지 않는다
    def test_location_survives_cache(self):
        self.store.save_listings('129',1,[dict(price=85,qty=1,shop='a',kind='sale',location='프론테라 100,100')],now=self.now)
        self.store.save_history('129',1,[dict(day='2026-09-27',min=100,avg=100,max=100)],now=self.now)
        alerts=self.check();self.assertIn('프론테라',alert_text(alerts[0]))

class DealWatchSettingsTest(unittest.TestCase):
    def test_watchlist_survives_restart(self):
        """설정 파일은 DEFAULTS 에 있는 키만 읽는다 — deal_watch 가 빠지면 다시 켤 때 목록이 사라졌다."""
        from config.settings import Settings
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'settings.json');s=Settings(path)
            s['deal_watch']=dict(enabled=True,server='229',items=[dict(item_id=1,name='a',percent=20.0)]);s.save()
            again=Settings(path)
            self.assertEqual(again['deal_watch']['server'],'229');self.assertTrue(again['deal_watch']['enabled'])
            self.assertEqual(again['deal_watch']['items'][0]['item_id'],1)

if __name__=='__main__':unittest.main()
