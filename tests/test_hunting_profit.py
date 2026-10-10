import helpers
import json,tempfile,unittest,zlib,sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from core.hunting_profit import report_sample,calculate_maps
from services.hunting_profit import HuntingProfitService
from services.gnjoy_market import VendingStore
from database.database import Database
from database.history import HistoryRepo

DROPS={'1001':dict(drops=[dict(item_id=1,probability=.5),dict(item_id=2,probability=.01)])}
def report(map_id='map_a',loot=None,method='ITEM_GAIN 이벤트'):
    return dict(meta=dict(server='바포메트'),window=dict(map=map_id,duration_ms=3600000,start_ms=0,end_ms=3600000),
        segments=[dict(map=map_id,start_ms=0,end_ms=3600000,is_hunt=True)],
        kpi=dict(kills=100),loot=dict(available=loot is not None,loot=loot or [],method=method),
        monsters=dict(monsters=[dict(class_id=1001,kills=100)]))
def repo_report():
    r=report(loot=[dict(item_id=1,quantity=2)])
    r['meta'].update(replay_file='a.rrf',character='a',job='a',job_id=1,level=1,start_time='2026-09-27T00:00:00',duration_ms=3600000)
    r['window']['label']='사냥'
    r['combat']=dict(available=False,dps_combat=None,dps_total=None,max_hit=None,burst={})
    r['exp']={};r['skills']=dict(skills=[])
    for k in ('base_exp_per_hour','zeny_net','loot_value','consumable_cost','profit','profit_per_hour'):r['kpi'][k]=None
    return r

class HuntingProfitTest(unittest.TestCase):
    def test_actual_priority_and_current_price(self):
        actual=report_sample(report(loot=[dict(item_id=1,quantity=3)]),DROPS)
        estimate=report_sample(report(),DROPS)
        rows=calculate_maps([estimate,actual],{1:100},str)
        self.assertEqual(rows[0]['per_hour'],300);self.assertEqual(rows[0]['basis'],'실제 획득 기록')
        self.assertEqual(rows[0]['hours'],1)
    def test_drop_expected_quantity_and_missing_price(self):
        sample=report_sample(report(),DROPS)
        rows=calculate_maps([sample],{1:100},str)
        self.assertEqual(rows[0]['per_hour'],5000);self.assertEqual(rows[0]['unpriced'],['2'])
        self.assertEqual(rows[0]['basis'],'드랍표 기반 추정')
        self.assertIsNone(calculate_maps([sample],{},str)[0]['per_hour'])
    def test_inventory_change_not_labelled_actual(self):
        sample=report_sample(report(loot=[dict(item_id=1,quantity=2)],method='인벤토리 비교'),DROPS)
        self.assertEqual(sample['basis'],'인벤토리 변화 기준')
    def test_multimap_nonhunt_zero_time_and_no_drop_rejected(self):
        self.assertIsNone(report_sample(report('map_a, map_b'),DROPS))
        r=report();r['segments'][0]['is_hunt']=False;self.assertIsNone(report_sample(r,DROPS))
        r=report();r['window']['duration_ms']=0;self.assertIsNone(report_sample(r,DROPS))
        self.assertIsNone(report_sample(report(),{}))
    def test_whole_file_report_with_none_window_bounds(self):
        r=report();r['window'].update(start_ms=None,end_ms=None)             # 전체 구간 Report 모양
        self.assertEqual(report_sample(r,DROPS)['ms'],3600000)
        inv=report_sample(report(loot=[dict(item_id=1,quantity=2)],method='인벤토리 비교 00:01 → 00:59'),DROPS)
        self.assertIn('창고 출납',inv['note'])                                # 인벤토리 비교는 추정이라고 밝힘
    def test_rates_use_total_time_not_mean_of_per_hour(self):
        a=report_sample(report(loot=[dict(item_id=1,quantity=10)]),DROPS)
        b=dict(a,ms=7200000,quantities={1:2})
        self.assertEqual(calculate_maps([a,b],{1:100},str)[0]['per_hour'],400)
    def test_unpriced_maps_are_last_not_zero(self):
        a=report_sample(report(loot=[dict(item_id=1,quantity=10)]),DROPS)
        b=dict(a,map='other',quantities={2:10000})
        rows=calculate_maps([b,a],{1:100},str)
        self.assertEqual(rows[0]['map'],'map_a');self.assertIsNone(rows[1]['per_hour'])
    def test_saved_per_segment_reports_update_and_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'market.db';db=Database(str(path));repo=HistoryRepo(db)
            store=VendingStore(str(Path(folder)/'vending.db'))
            try:
                r=repo_report();seg=dict(index=0,map='map_a',start_ms=0,end_ms=3600000,is_hunt=True)
                sid=repo.save(r,'hash',[(seg,r)]);repo.save(r,'hash',[(seg,r)])
                self.assertEqual(db._query('SELECT COUNT(*) FROM hunting_window_reports')[0][0],1)
                store.save_listings('129',1,[dict(price=100,qty=1,shop='a',kind='sale')],now=100)
                drops=Path(folder)/'drops.json';drops.write_text(json.dumps(dict(monsters=DROPS)))
                gd=SimpleNamespace(maps=SimpleNamespace(name=lambda s:s))
                fake=SimpleNamespace(map_monsters=lambda name:[],recommend=lambda x:dict(available=False))
                with patch('services.hunting_profit.elements.shared',return_value=fake):
                    service=HuntingProfitService(path,store,SimpleNamespace(name=str),gd,drops)
                    data=service.load('129');self.assertEqual(data['samples'],1);self.assertEqual(data['rows'][0]['per_hour'],200)
                    self.assertEqual(service.load('229')['rows'],[])
                    store.save_listings('129',1,[],now=200)
                    self.assertIsNone(service.load('129')['rows'][0]['per_hour'])
                repo.delete(sid);self.assertEqual(db._query('SELECT COUNT(*) FROM hunting_window_reports')[0][0],0)
            finally:store.close();db.conn.close()

if __name__=='__main__':unittest.main()
