"""+10 쉐도우 장비 저가 매물 TOP 20 · 재료 차익. 추천·알림만 하며 구매·게임 입력은 하지 않는다.

공식 노점 검색은 '포함' 일치이고 목록 이름에 제련이 붙어 있어('+10인피니티 웨폰 쉐도우'), '+10'을 붙인 이름으로 검색하면
+10 매물만 온다(2026-09-27 실제 응답 확인, 띄어 쓰면 0건). 그래도 결과는 아이템 번호·제련·이름으로 다시 검증한다.
비교 단위: 같은 서버 + 같은 아이템 번호 + 정확히 +10. 랜덤옵션·인챈트는 평가에 쓰지 않는다(옵션 가치 미반영).
기준가: 같은 아이템 +10의 최근 7일 공식 거래 평균. 공식 거래 이력은 '+10이름'으로 조회하면 제련별로 따로 나온다
(2026-09-27 확인: +10 인피니티 웨폰 평균 약 1.5억, 제련 없음 약 2,900만). 공식 자료는 날짜별 최저·평균·최고만 주고
거래 건수는 없으므로 '최근 7일(오늘 포함, 미래 날짜 제외) 날짜별 평균의 평균'이며, 거래일이 3일 미만이면 비교 자료 부족.
"""
import datetime
import json
import re
import time
import threading

# 같은 검색 캐시의 갱신만 직렬화한다. GUI의 캐시 읽기와 DB 잠금은 막지 않는다.
_CACHE_REFRESH_LOCK = threading.RLock()

from core.item_category import category
from core.vend_ocr import is_own_listing
from services import gnjoy_market as G

FAMILIES = ['인피니티', '익스피리언스', '올마이티', '듀러블', '메가 블리츠', '세퍼레이트 리로드', '퍼펙트 사이즈', '피지컬 파워',
            '매지컬 스펠', '매지컬 마스터', '풀 페니트레이션', '풀 템페스트', '스펠 캐스터', '트루 젬스톤', '앱솔브']
EXCLUDE_WORDS = ('스톤', '의상', '카드', '마법서', '조합서', '선택상자', '뚝딱상자', '상자')
REFINE = 10
BASE_DAYS = 7              # 기준가: 최근 7일 거래 평균
MIN_DAYS = 3               # 7일 중 거래일이 이보다 적으면 비교 자료 부족
DEFAULT_PERCENT = 15
TOP_N = 20
MAX_PAGES = 5              # 아이템당 +10 검색 최대 쪽 수(쪽당 10건) — 넘으면 '조회한 매물 기준'
PAGE_TTL = 30 * 60         # 같은 검색·쪽 응답 재사용
ITEM_INTERVAL = 10 * 60    # 아이템별 확인 주기 최소
ZERO_LOW, ZERO_HIGH = 0.07, 0.13          # 기준가의 약 1/10 → '0 누락 의심'
MIN_ARB_PROFIT = 1_000_000
_COORD = re.compile(r'(?<!\d)(\d{2,3})\s*[, ]\s*(\d{2,3})(?!\d)')


def _norm(s):
    return re.sub(r'\s+', '', s or '')


def shadow_targets(item_db, families=FAMILIES):
    """ItemDB 에서 실제 쉐도우 장비(분류 '쉐도우 장비', 이름이 '… 쉐도우'로 끝남)만. 상자·마법서 등 제외."""
    observed = getattr(item_db, 'categories', {}) or {}
    out = []
    for iid, it in (getattr(item_db, 'items', {}) or {}).items():
        name = (it or {}).get('name') or '' if isinstance(it, dict) else ''
        if not name.endswith('쉐도우') or name.startswith('+'):
            continue
        fam = max((f for f in families if name.startswith(f + ' ')), key=len, default=None)
        # 제외 단어는 계열 이름을 뗀 나머지에서만 본다('트루 젬스톤'의 '스톤'은 계열 이름)
        if fam and any(w in name[len(fam):] for w in EXCLUDE_WORDS):
            continue
        if fam and category(it, observed.get(iid)) == '쉐도우 장비':
            out.append(dict(item_id=int(iid), name=name, family=fam))
    return sorted(out, key=lambda t: (families.index(t['family']), t['name']))


def is_plus10(listing, target):
    """같은 아이템 번호 + 이름 앞 제련이 정확히 +10 + 이름이 대상과 같음('+10 … 선택상자' 등 제외)."""
    refine, _grade, base, _slots = G.split_name(listing.get('name') or listing.get('listing_name') or '')
    return listing.get('item_id') == target['item_id'] and refine == REFINE and _norm(base) == _norm(target['name'])


