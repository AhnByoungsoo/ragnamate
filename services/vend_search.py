"""노점 검색: 공식 '아이템 거래현황'의 지금 노점 목록을 이름 일부로 찾는다.

벤치마킹: RagMAYA 실시간 노점 검색(서버·판매/구매·가격 범위·정렬·상세 한 번에), 공식 거래현황(분류·상세 보기),
kafra(단어 사이 '%'로 여러 단어 검색). 요청은 shared_client(2.5초 간격, 429면 5분 멈춤)로만 하고,
같은 검색은 30분 동안 다시 요청하지 않는다. 받은 목록은 검색 결과로만 쓰고 시세 기록(vending.db)에 넣지 않는다
(가격 낮은 순으로 앞 몇 쪽만 받으므로 아이템별 전체 목록이 아님).

공식 사이트 동작(2026-09-27 확인): svrID ''=전체 서버, inclusion W/D/C/E=무기/방어구/소비/기타,
itemOrder 'DESC'=가격 높은 순(빈 값=낮은 순), 이름 사이 '%'=순서대로 모두 포함, 한 쪽 10건.
"""
import statistics
import time

from services import gnjoy_market as G
from services.market_insights import recent_history

CATEGORIES = (('', '전체'), ('W', '무기'), ('D', '방어구'), ('C', '소비'), ('E', '기타'))
ORDERS = (('', '가격 낮은순'), ('DESC', '가격 높은순'))
SERVERS = (('', '전체 서버'),) + tuple(G.SERVERS.items())
CACHE_SEC = 30 * 60
MAX_PAGES = 20            # 한 쪽 10건. 공식 사이트가 알려 주는 '전체 쪽 수'가 실제보다 적을 때가 있어 넉넉히 둔다
PAGE_ROWS = 10            # 한 쪽이 꽉 찼으면 뒤에 더 있을 수 있다(2026-10-05 실측: 전체 3쪽이라더니 4쪽까지 나옴)
_SID = {name: sid for sid, name in G.SERVERS.items()}


def build_query(text, all_words=True):
    """'스펠 스톤' → '스펠%스톤'(두 단어가 순서대로 들어간 이름 모두). all_words=False 면 띄어쓰기 그대로."""
    words = (text or '').split()
    return ('%'.join(words) if all_words else ' '.join(words))[:50]


def parse_zeny(text):
    """'1억 5000만' · '15,000,000' · '1.5m' · '300k' · '300만' → 제니. 빈 값·못 읽으면 None."""
    s = (text or '').replace(',', '').replace(' ', '').lower()
    if not s:
        return None
    units = (('억', 100000000), ('만', 10000), ('m', 1000000), ('k', 1000))
    total, rest = 0.0, s
    for unit, mul in units:
        if unit in rest:
            head, rest = rest.split(unit, 1)
            try:
                total += float(head or 1) * mul
            except ValueError:
                return None
    if rest:
        try:
            total += float(rest)
        except ValueError:
            return None
    return int(total)


def _enrich(row, server):
    refine, grade, base, slots = G.split_name(row['name'])
    row.update(refine=refine, grade=grade, base=base, slots=slots, sid=_SID.get(row.get('server'), server))
    return row


