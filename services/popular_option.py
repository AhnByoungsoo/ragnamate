"""국민 옵션: 공식 노점 목록에서 가장 많이 올라온 장비 구성(제련·등급·슬롯)과 인챈트·카드 구성.

공식 사이트에는 거래 '건수'가 없어서 '지금 판매 매물 중 가장 많은 구성'을 국민 옵션으로 본다(사용자 확정 2026-09-30).
인챈트·카드는 그 구성의 싼 판매 매물 최대 5건 상세(슬롯정보)를 받아, 같은 구성이 2건 이상일 때만 기본값으로 한다.
요청은 shared_client(2.5초 간격·429면 5분 중지)로만 하고, 결과는 30분 동안 다시 받지 않는다.

거래 이력(itemPriceExtendList)은 슬롯·등급까지 포함한 전체 이름이 정확해야 나온다
(2026-09-30 확인: '+9일루시온 레그 B타입[1]' 10일치, '일루시온 레그 B타입' 0건). 그래서 장비 구성별 시세는 ExactMarket 으로 받는다.
"""
import collections
import json
import time

from core.item_category import category
from services import gnjoy_market as G
from services import shadow_deals as SD
from services.market_insights import recent_history

EQUIPMENT = ('무기', '방어구', '쉐도우 장비')
LIST_PAGES = 3
MAX_DETAILS = 5
MIN_SAME_CARDS = 2
CACHE_SEC = 30 * 60


def is_equipment(item_db, item_id):
    item = (getattr(item_db, 'items', {}) or {}).get(item_id)
    return isinstance(item, dict) and category(item) in EQUIPMENT


def config_of(name):
    """(제련, 등급, 슬롯, 이름) — 이름까지 본다: '+10마술의일루시온…'(카드 접두사)은 다른 구성이고 거래 이력도 따로다."""
    refine, grade, base, slots = G.split_name(name)
    return refine, grade, slots, ''.join(base.split())


def popular_config(listings, item_id):
    """판매 매물 중 가장 많은 구성(제련·등급·슬롯·이름). 같으면 높은 제련. 없으면 None."""
    sales = [l for l in listings if l.get('item_id') == item_id and l.get('kind') == 'sale' and l.get('price')]
    counts = collections.Counter(config_of(l['name']) for l in sales)
    if not counts:
        return None
    best = max(counts, key=lambda k: (counts[k], k[0]))
    base = next(G.split_name(l['name'])[2] for l in sales if config_of(l['name']) == best)
    return dict(refine=best[0], grade=best[1], slots=best[2], base=base, count=counts[best], total=len(sales),
                name=G.join_name(best[0], best[1], base, best[2]))


def popular_cards(details):
    """상세들의 카드·인챈트(슬롯정보) 중 가장 많은 구성 → (목록 또는 None, 그 구성 수). 2건 미만이면 기본값 없음."""
    counts = collections.Counter(tuple(d.get('cards') or []) for d in details)
    if not counts:
        return None, 0
    cards, n = counts.most_common(1)[0]
    return (list(cards) if n >= MIN_SAME_CARDS else None), n


def describe(opt):
    if not opt or not opt.get('name'):
        return '판매 매물이 없어 국민 옵션을 정하지 못했습니다'
    text = '%s · 판매 %d건 중 %d건' % (opt['name'], opt['total'], opt['count'])
    if opt.get('cards'):
        text += ' · 카드/인챈트 %s (%d/%d건)' % (', '.join(opt['cards']), opt['cards_seen'], opt['details'])
    elif opt.get('details'):
        text += ' · 카드/인챈트 구성이 제각각(상세 %d건) — 기본값 없음' % opt['details']
    if opt.get('more'):
        text += ' · 싼 순 %d쪽까지만 봄' % LIST_PAGES
    return text


class PopularOption:
    def __init__(self, store, client=None, clock=time.time):
        self.store, self.client, self.clock = store, client or G.shared_client(), clock

    def _key(self, server, item_id):
        return 'popular:%s:%s' % (server, item_id)

    def cached(self, server, item_id):
        raw = self.store.meta(self._key(server, item_id))
        opt = json.loads(raw) if raw else None
        return opt if opt and self.clock() - opt['fetched_at'] < CACHE_SEC else None

    def find(self, server, item_id, name, stop=None):
        """목록 최대 3쪽 + 상세 최대 5건. 30분 안에 받은 것은 재사용."""
        hit = self.cached(server, item_id)
        if hit:
            return hit
        rows, more = self.client.listings(server, name, max_pages=LIST_PAGES, with_more=True, stop=stop)
        opt = popular_config(rows, item_id) or dict(total=0, count=0)
        details = []
        if opt.get('name'):
            same = sorted((l for l in rows if l['item_id'] == item_id and l['kind'] == 'sale' and l.get('ssi')
                           and config_of(l['name']) == config_of(opt['name'])),
                          key=lambda l: l['price'])[:MAX_DETAILS]
            for l in same:
                if stop and stop.is_set():
                    break
                _, det = self.store.detail(server, l['ssi'])
                if det is None:
                    try:
                        det = self.client.deal_view(server, l['name'], l.get('map_code', ''), l['ssi'])
                    except G.RateLimited:
                        raise
                    except Exception:
                        continue                         # 매물이 그새 사라짐 등: 이 한 건만 건너뜀
                    self.store.save_detail(server, l['ssi'], det, now=self.clock())
                details.append(det)
        cards, seen = popular_cards(details)
        opt.update(cards=cards, cards_seen=seen, details=len(details), more=bool(more), fetched_at=self.clock())
        if not (stop and stop.is_set()):
            self.store.set_meta(self._key(server, item_id), json.dumps(opt, ensure_ascii=False))
        return opt


class ExactMarket(SD.ShadowScanner):
    """정확한 공식 이름(term)으로 받은 목록·거래 이력(shadow_pages/shadow_history 캐시 공용, 30분 재사용)."""

    def history(self, server, term, network=True):
        at, rows, _ = self._history(server, term, network)
        return at, rows

    def avg7(self, server, term, today=None):
        """저장된 이력만으로 최근 7일 날짜별 평균의 평균(요청 없음). 없으면 None."""
        _, rows = self.history(server, term, network=False)
        days = recent_history(rows, 7, today)
        return sum(d['avg'] for d in days) / len(days) if days else None
