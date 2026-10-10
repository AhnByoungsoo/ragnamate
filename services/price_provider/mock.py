"""MockPriceProvider: UI/분석 테스트용 가짜 시세 (item_id로 결정되는 고정 값).

실제 가격이 아니므로 화면에 'MOCK' 표시가 붙는다.
"""
import random
import time

from core.models import Listing
from services.price_provider.base import PriceProvider


class MockPriceProvider(PriceProvider):
    key = 'mock'
    label = 'MOCK (테스트용 가짜 시세)'

    def get_market_listings(self, item_id, name=None):
        rnd = random.Random(item_id)
        if rnd.random() < 0.3:
            return []
        base = int(10 ** rnd.uniform(2.5, 7.5))
        now = time.time()
        sales = [Listing(int(base * rnd.uniform(0.9, 1.6)), rnd.randint(1, 50), 'sale', 'mock', now)
                 for _ in range(rnd.randint(1, 12))]
        if rnd.random() < 0.2:
            sales.append(Listing(base * 50, 1, 'sale', 'mock-outlier', now))   # 이상치 예시
        return sales

    def get_last_updated(self, item_id):
        return time.time()