def shop_coords(shop):
    m = _COORD.search(shop or '')
    return '%s %s' % m.groups() if m else ''


class ShadowScanner:
    """+10 검색 쪽 캐시(30분)와 아이템 순환 확인(10분 이상 간격). 요청은 shared_client() 한 곳으로."""

    def __init__(self, store, client=None, clock=time.time):
        self.store, self.client, self.clock = store, client or G.shared_client(), clock
        with store.lock:
            store.conn.executescript('''
            CREATE TABLE IF NOT EXISTS shadow_pages (server TEXT, term TEXT, page INTEGER, fetched_at REAL, last INTEGER,
                rows TEXT, PRIMARY KEY (server, term, page));
            CREATE TABLE IF NOT EXISTS shadow_alert_seen (server TEXT, item_id INTEGER, shop TEXT, price INTEGER,
                seen_at REAL, PRIMARY KEY (server, item_id, shop, price));
            CREATE TABLE IF NOT EXISTS shadow_history (server TEXT, term TEXT, fetched_at REAL, rows TEXT,
                PRIMARY KEY (server, term));''')
            store.conn.commit()

    @staticmethod
    def term(target):
        return '+%d%s' % (REFINE, target['name'])

    def _page(self, server, term, page, network=True):
        if not network:
            return self._page_cached(server, term, page, False)
        with _CACHE_REFRESH_LOCK:
            return self._page_cached(server, term, page, True)

    def _page_cached(self, server, term, page, network=True):
        with self.store.lock:
            r = self.store.conn.execute('SELECT fetched_at, last, rows FROM shadow_pages WHERE server=? AND term=? AND page=?',
                                        (server, term, page)).fetchone()
        if r and (not network or self.clock() - r[0] < PAGE_TTL):
            return r[0], r[1], json.loads(r[2]), False
        if not network:
            return None, None, None, False
        rows, last = G.parse_listings(self.client.get('itemDealList.asp', dict(svrID=server, itemFullName=term, itemOrder='',
                                                                              inclusion='', curpage=page), 'dealSearch.asp'))
        now = self.clock()
        with self.store.lock:
            self.store.conn.execute('INSERT OR REPLACE INTO shadow_pages VALUES (?,?,?,?,?,?)',
                                    (server, term, page, now, last, json.dumps(rows, ensure_ascii=False)))
            self.store.conn.commit()
        return now, last, rows, True

    def _history(self, server, term, network=True):
        if not network:
            return self._history_cached(server, term, False)
        with _CACHE_REFRESH_LOCK:
            return self._history_cached(server, term, True)

    def _history_cached(self, server, term, network=True):
        """'+10이름' 공식 거래 이력 1쪽(최근 10일) — 30분 재사용. → (받은 시각, 행, 새로 요청했는지)"""
        with self.store.lock:
            r = self.store.conn.execute('SELECT fetched_at, rows FROM shadow_history WHERE server=? AND term=?',
                                        (server, term)).fetchone()
        if r and (not network or self.clock() - r[0] < PAGE_TTL):
            return r[0], json.loads(r[1]), False
        if not network:
            return None, [], False
        rows, _ = self.client.history(server, term, pages=1)
        now = self.clock()
        with self.store.lock:
            self.store.conn.execute('INSERT OR REPLACE INTO shadow_history VALUES (?,?,?,?)',
                                    (server, term, now, json.dumps(rows, ensure_ascii=False)))
            self.store.conn.commit()
        return now, rows, True

    def fetch(self, server, target, stop=None, network=True, stop_on_match=True):
        """→ dict(listings[+10 검증된 판매·구매], pages 받은 쪽, last 마지막 쪽, fetched_at, history +10 거래 이력,
        history_at) — network=False 면 캐시만.

        공식 검색은 **가격 낮은 순**이라 이 아이템의 첫 매물이 보이면 그게 최저가다. 순위는 최저가로 정해지므로
        stop_on_match=True면 거기서 멈춰 요청을 아낀다(그 뒤 쪽은 더 비싼 매물뿐). 전체 매물 수가 필요하면 끈다.
        """
        term, rows, page, last, at, requested = self.term(target), [], 1, 1, None, False
        while page <= min(last, MAX_PAGES):
            if stop and stop.is_set() and page > 1:
                break
            got_at, got_last, got, fresh = self._page(server, term, page, network)
            if got is None:
                break
            requested = requested or fresh
            at = min(at or got_at, got_at)
            last, rows = max(last, got_last or 1, page), rows + got   # 마지막 쪽 응답은 자기 번호를 링크로 안 보여 줌
            page += 1
            if stop_on_match and any(is_plus10(r, target) for r in rows):
                break                                                 # 최저가를 찾음 — 더 받지 않는다
        hist_at, hist = None, []
        matched = [r for r in rows if is_plus10(r, target)]
        # 거래 이력(기준가)은 **판매 매물이 있을 때만** 쓸모가 있다 — 없으면 요청하지 않는다
        if at is not None and any(r.get('kind') == 'sale' for r in matched) and not (stop and stop.is_set()):
            hist_at, hist, fresh = self._history(server, term, network)
            requested = requested or fresh
        if requested:                   # 실제로 요청했을 때만 '확인함' — 캐시로 답한 것은 순환 순서를 바꾸지 않는다
            self.store.set_meta('shadow_checked:%s:%s' % (server, target['item_id']), self.clock())
        return dict(listings=matched, pages=page - 1, last=last if at else None, early=bool(stop_on_match and matched),
                    fetched_at=at, history=hist, history_at=hist_at)

    def next_due(self, server, targets):
        """가장 오래 확인 안 한 대상 하나(10분 간격, 30분 캐시가 있으면 요청하지 않으므로 사실상 30분마다)."""
        now, best = self.clock(), None
        for t in targets:
            last = float(self.store.meta('shadow_checked:%s:%s' % (server, t['item_id']), '0'))
            at = self.fetch(server, t, network=False)['fetched_at']
            if now - last < ITEM_INTERVAL or (at and now - at < PAGE_TTL):
                continue
            if best is None or last < best[0]:
                best = (last, t)
        return best and best[1]

    def seen(self, server, row):
        with self.store.lock:
            return self.store.conn.execute('SELECT 1 FROM shadow_alert_seen WHERE server=? AND item_id=? AND shop=? AND price=?',
                                           (server, row['item_id'], row['shop'], row['price'])).fetchone() is not None

    def mark_seen(self, server, row):
        with self.store.lock:
            cur = self.store.conn.execute('INSERT OR IGNORE INTO shadow_alert_seen VALUES (?,?,?,?,?)',
                                          (server, row['item_id'], row['shop'], row['price'], self.clock()))
            self.store.conn.commit()
        return bool(cur.rowcount)


