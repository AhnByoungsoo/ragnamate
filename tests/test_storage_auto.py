import helpers
import tempfile,time,unittest,sqlite3
from pathlib import Path
from core.models import ItemEntry,PriceInfo
from core.analysis import build_rows
from core.storage_auto import candidates_from_rows,price_labels,with_official_prices

class StorageAutoPricesTest(unittest.TestCase):
    def test_candidates_exclude_other_storage_and_mock(self):
        rows=build_rows([ItemEntry('창고1',1,'a',2),ItemEntry('창고2',2,'b',1)],
            {1:PriceInfo(1,999,999,999,1,10,provider='mock')},'lowest')
        c=candidates_from_rows(rows,'창고1')
        self.assertEqual([x['item_id'] for x in c],[1]);self.assertIsNone(c[0]['price'])

    def test_variants_collapse_but_are_approximate(self):
        rows=build_rows([ItemEntry('창고1',1,'a',1,refine=5),ItemEntry('창고1',1,'a',1,refine=9)],{},'lowest')
        c=candidates_from_rows(rows,'창고1')
        self.assertEqual(len(c),1);self.assertTrue(c[0]['approximate'])

    def test_unknown_missing_zero_and_stale(self):
        def slot(price,at,approx=False):return {'item':dict(name='a',price=price,updated_at=at,approximate=approx)}
        values=price_labels([{'item':None},slot(None,1),slot(0,99999),slot(10000,1,True)],now=100000)
        self.assertEqual([x['text'] for x in values],['?','-','0','1만~'])
        self.assertFalse(values[2]['stale']);self.assertTrue(values[3]['stale'])

    def test_server_and_latest_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'vending.db'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE listings(server TEXT,item_id INTEGER,price INTEGER,kind TEXT,fetched_at REAL)')
            db.executemany('INSERT INTO listings VALUES(?,?,?,?,?)',[
                ('129',1,500,'sale',10),('129',1,None,None,20),('229',1,1,'sale',30),
                ('129',2,200,'sale',20),('129',2,1,'buy',20),('129',2,100,'sale',20)])
            db.commit();db.close()
            c=[dict(item_id=i,price=400,updated_at=1) for i in (1,2)]
            values=with_official_prices(c,path,'바포메트')
            self.assertIsNone(values[0]['price']);self.assertEqual(values[1]['price'],100)
            self.assertEqual(c[0]['price'],400)
            self.assertEqual(with_official_prices(c,path,'unknown'),c)
            path.unlink() # Reader must release the connection on Windows.

if __name__=='__main__':unittest.main()
