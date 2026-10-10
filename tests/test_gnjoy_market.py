import datetime
import os
import tempfile
import time
import unittest

from helpers import ROOT  # noqa: F401  (sys.path 설정)

from services import gnjoy_market as G


def deal_row(iid, name, qty, price, shop, kind):
    return ('<tr><td>바포메트</td><td class="item"><a href="#" onclick="javascript:CallItemDealView(129,2023,\'1\',1)">'
            '<img src="https://imgc1.gnjoy.com/games/ro1/object/201306/%d.png" alt="%s" /><span>%s</span></a></td>'
            '<td class="quantity">%s</td><td class="price"><span class="priceLv3">%s</span></td>'
            '<td class="shop %s">%s</td></tr>' % (iid, name, name, qty, format(price, ','), kind, shop))


def deal_page(rows, last=1):
    nav = ''.join('<a href="javascript:CallItemDealList(129,\'오리데오콘\', \'\', \'\',%d)">%d</a>' % (p, p)
                  for p in range(2, last + 1))
    return '<table><tbody>%s</tbody></table><span class="pagingNav"><em>1</em>%s</span>' % (''.join(rows), nav)


def hist_page(days, last=1):
    rows = ''.join('<tr><td><a onclick="javascript:CallItemPriceExtendDateList(\'%s\', 129, \'x\', 1);">%s</a></td>'
                   '<td class="price"><span>%s</span></td><td class="price"><span>%s</span></td>'
                   '<td class="price"><span>%s</span></td></tr>' % (d, d.replace('-', '.'), format(a, ','),
                                                                     format(b, ','), format(c, ','))
                   for d, a, b, c in days)
    nav = ''.join('<a href="javascript:CallItemPriceExtendList(129,\'x\',%d)">%d</a>' % (p, p) for p in range(2, last + 1))
    return '<table><tbody>%s</tbody></table><span class="pagingNav">%s</span>' % (rows, nav)


class ParseTest(unittest.TestCase):
    def test_listings_item_id_kind_and_pages(self):
        page = deal_page([deal_row(756, '오리데오콘 원석', 20, 1500, '◑낙원◐', 'sale'),
                          deal_row(984, '오리데오콘', 105, 8000, '삼)오리', 'buy'),
                          deal_row(984, '오리데오콘', 6, 8500, '**프론 6시**', 'sale')], last=2)
        rows, last = G.parse_listings(page)
        self.assertEqual(last, 2)
        self.assertEqual([(r['item_id'], r['price'], r['kind'], r['qty']) for r in rows],
                         [(756, 1500, 'sale', 20), (984, 8000, 'buy', 105), (984, 8500, 'sale', 6)])
        self.assertEqual(rows[2]['shop'], '**프론 6시**')
        self.assertEqual(G.parse_listings('<p>검색된 결과가 없습니다</p>'), ([], 1))

    def test_history(self):
        rows, last = G.parse_history(hist_page([('2026-09-26', 8000, 11531, 30000), ('2026-09-25', 8000, 13737, 30000)],
                                               last=13))
        self.assertEqual(last, 13)                        # 표 안의 날짜 링크(…, 1) 는 페이지 번호로 치지 않음
        self.assertEqual(rows[0], dict(day='2026-09-26', min=8000, avg=11531, max=30000))


class ClientTest(unittest.TestCase):
    def test_pages_interval_and_rate_limit(self):
        calls = []

        def opener(url, referer):
            calls.append((time.monotonic(), url))
            if 'itemDealList' in url:
                return deal_page([deal_row(984, '오리데오콘', 1, 9000 + len(calls), 's', 'sale')], last=3)
            return hist_page([('2026-09-2%d' % len(calls), 1, 2, 3)], last=13)
        c = G.GnjoyClient(interval=0.05, opener=opener)
        self.assertEqual(len(c.listings('129', '오리데오콘', max_pages=2)), 2)   # 3쪽까지 있어도 2쪽만
        rows, last = c.history('129', '오리데오콘', pages=1)
        self.assertEqual((len(rows), last), (1, 13))
        self.assertTrue(all(b[0] - a[0] >= 0.045 for a, b in zip(calls, calls[1:])))   # 요청 간격
        self.assertIn('svrID=129', calls[0][1])

        def limited(url, referer):
            raise G.RateLimited('429')
        with self.assertRaises(G.RateLimited):
            G.GnjoyClient(interval=0, opener=limited).listings('129', 'x')


class StoreCompareTest(unittest.TestCase):
    def test_store_and_compare(self):
        with tempfile.TemporaryDirectory() as d:
            s = G.VendingStore(os.path.join(d, 'v.db'))
            s.save_history('129', 984, [dict(day='2026-09-26', min=8000, avg=12000, max=30000),
                                        dict(day='2026-09-01', min=5000, avg=10000, max=20000)])
            s.save_history('129', 984, [dict(day='2026-09-26', min=7000, avg=12000, max=30000)])   # 같은 날은 새 값
            s.save_listings('129', 984, [dict(price=9000, qty=3, shop='a', kind='sale'),
                                         dict(price=8000, qty=9, shop='b', kind='buy')], now=100)
            s.save_listings('129', 984, [dict(price=8500, qty=1, shop='c', kind='sale')], now=200)
            self.assertEqual([h['min'] for h in s.history('129', 984)], [7000, 5000])
            t, ls = s.latest_listings('129', 984)
            self.assertEqual((t, [l['price'] for l in ls]), (200, [8500]))         # 마지막으로 받은 목록만
            s.save_listings('129', 5, [], now=300)                                    # 노점 없음도 기록
            self.assertEqual(s.latest_listings('129', 5), (300, []))
            self.assertEqual(s.latest_listings('229', 984), (None, []))
            s.set_meta('server', '229')
            self.assertEqual(s.meta('server'), '229')
            s.conn.close()

        today = datetime.date(2026, 9, 26)
        hist = [dict(day='2026-09-26', min=8000, avg=12000, max=30000), dict(day='2026-09-24', min=8000, avg=14000, max=30000),
                dict(day='2026-08-01', min=1, avg=40000, max=1)]
        c = G.compare(hist, [dict(price=9000, qty=1, shop='a', kind='sale'), dict(price=8000, qty=1, shop='b', kind='buy')],
                      today=today)
        self.assertEqual((c['now_min'], c['sellers'], c['buy_max'], c['avg7'], c['avg30']), (9000, 1, 8000, 13000, 13000))
        self.assertEqual(round(c['diff']), -31)
        self.assertEqual(c['verdict'], '평소보다 쌈')
        self.assertEqual(c['avg120'], 22000)
        self.assertEqual(c['suggest'], 8910)                                          # min(7일 평균, 지금 최저) × 0.99
        self.assertEqual(G.compare(hist, [], today=today)['verdict'], '지금 판매 노점 없음')
        self.assertEqual(G.compare([], [], today=today)['verdict'], '거래 기록 없음')
        self.assertEqual(G.compare([dict(day='2026-09-26', min=1, avg=10000, max=1)],
                                   [dict(price=12000, qty=1, shop='', kind='sale')], today=today)['verdict'], '평소보다 비쌈')


if __name__ == '__main__':
    unittest.main()
