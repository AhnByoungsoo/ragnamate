import helpers
import datetime,unittest
from types import SimpleNamespace
from core.models import ItemEntry
from core.storage_advice import *

TODAY=datetime.date(2026,9,27)
def hist(count=3,avg=100):return [dict(day=(TODAY-datetime.timedelta(days=i)).isoformat(),min=avg,avg=avg,max=avg) for i in range(count)]
def listing(price):return [dict(price=price,kind='sale',qty=1,shop='a')]
def item(qty=1):return dict(item_id=1,name='a',quantity=qty,variant=False)
def advice(price,count=3):return advise(item(),hist(count),listing(price),TODAY,10000,10000)

class StorageAdviceTest(unittest.TestCase):
    def test_thresholds_are_inclusive(self):
        self.assertEqual(advice(110)['recommendation'],'지금 팔기')
        self.assertEqual(advice(109)['recommendation'],'보류')
        self.assertEqual(advice(85)['recommendation'],'팔지 말기')
        self.assertEqual(advice(86)['recommendation'],'보류')
    def test_sparse_priority_and_old_future_dates(self):
        self.assertEqual(advice(150,2)['recommendation'],'보류')
        self.assertIn('거래 드묾',advice(150,2)['reason'])
        h=hist(2)+[dict(day='2026-08-27',avg=100,min=100,max=100),dict(day='2026-10-01',avg=10000,min=1,max=10000)]
        r=advise(item(),h,listing(110),TODAY,10000,10000)
        self.assertEqual(r['recommendation'],'보류');self.assertEqual(r['avg7'],100)
    def test_tax_is_per_unit_and_amount_sorting(self):
        r=advise(item(2),hist(avg=20000000),listing(20000000),TODAY,10000,10000)
        self.assertEqual(r['recommended'],19800000);self.assertEqual(r['expected'],39600000)
        self.assertEqual(r['net'],37620000)
        cheap=advise(item(2),hist(avg=10000000),listing(10000000),TODAY,10000,10000)
        self.assertEqual(cheap['net'],cheap['expected'])
        self.assertEqual(sorted_advice([cheap,r])[0],r)
    def test_no_price_stale_and_variants(self):
        r=advise(item(),[],[],TODAY,10000,10000);self.assertIsNone(r['net'])
        self.assertEqual(advise(item(),hist(),listing(110),TODAY,1,10000)['recommendation'],'보류')
        self.assertEqual(advise(dict(item(),variant=True),hist(),listing(110),TODAY,10000,10000)['recommendation'],'보류')
    def test_bound_exclusion_and_quantity(self):
        db=SimpleNamespace(get=lambda i:dict(description='^FF0000거래 불가' if i==2 else '일반'))
        es=[ItemEntry('창고1',1,'a',2),ItemEntry('창고2',1,'a',3),ItemEntry('카트',1,'a',100),ItemEntry('창고1',2,'b',1)]
        rows,excluded=storage_items(es,db)
        self.assertEqual((len(rows),rows[0]['quantity'],excluded),(1,5,1))

    def test_data_states_are_distinguished(self):
        never=advise(item(),[],[],TODAY,None,10000,None)
        self.assertIn('아직 받지 않음',never['reason'])
        empty=advise(item(),[],[],TODAY,10000,10000,10000)
        self.assertIn('거래 기록 없음',empty['reason'])
        stale=advise(item(),hist(),listing(110),TODAY,10000,10000+1800,10000)
        self.assertEqual(stale['recommendation'],'보류');self.assertIn('오래된 자료(현재 매물 30분 전',stale['reason'])
        fresh=advise(item(),hist(),listing(110),TODAY,10000,10000+1799,10000)
        self.assertEqual(fresh['recommendation'],'지금 팔기')
        unknown=advise(item(),hist(),[],TODAY,10000,10000,10000,listing_unknown=True)
        self.assertIn('확인 불가',unknown['reason']);self.assertEqual(unknown['recommendation'],'보류')
    def test_own_listing_excluded_only_when_shop_confirmed(self):
        ls=[dict(price=110,kind='sale',qty=1,shop='내가게'),dict(price=120,kind='sale',qty=1,shop='남')]
        mine=advise(item(),hist(),ls,TODAY,10000,10000,10000,own=[dict(price=110,shop='내가게')])
        self.assertEqual(mine['now_min'],120)                                  # 내 노점 110 은 뺌
        maybe=advise(item(),hist(),ls,TODAY,10000,10000,10000,own=[dict(price=110,shop='')])
        self.assertEqual(maybe['now_min'],110);self.assertIn('상점명 미확인',maybe['reason'])
    def test_three_records_boundary_and_tax_per_unit_not_total(self):
        self.assertEqual(advice(110,3)['recommendation'],'지금 팔기')
        self.assertEqual(advice(110,2)['recommendation'],'보류')
        r=advise(item(3),hist(avg=5000000),listing(5000000),TODAY,10000,10000)   # 개당 500만 × 3 = 1,500만
        self.assertEqual(r['net'],r['expected'])                                # 총액이 1천만을 넘어도 세금 없음

if __name__=='__main__':unittest.main()
