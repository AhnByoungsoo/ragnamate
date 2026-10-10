"""시세 Provider 인터페이스.

새 시세 출처(공개 API, 시세 사이트, 직접 수집 DB 등)를 붙이려면 PriceProvider를 상속해
get_market_listings()만 구현하고 services/price_provider/__init__.py의 PROVIDERS에 등록하면 된다.
"""
from abc import ABC, abstractmethod

from core.pricing import compute_price_info


class PriceProvider(ABC):
    key = 'base'
    label = 'Base'
    is_remote = False    # True면 MarketService가 동시 요청 수/요청 간격을 제한한다

    def __init__(self, db, settings):
        self.db = db
        self.settings = settings

    @abstractmethod
    def get_market_listings(self, item_id, name=None):
        """현재 노점 목록 [Listing]. 시세가 없으면 []."""

    def get_price(self, item_id, name=None):
        """PriceInfo (노점 목록으로 계산)."""
        return compute_price_info(item_id, self.get_market_listings(item_id, name), self.key,
                                  self.settings.get('outlier_filter', True),
                                  updated_at=self.get_last_updated(item_id))

    def get_last_updated(self, item_id):
        """시세 수집 시각 epoch 또는 None."""
        return None
