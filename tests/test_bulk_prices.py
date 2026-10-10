"""전체 시세 받기: 받는 순서, 최근에 받은 것 건너뛰기, 멈춤·요청 제한, 표가 쓰는 시세 모양."""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

import helpers  # noqa: F401  (sys.path 설정)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication  # noqa: E402

from services import gnjoy_market as G  # noqa: E402
from services.loot_value import LootValue  # noqa: E402
from ui.bulk_prices import BulkPriceFetcher, order  # noqa: E402

APP = QApplication.instance() or QApplication([])
CARD, JUNK, ORE = 4001, 7219, 984


def entry(item_id, name, quantity):
    return SimpleNamespace(item_id=item_id, name=name, quantity=quantity)


class FakePricer:
    def __init__(self, fresh=(), blocked=0):
        self.done, self._fresh, self.blocked, self.calls = set(fresh), set(fresh), blocked, []

    def fresh(self, item_id):
        return item_id in self._fresh

    def fetch(self, item_id, name):
        self.calls.append(item_id)
        if self.blocked:
            self.blocked -= 1
            raise G.RateLimited('잠시 뒤')
        self._fresh.add(item_id)


class OrderTest(unittest.TestCase):
    def test_bigger_stacks_of_pricier_items_first_and_duplicates_merged(self):
        entries = [entry(JUNK, '양말', 10), entry(ORE, '오리데오콘', 5), entry(JUNK, '양말', 30), entry(CARD, '카드', 1)]
        self.assertEqual(order(entries, {JUNK: 570, ORE: 682, CARD: 12}.get),
                         [(JUNK, '양말'), (ORE, '오리데오콘'), (CARD, '카드')])           # 40×570 > 5×682 > 1×12
        self.assertEqual(order(entries)[0], (JUNK, '양말'))                           # 상점가를 모르면 수량 순


class FetcherTest(unittest.TestCase):
    def run_all(self, pricer, items, **kw):
        f = BulkPriceFetcher(pricer, retry_sec=0.01, **kw)
        seen, result = [], []
        f.progress.connect(lambda done, total, name: seen.append((done, total, name)))
        f.finished.connect(lambda *a: result.append(a))
        f.run(items)                                                   # 같은 스레드에서 끝까지
        return f, seen, result[0]

    def test_recent_ones_are_skipped_so_it_resumes(self):
        pricer = FakePricer(fresh={JUNK})
        _, seen, result = self.run_all(pricer, [(JUNK, '양말'), (ORE, '오리'), (CARD, '카드')])
        self.assertEqual(pricer.calls, [ORE, CARD])                    # 30분 안에 받은 양말은 다시 묻지 않는다
        self.assertEqual(result, (2, 1, True))
        self.assertEqual(seen[-1], (3, 3, '카드'))

    def test_rate_limit_waits_and_continues(self):
        pricer = FakePricer(blocked=2)
        f = BulkPriceFetcher(pricer, retry_sec=0.01)
        waits = []
        f.waiting.connect(waits.append)
        f.run([(ORE, '오리')])
        self.assertEqual(pricer.calls, [ORE, ORE, ORE])                # 막힌 두 번 뒤에 받았다
        self.assertEqual(len(waits), 2)

    def test_stop_ends_without_fetching_the_rest(self):
        pricer = FakePricer()
        f = BulkPriceFetcher(pricer, retry_sec=0.01)
        result = []
        f.finished.connect(lambda *a: result.append(a))
        f.progress.connect(lambda *a: f.stop())                        # 첫 아이템 뒤에 멈춤
        f.run([(ORE, '오리'), (CARD, '카드'), (JUNK, '양말')])
        self.assertEqual((pricer.calls, result), ([ORE], [(1, 0, False)]))

    def test_one_failure_does_not_stop_the_rest(self):
        pricer = FakePricer()
        real = pricer.fetch

        def flaky(item_id, name):
            if item_id == ORE:
                raise OSError('네트워크')
            real(item_id, name)
        pricer.fetch = flaky
        _, _, result = self.run_all(pricer, [(ORE, '오리'), (CARD, '카드')])
        self.assertEqual(result, (1, 0, True))


class PriceInfoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'))
        self.addCleanup(self.store.close)
        self.value = LootValue(self.store, None)

    def test_listing_snapshot_becomes_table_price(self):
        now = time.time()
        rows = [dict(price=p, qty=1, shop='가게', kind=k) for p, k in ((300, 'sale'), (100, 'sale'), (200, 'sale'), (90, 'buy'))]
        self.store.save_listings('129', JUNK, rows, now=now)
        info = self.value.price_info(JUNK)
        self.assertEqual((info.lowest_price, info.median_price, info.average_price, info.listing_count, info.highest_buy),
                         (100, 200, 200, 3, 90))                       # 구매 노점은 판매가에 넣지 않는다
        self.assertEqual((info.updated_at, info.provider, info.has_price), (now, '공식 노점', True))

    def test_unknown_stays_none(self):
        self.assertIsNone(self.value.price_info(ORE))
        self.store.save_listings('129', ORE, [])                        # 받았지만 노점 없음
        self.assertIsNone(self.value.price_info(ORE))


if __name__ == '__main__':
    unittest.main()
