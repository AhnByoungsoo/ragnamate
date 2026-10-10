"""창고 시세 기능 회귀: 가격 계산/필터 기준/내보내기/DB 마이그레이션 보존."""
import os
import tempfile
import unittest

import helpers  # noqa: F401  (sys.path 설정)

from core import analysis
from core.models import ItemEntry, Listing, PriceInfo
from core.pricing import compute_price_info


class StorageFeatureTest(unittest.TestCase):
    def test_price_stats_iqr(self):
        p = compute_price_info(1, [Listing(x) for x in [100, 110, 120, 105, 115, 99999]])
        self.assertEqual((p.lowest_price, p.average_price, p.listing_count), (100, 110, 6))

    def test_value_and_sellable(self):
        e = ItemEntry('창고1', 984, '오리데오콘', 10)
        rows = analysis.build_rows([e], {984: PriceInfo(984, 7000, 8000, 9000, 3, 0)}, 'lowest')
        self.assertEqual(rows[0].value, 70000)
        self.assertFalse(analysis.is_sellable(rows[0], 100_000))
        rows = analysis.build_rows([e], {984: PriceInfo(984, 7000, 80000, 9000, 3, 0)}, 'median')
        self.assertTrue(analysis.is_sellable(rows[0], 100_000))

    def test_merge(self):
        es = [ItemEntry('창고1', 1, 'a', 2), ItemEntry('창고1', 1, 'a', 3), ItemEntry('카트', 1, 'a', 1)]
        m = analysis.merge_entries(es)
        self.assertEqual([(x.source, x.quantity) for x in m], [('창고1', 5), ('카트', 1)])

    def test_overlay_rows(self):
        entries = [ItemEntry('창고1', 984, '오리데오콘', 10), ItemEntry('카트', 984, '오리데오콘', 5),
                   ItemEntry('인벤토리', 501, '빨간포션', 3), ItemEntry('창고1', 7, '이름없음', 1)]
        prices = {984: PriceInfo(984, 7000, 8000, 9000, 3, 1_700_000_000), 501: PriceInfo(501, 50, 50, 50, 2, 0)}
        rows = analysis.build_rows(entries, prices, 'lowest')
        out = analysis.overlay_rows(rows)
        self.assertEqual(out[0]['name'], '오리데오콘')
        self.assertEqual(out[0]['quantity'], 15)                  # 창고+카트 합산
        self.assertEqual(out[0]['value'], 105_000)
        self.assertEqual(out[0]['locations'], '창고·카트')
        self.assertEqual(out[0]['average'], 9000)
        self.assertIsNone(out[-1]['value'])                       # 시세 없는 아이템은 0이 아니라 없음
        self.assertEqual([r['name'] for r in analysis.overlay_rows(rows, query='포션')], ['빨간포션'])
        self.assertEqual([r['name'] for r in analysis.overlay_rows(rows, only_sellable=True, min_value=100_000)],
                         ['오리데오콘'])

    def test_db_migration_keeps_market_tables(self):
        import sqlite3
        from database.database import Database
        path = os.path.join(tempfile.mkdtemp(), 'old.db')
        c = sqlite3.connect(path)       # v1 DB(창고 시세) 흉내
        c.execute('CREATE TABLE local_listings (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, '
                  "variant TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, "
                  "kind TEXT NOT NULL DEFAULT 'sale', shop TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT "
                  "'manual', collected_at REAL NOT NULL)")
        c.execute("INSERT INTO local_listings(item_id, price, collected_at) VALUES (984, 7000, 0)")
        c.commit()
        c.close()
        db = Database(path)
        from database.migrations import LATEST
        self.assertEqual(db.schema_version, LATEST)
        self.assertTrue(db._query("SELECT name FROM sqlite_master WHERE name='guide_progress'"))
        self.assertEqual(len(db.local_listings(984)), 1)
        self.assertTrue(db._query("SELECT name FROM sqlite_master WHERE name='replay_sessions'"))
        Database(path)                   # 재실행해도 초기화되지 않음
        self.assertEqual(len(Database(path).local_listings(984)), 1)

    def test_export(self):
        from services import export_service

        class Fmt:
            card_text = option_text = staticmethod(lambda e: '')
        rows = analysis.build_rows([ItemEntry('창고1', 984, '오리데오콘', 10)],
                                   {984: PriceInfo(984, 7000, 8000, 9000, 3, 0)}, 'lowest')
        for ext in ('xlsx', 'csv'):
            path = os.path.join(tempfile.mkdtemp(), 'x.' + ext)
            export_service.export(path, rows, Fmt(), analysis.summarize(rows), {'a': 'b'})
            self.assertGreater(os.path.getsize(path), 0)


if __name__ == '__main__':
    unittest.main()
