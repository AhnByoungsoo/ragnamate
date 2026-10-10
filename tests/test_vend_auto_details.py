"""가짜 opener로 자동 상세 표시·캐시·중지·429·오래된 결과 억제를 확인한다."""
import helpers  # noqa: F401
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services import gnjoy_market as G
from services.vend_search import CACHE_SEC, VendSearch
from ui.vend_search_dialog import VendSearchDialog
from test_equipment_details import database
from test_gnjoy_market import deal_page
from test_vend_search import Settings, row

APP = QApplication.instance() or QApplication([])
VIEW = '<th>서버</th><td>바포메트 (prt_mk)</td><th>슬롯정보</th><td><ul><li>심연의 기사 카드</li><li>바르문트의 결정(화염) Lv3</li></ul></td>'


def parameters(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


class AutoDetailsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'))
        self.calls = []
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.mode = ''

        def opener(url, referer):
            self.calls.append(url)
            p = parameters(url)
            if 'itemDealList' in url:
                if p.get('itemFullName') == '새검색':
                    return deal_page([row(100, '+10새 장비[1]', 99, '새 상점', '3')])
                return deal_page([row(100, '[UNIQUE]+12장비[2]', 240000000, '가게1', '1'),
                                  row(100, '+10장비[2]', 250000000, '가게2', '2')])
            if self.mode == 'gate' and p['ssi'] == '1':
                self.entered.set()
                self.gate.wait(5)
            if self.mode == 'error' and p['ssi'] == '1':
                raise urllib.error.HTTPError(url, 500, 'error', {}, None)
            if self.mode == '429':
                raise urllib.error.HTTPError(url, 429, 'rate limited', {}, None)
            return VIEW
        self.client = G.GnjoyClient(interval=0, opener=opener)
        self.dialog = VendSearchDialog(None, Settings(), database(), self.store, self.client)
        self.dialog.show()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.gate.set()
        self.dialog.close()
        for task in (self.dialog.task, self.dialog.auto_task):
            future = task.future
            if future is not None:
                future.result(timeout=5)
                task.poll()
            task.close()
        self.store.close()
        self.tmp.cleanup()

    def until(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            APP.processEvents()
            if condition():
                return
            QTest.qWait(15)
        self.fail('작업이 시간 안에 완료되지 않았습니다.')

    def search(self, text='장비'):
        self.dialog.query.setText(text)
        self.dialog.search()

    def finished(self):
        d = self.dialog
        return d.task.future is None and d.auto_task.future is None and not d._auto_queue

    def test_search_automatically_shows_every_listing_without_selection(self):
        self.search()
        self.until(self.finished)
        d = self.dialog
        self.assertEqual(d.result_view.currentIndex(), 0)
        self.assertIsNone(d.current)
        self.assertFalse(d.only_popular.isChecked())
        self.assertEqual(len(d.cards.cards), 2)
        for card in d.cards.cards:
            self.assertIn('심연의 기사 카드', card.options.buttons[0].text())
            self.assertIn('25%', card.options.buttons[0].text())
            self.assertIn('Lv3', card.options.buttons[1].text())
            self.assertFalse(card.loading.isVisible())
        self.assertIn('2/2건', d.auto_status.text())
        self.assertEqual(len(self.calls), 3)
        self.search()
        self.until(self.finished)
        self.assertEqual(len(self.calls), 3)  # 검색·상세 모두 캐시 재사용

    def test_details_are_visible_before_remaining_requests_finish(self):
        self.mode = 'gate'
        self.search()
        self.until(self.entered.is_set)
        self.assertEqual(len(self.dialog.cards.cards), 2)
        self.assertIn('조회 중', self.dialog.cards.cards[0].loading.text())
        self.assertIn('대기', self.dialog.cards.cards[1].loading.text())
        self.gate.set()
        self.until(self.finished)

    def test_failure_does_not_look_like_no_enchants_or_stop_other_rows(self):
        self.mode = 'error'
        self.search()
        self.until(self.finished)
        cards = self.dialog.cards.cards
        self.assertIn('확인 실패', cards[0].loading.text())
        self.assertNotIn('없음', cards[0].loading.text())
        self.assertEqual(len(cards[1].options.buttons), 2)
        self.assertIn('미확인 1건', self.dialog.auto_status.text())

    def test_429_stops_global_requests_then_automatically_resumes(self):
        self.mode = '429'
        self.search()
        self.until(lambda: self.dialog.auto_retry.isActive())
        self.assertGreaterEqual(self.client.retry_after, 290)
        self.assertEqual(len(self.calls), 2)  # 목록 + 실패한 상세 한 번만
        self.assertTrue(all(c.row['_detail_state'] == 'rate_limited' for c in self.dialog.cards.cards))
        self.mode = ''
        self.client._paused_until = 0
        self.dialog._pump_auto()
        self.until(self.finished)
        self.assertEqual(len(self.calls), 4)
        self.assertFalse(self.dialog.auto_retry.isActive())

    def test_stop_prevents_later_rows_and_restart_keeps_cache(self):
        self.mode = 'gate'
        self.search()
        self.until(self.entered.is_set)
        self.dialog.stop()
        self.gate.set()
        self.until(self.finished)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(c.row['_detail_state'] == 'stopped' for c in self.dialog.cards.cards))
        self.dialog._begin_auto()
        self.until(self.finished)
        self.assertEqual(len(self.calls), 3)  # 중지 직전 받은 상세는 재요청하지 않음

    def test_old_details_cannot_overwrite_new_search(self):
        self.mode = 'gate'
        self.search()
        self.until(self.entered.is_set)
        self.search('새검색')
        self.gate.set()
        self.until(self.finished)
        self.assertEqual([c.row['ssi'] for c in self.dialog.cards.cards], ['3'])
        self.assertEqual([r['ssi'] for r in self.dialog.rows], ['3'])
        self.assertIn('새 장비', self.dialog.cards.cards[0].title.text())
        self.assertNotIn('ssi=2', '\n'.join(self.calls))

    def test_close_suppresses_updates_and_reopen_can_search_again(self):
        self.mode = 'gate'
        self.search()
        self.until(self.entered.is_set)
        self.dialog.close()
        self.gate.set()
        self.until(self.finished)
        self.assertEqual(len(self.calls), 2)
        self.dialog.show()
        self.until(self.finished)
        self.assertEqual(len(self.calls), 3)
        self.search('새검색')
        self.until(self.finished)
        self.assertEqual(self.dialog.rows[0]['ssi'], '3')

    def test_filter_and_price_copy_use_the_corresponding_card(self):
        self.search()
        self.until(self.finished)
        self.dialog.pmax.setText('2.4억')
        self.assertEqual(len(self.dialog.cards.cards), 1)
        card = self.dialog.cards.cards[0]
        self.dialog._card_action(card.row, 'price')
        self.assertEqual(QApplication.clipboard().text(), '240000000')
        self.assertEqual(self.dialog.current['ssi'], '1')

    def test_detail_cache_expires_at_30_minutes_and_empty_response_is_not_cached(self):
        self.search()
        self.until(self.finished)
        item = self.dialog.rows[0]
        now = [1000.0]
        engine = VendSearch(self.client, clock=lambda: now[0])
        self.store.save_detail('129', '1', G.parse_deal_view(VIEW), now=1000)
        calls = len(self.calls)
        now[0] += CACHE_SEC - 1
        engine.detail(self.store, item)
        self.assertEqual(len(self.calls), calls)
        now[0] += 1
        engine.detail(self.store, item)
        self.assertEqual(len(self.calls), calls + 1)
        missing = dict(item, ssi='404')
        self.client._open = lambda *args: '<html>매물 없음</html>'
        with self.assertRaises(ValueError):
            engine.detail(self.store, missing)
        self.assertIsNone(self.store.detail('129', '404')[1])


if __name__ == '__main__':
    unittest.main()