def seven_day_base(history, today=None):
    """(최근 7일 날짜별 평균의 평균, 거래일 수). 오늘 포함, 미래 날짜·평균 0 제외."""
    today = today or datetime.date.today()
    start = (today - datetime.timedelta(days=BASE_DAYS - 1)).isoformat()
    days = [h['avg'] for h in history or [] if start <= h.get('day', '') <= today.isoformat() and (h.get('avg') or 0) > 0]
    return (sum(days) / len(days) if days else None), len(days)


def evaluate(snapshots, targets, percent=DEFAULT_PERCENT, own=(), details=None, today=None):
    """snapshots: {item_id: fetch 결과}. → dict(top[최대 20], candidates 전체 적격, insufficient 비교 자료 부족 매물 수,
    partial[뒤 쪽이 남은 대상], unchecked[미조회 대상]). 기준가 = +10 최근 7일 거래 평균. 옵션·인챈트는 보지 않는다."""
    details = details or {}
    cands, insufficient, partial, unchecked = [], 0, [], []
    for t in targets:
        snap = snapshots.get(t['item_id'])
        if not snap or not snap.get('fetched_at'):
            unchecked.append(t['name'])
            continue
        if snap.get('last') and snap['last'] > snap['pages'] and not snap.get('early'):
            partial.append(t['name'])        # 최저가를 찾아 일부러 멈춘 경우는 자료가 빠진 것이 아니다
        mine = [o for o in own if o.get('item_id') == t['item_id'] or o.get('name') == t['name']]
        sales = [l for l in snap['listings'] if l.get('kind') == 'sale' and l.get('price')
                 and not any(o.get('shop') and is_own_listing(l, o.get('price'), o['shop']) for o in mine)]
        for i, l in enumerate(sales):
            l['_shop'] = l.get('shop') or '#%d' % i               # 이름 없는 상점은 서로 다른 상점으로
        base, days = seven_day_base(snap.get('history'), today)
        seen = set()
        for l in sales:
            dup = (l['_shop'], l['price'])
            if dup in seen:                                       # 같은 상점·같은 가격 여러 개는 한 줄
                continue
            seen.add(dup)
            if days < MIN_DAYS:
                insufficient += 1
                continue
            discount = (base - l['price']) / base * 100
            if discount + 1e-9 < percent:
                continue
            ratio = l['price'] / base
            det = details.get(l.get('ssi')) or {}
            row = dict(item_id=t['item_id'], name=t['name'], family=t['family'], price=l['price'], base=base,
                       discount=discount, diff=base - l['price'], shop=l.get('shop') or '', days=days,
                       fetched_at=snap['fetched_at'], ssi=l.get('ssi') or '', map_code=l.get('map_code') or '',
                       listing_name=l.get('name') or '', coords=shop_coords(l.get('shop')), map=det.get('map', ''),
                       count=sum(1 for x in sales if x['_shop'] == l['_shop'] and x['price'] == l['price']),
                       zero=ZERO_LOW <= ratio <= ZERO_HIGH)
            row['reason'] = '동일 +10 장비의 최근 7일 거래 평균(거래 %d일)보다 %.0f%% 저렴%s' % (
                days, discount, ' · 0 누락 의심(가격 입력 실수로 단정하지 않음)' if row['zero'] else '')
            cands.append(row)
    cands.sort(key=lambda r: (-r['discount'], -r['diff']))
    for i, r in enumerate(cands[:TOP_N], 1):
        r['rank'] = i
    return dict(top=cands[:TOP_N], candidates=cands, insufficient=insufficient, partial=partial, unchecked=unchecked)