class VendSearch:
    def __init__(self, client=None, clock=time.time):
        self.client = client or G.shared_client()
        self.clock = clock
        self.cache = {}

    def cached(self, key):
        hit = self.cache.get(key)
        return hit if hit and self.clock() - hit['fetched_at'] < CACHE_SEC else None

    def search(self, server, query, category='', order='', pages=1, stop=None):
        """{rows, last(공식이 알려 준 전체 쪽 수), pages(받은 쪽 수), more(뒤에 더 있을 수 있음), fetched_at, cached}.

        같은 검색을 쪽 수만 늘려 다시 부르면 **이미 받은 쪽은 다시 요청하지 않고 뒤쪽만** 받는다.
        쪽 사이에서 stop 이면 멈춘다.
        """
        pages = max(1, min(MAX_PAGES, int(pages)))
        key = (server, query, category, order)
        hit = self.cached(key)
        rows = list(hit['rows']) if hit else []
        page = (hit['pages'] if hit else 0) + 1
        last = hit['last'] if hit else 1
        full = hit['full'] if hit else True                     # 마지막으로 받은 쪽이 꽉 찼는가
        fetched = False
        while page <= pages and (page <= last or full):
            if stop and stop.is_set() and rows:
                break
            got, got_last = G.parse_listings(self.client.get(
                'itemDealList.asp', dict(svrID=server, itemFullName=query, itemOrder=order, inclusion=category,
                                         curpage=page), 'dealSearch.asp'))
            fetched = True
            full = len(got) >= PAGE_ROWS
            rows += [_enrich(r, server) for r in got]
            last = max(last, got_last, page)
            page += 1
        done = page - 1
        result = dict(rows=rows, last=last, pages=done, full=full, more=full or last > done,
                      fetched_at=self.clock() if fetched or not hit else hit['fetched_at'], cached=not fetched)
        self.cache[key] = dict(result, cached=False)
        return result

    def cached_detail(self, store, row):
        if not row.get('ssi') or not row.get('sid'):
            return None
        fetched, det = store.detail(row['sid'], row['ssi'])
        return det if fetched is not None and 0 <= self.clock() - fetched < CACHE_SEC else None

    def detail(self, store, row):
        """상세 보기(카드·랜덤옵션·맵): 30분 캐시, 만료 시 기존 요청 제한으로 갱신."""
        det = self.cached_detail(store, row)
        if det is None:
            det = self.client.deal_view(row['sid'], row['name'], row.get('map_code', ''), row['ssi'])
            if not any(det.get(k) for k in ('map', 'shop', 'character', 'cards', 'options', 'element', 'maker')):
                raise ValueError('매물이 사라졌거나 상세 응답을 확인할 수 없습니다.')
            store.save_detail(row['sid'], row['ssi'], det, now=self.clock())
        return det


def filter_rows(rows, kinds=('sale', 'buy'), refine_min=0, price_min=None, price_max=None, item_id=None):
    return [r for r in rows if r['kind'] in kinds and r['refine'] >= refine_min
            and (price_min is None or (r['price'] or 0) >= price_min)
            and (price_max is None or (r['price'] or 0) <= price_max)
            and (item_id is None or r['item_id'] == item_id)]


SORTS = (('price', '가격 낮은순'), ('-price', '가격 높은순'), ('-refine', '제련 높은순'), ('name', '이름순'))


def sort_rows(rows, key='price'):
    """판매 매물을 먼저, 구매(사겠다는) 매물을 뒤로 — 섞어 놓으면 더 싼 구매가가 맨 앞에 와서
    그 아이템의 가격으로 잘못 읽힌다. 그 안에서만 고른 순서를 따른다."""
    buy_last = lambda r: r.get('kind') != 'sale'                                  # noqa: E731
    if key == '-refine':
        return sorted(rows, key=lambda r: (buy_last(r), -r['refine'], r['price'] or 0))
    if key == 'name':
        return sorted(rows, key=lambda r: (buy_last(r), r['base'], -r['refine'], r['price'] or 0))
    if key == '-price':
        return sorted(rows, key=lambda r: (buy_last(r), -(r['price'] or 0)))
    return sorted(rows, key=lambda r: (buy_last(r), r['price'] or 0))


def summary(rows):
    """판매 매물 기준 최저·중간값 + 판매/구매 수."""
    sale = sorted(r['price'] for r in rows if r['kind'] == 'sale' and r['price'])
    buy = sorted((r['price'] for r in rows if r['kind'] == 'buy' and r['price']), reverse=True)
    return dict(count=len(rows), sale=len(sale), buy=len(buy),
                low=sale[0] if sale else None, median=int(statistics.median(sale)) if sale else None,
                buy_high=buy[0] if buy else None)


def avg7(store, server, item_id, today=None):
    """저장된 공식 거래 시세(날짜별 평균)의 최근 7일 평균. 없으면 None — 요청하지 않음."""
    days = recent_history(store.history(server, item_id), 7, today) if server else []
    return sum(d['avg'] for d in days) / len(days) if days else None


def vs_avg(price, avg):
    """7일 평균 대비 %(음수 = 쌈)."""
    return None if not (price and avg) else (price - avg) / avg * 100
