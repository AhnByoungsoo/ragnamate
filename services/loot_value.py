"""득템 가치: 공식 노점 자료(data/vending.db)로 아이템 1개의 지금 시세를 본다.

- 지금 판매 최저가 → 없으면 최근 7일 거래 평균 → 그것도 없으면 None(모름). 값을 지어내지 않는다.
- 받은 적이 없거나 30분이 지난 아이템만 한 번 받아 온다(요청 2번: 지금 노점 1쪽 + 최근 시세 1쪽).
  요청은 앱 전체가 같이 쓰는 클라이언트의 간격·429 규칙을 그대로 따른다.
"""
import time

from services import gnjoy_market as G

FRESH_SEC = 30 * 60


class LootValue:
    def __init__(self, store, client, server=lambda: '129', clock=time.time):
        self.store, self.client, self.server, self.clock = store, client, server, clock

    def price(self, item_id):
        """(단가, 근거 글) — 저장된 자료만 본다(요청 없음). 모르면 (None, '')."""
        sid = self.server()
        _, listings = self.store.latest_listings(sid, item_id)
        cmp = G.compare(self.store.history(sid, item_id), listings)
        if cmp.get('now_min'):
            return cmp['now_min'], '지금 판매 최저가'
        if cmp.get('avg7'):
            return int(cmp['avg7']), '최근 7일 거래 평균'
        return None, ''

    def price_info(self, item_id):
        """창고·시세 표가 쓰는 모양(PriceInfo)으로. 지금 판매 노점이 없으면 최근 7일 평균을 쓴다. 모르면 None."""
        import statistics

        from core.models import PriceInfo
        sid = self.server()
        at, listings = self.store.latest_listings(sid, item_id)
        sales = sorted(row['price'] for row in listings if row['kind'] == 'sale' and row['price'])
        buys = [row['price'] for row in listings if row['kind'] == 'buy' and row['price']]
        if sales:
            return PriceInfo(item_id, sales[0], int(statistics.median(sales)), int(sum(sales) / len(sales)), len(sales),
                             at, '공식 노점', max(buys) if buys else None)
        avg = G.compare(self.store.history(sid, item_id), []).get('avg7')
        if avg:
            when = self.store.history_fetched_at(sid, item_id) or at or 0
            return PriceInfo(item_id, int(avg), int(avg), int(avg), 0, when, '공식 최근 7일 평균')
        return None

    def fresh(self, item_id):
        sid = self.server()
        at = max(self.store.latest_listings(sid, item_id)[0] or 0, self.store.history_fetched_at(sid, item_id) or 0,
                 float(self.store.meta('listing_unknown:%s:%s' % (sid, item_id), 0) or 0))
        return bool(at) and self.clock() - at < FRESH_SEC

    def fetch(self, item_id, name):
        """공식 사이트에서 이 아이템의 지금 노점과 최근 시세를 받아 저장한다."""
        sid = self.server()
        raw, more = self.client.listings(sid, name, max_pages=1, with_more=True)
        mine = [r for r in raw if r['item_id'] == item_id]     # 검색은 이름 '포함' — 아이템 번호가 같은 줄만 쓴다
        if not mine and more:                                  # 비슷한 이름에 밀려 안 보임 → '노점 없음'으로 적지 않는다
            self.store.set_meta('listing_unknown:%s:%s' % (sid, item_id), self.clock())
        else:
            self.store.save_listings(sid, item_id, mine)
        hist, _ = self.client.history(sid, name, 1)
        self.store.save_history(sid, item_id, hist)

    def ensure(self, item_id, name):
        """자료가 오래됐으면 받아 온 뒤 (단가, 근거)."""
        if not self.fresh(item_id):
            self.fetch(item_id, name)
        return self.price(item_id)