def arbitrage(store, server, min_profit=MIN_ARB_PROFIT, now=None):
    """저장된 최신 노점 목록(아이템별 마지막 스냅샷)에서 같은 아이템의 판매노점을 사서 구매노점에 파는 차익.
    제련·슬롯 표시가 있는 장비 줄은 제외(재료만). 판매 수량과 구매노점 남은 매입 수량을 모두 반영하고,
    싼 판매부터 비싼 매입 순으로 한 번씩만 짝지어 같은 수량을 두 번 세지 않는다.
    수수료: 구매노점에 팔 때의 수수료·세금 근거를 확인하지 못해 반영하지 않음. 이동비 제외."""
    with store.lock:
        snaps = store.conn.execute('SELECT item_id, MAX(fetched_at) FROM listings WHERE server=? GROUP BY item_id',
                                   (server,)).fetchall()
    out = []
    for iid, at in snaps:
        _, ls = store.latest_listings(server, iid)
        ls = [l for l in ls if l.get('qty') and l.get('price')
              and not (G.split_name(l.get('listing_name') or '')[0] or G.split_name(l.get('listing_name') or '')[3])]
        sells = sorted(([l['price'], l['qty'], l.get('shop') or ''] for l in ls if l['kind'] == 'sale'), key=lambda x: x[0])
        buys = sorted(([l['price'], l['qty'], l.get('shop') or ''] for l in ls if l['kind'] == 'buy'), key=lambda x: -x[0])
        qty = cost = revenue = 0
        sell_shops, buy_shops = [], []
        i = j = 0
        while i < len(sells) and j < len(buys) and sells[i][0] < buys[j][0]:
            q = min(sells[i][1], buys[j][1])
            qty += q
            cost += q * sells[i][0]
            revenue += q * buys[j][0]
            for shops, name in ((sell_shops, sells[i][2]), (buy_shops, buys[j][2])):
                if name not in shops:
                    shops.append(name)
            sells[i][1] -= q
            buys[j][1] -= q
            if sells[i][1] == 0:
                i += 1
            if buys[j][1] == 0:
                j += 1
        profit = revenue - cost
        if qty and profit >= min_profit:
            name = next((l.get('listing_name') for l in ls if l.get('listing_name')), '') or str(iid)
            out.append(dict(item_id=iid, name=name, unit_cost=cost / qty, unit_buy=revenue / qty, qty=qty, cost=cost,
                            profit=profit, sell_shops=sell_shops, buy_shops=buy_shops, fetched_at=at))
    return sorted(out, key=lambda r: -r['profit'])


def snapshot_all(scanner, server, targets):
    """캐시만 읽어 {item_id: fetch 결과}와 저장된 상세 {ssi: 상세} (요청 없음)."""
    snaps = {t['item_id']: scanner.fetch(server, t, network=False) for t in targets}
    details = {}
    for s in snaps.values():
        for l in s['listings']:
            if l.get('ssi'):
                _, d = scanner.store.detail(server, l['ssi'])
                if d:
                    details[l['ssi']] = d
    return snaps, details


def location_text(row):
    """위치: 상세 보기로 받은 맵 > 상점명에 적힌 좌표(구분 표시) > 없음."""
    if row.get('map'):
        return '맵 %s' % row['map']
    if row.get('coords'):
        return '상점명에 기재된 위치 %s' % row['coords']
    return '-'


def shadow_alert_text(row):
    return '+10 %s · %s Z (기준 %s Z)\n%s\n상점: %s%s' % (
        row['name'], format(row['price'], ','), format(int(row['base']), ','), row['reason'], row['shop'] or '이름 없음',
        (' · ' + location_text(row)) if location_text(row) != '-' else '')
