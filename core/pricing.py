"""노점 목록 → 시세 통계. 이상치(비정상 고가/저가)를 고려한다."""
import statistics
import time

from core.models import PriceInfo

IQR_MIN_SAMPLES = 4


def iqr_filter(prices):
    """표본이 충분하면 Q1-1.5IQR ~ Q3+1.5IQR 밖 값을 제거."""
    if len(prices) < IQR_MIN_SAMPLES:
        return list(prices)
    q1, _, q3 = statistics.quantiles(prices, n=4)
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    kept = [p for p in prices if lo <= p <= hi]
    return kept or list(prices)


def compute_price_info(item_id, listings, provider='', outlier_filter=True, updated_at=None):
    """판매 노점 기준 통계. 최저가는 실제 살 수 있는 가격이므로 원본 최저값을 쓰고,
    평균가는 이상치를 제거한 값으로 계산한다(중앙값은 원래 이상치에 강함)."""
    sales = sorted(l.price for l in listings if l.kind == 'sale' and l.price > 0)
    buys = [l.price for l in listings if l.kind == 'buy' and l.price > 0]
    if updated_at is None:
        seen = [l.seen_at for l in listings if l.seen_at]
        updated_at = max(seen) if seen else time.time()
    if not sales:
        return PriceInfo(item_id, None, None, None, 0, updated_at, provider, max(buys) if buys else None)
    clean = iqr_filter(sales) if outlier_filter else sales
    return PriceInfo(item_id,
                     lowest_price=sales[0],
                     median_price=int(statistics.median(sales)),
                     average_price=int(statistics.fmean(clean)),
                     listing_count=len(sales),
                     updated_at=updated_at,
                     provider=provider,
                     highest_buy=max(buys) if buys else None)
