"""라그나로크 공식 홈페이지 '아이템 거래 정보'(https://ro.gnjoy.com/itemdeal/)에서 노점 시세를 받아 쌓고 비교한다.

- 지금 노점: itemDealList.asp — 서버·아이템·수량·가격·상점 이름·판매/구매 (한 페이지 10건, 가격 낮은 순)
- 과거 시세: itemPriceExtendList.asp — 날짜별 노점 거래 최저/평균/최고가 (한 페이지 10일, 최근 120일)
- 아이템 번호는 아이콘 파일 이름(.../984.png)에서 읽어 비슷한 이름(오리데오콘 원석 등)을 걸러 낸다.
- 홈페이지가 요청이 많으면 429 로 막으므로 요청 사이 간격을 두고, 막히면 멈춘다(RateLimited).
받은 자료는 data/vending.db 에 날짜별로 쌓아 120일이 지나도 남긴다.
"""
import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://ro.gnjoy.com/itemdeal/'
SERVERS = {'129': '바포메트', '229': '이그드라실', '529': '다크로드', '729': '이프리트'}
USER_AGENT = 'Mozilla/5.0 (RagnaMate personal price tool)'
MIN_INTERVAL = 1.2            # 요청 사이 기본 간격(초). 아래 FLOOR 보다 짧아지지 않는다
FLOOR_INTERVAL = 1.0          # 이보다 빠르게는 보내지 않는다(공식 사이트 보호)
MAX_INTERVAL = 6.0            # 429를 받으면 간격을 늘려 가며 여기까지 물러난다
SPEEDUP_AFTER = 12            # 연속 성공이 이만큼 쌓이면 간격을 조금 줄인다
RATE_PAUSE_SEC = 300


class RateLimited(Exception):
    """홈페이지가 '요청이 너무 많음(429)'으로 막음 — 잠시 뒤에 다시."""


def _text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def _num(s):
    s = re.sub(r'[^\d]', '', s or '')
    return int(s) if s else None


# ---------------------------------------------------------------- 파서 (홈페이지 HTML → 값)

_ROW = re.compile(r'<tr>(.*?)</tr>', re.S)


def parse_listings(page):
    """지금 노점 목록 → ([{item_id, name, qty, price, shop, kind('sale'|'buy'), server}], 마지막 페이지 번호)."""
    out = []
    body = page[page.find('<tbody'):page.find('</tbody>')] if '<tbody' in page else ''
    for row in _ROW.findall(body):
        icon = re.search(r'/(\d+)\.png', row)
        name = re.search(r'<span>(.*?)</span>', row, re.S)
        qty = re.search(r'class="quantity">(.*?)</td>', row, re.S)
        price = re.search(r'class="price">(.*?)</td>', row, re.S)
        shop = re.search(r'class="shop (sale|buy)">(.*?)</td>', row, re.S)
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if not (icon and price and shop):
            continue
        out.append(dict(item_id=int(icon.group(1)), name=_text(name.group(1)) if name else '',
                        qty=_num(_text(qty.group(1))) if qty else None, price=_num(_text(price.group(1))),
                        shop=_text(shop.group(2)), kind=shop.group(1), server=_text(cells[0]) if cells else ''))
        location=re.search(r'class="(?:location|map)"[^>]*>(.*?)</td>',row,re.S)
        if location:out[-1]['location']=_text(location.group(1))
        view = re.search(r"CallItemDealView\((\d+)\s*,\s*(\d+)\s*,\s*'(\d+)'", row)
        if view:                                   # 상세 보기(카드·옵션·맵)에 필요한 번호
            out[-1].update(map_code=view.group(2), ssi=view.group(3))
    return out, _last_page(page)


# 등급이 있는 장비는 공식 목록에 '[UNIQUE]+12러쉬 가드[1]'처럼 등급이 맨 앞에 온다(2026-09-30 실제 응답 확인).
# '+12 [C] 이름' 꼴도 예전 자료·손 입력용으로 읽는다.
_NAME = re.compile(r'^\s*(?:\[([A-Z]+)\]\s*)?(?:\+(\d{1,2}))?\s*(?:\[([A-Z])\]\s*)?(.*?)\s*(?:\[(\d)\])?\s*$')


def split_name(name):
    """공식 목록 이름 '+9일루시온 레그 B타입[1]' → (제련 9, 등급 '', '일루시온 레그 B타입', 슬롯 1).
    '[UNIQUE]+12러쉬 가드[1]' → (12, 'UNIQUE', '러쉬 가드', 1). 제련·슬롯 표시가 없으면 0, None."""
    m = _NAME.match(name or '')
    if not m:
        return 0, '', (name or '').strip(), None
    return (int(m.group(2) or 0), m.group(1) or m.group(3) or '', m.group(4).strip(),
            int(m.group(5)) if m.group(5) else None)


def join_name(refine, grade, base, slots):
    """split_name 의 반대 — 공식 목록과 같은 꼴('[UNIQUE]+12러쉬 가드[1]'). 거래 이력은 이 전체 이름이 정확해야 나온다."""
    return '%s%s%s%s' % ('[%s]' % grade if grade else '', '+%d' % refine if refine else '', base.strip(),
                         '[%d]' % slots if slots is not None else '')


def parse_deal_view(page):
    """노점 상세 보기 → {map, shop, character, cards[이름], options[글], element, maker}. 못 읽으면 빈 값."""
    def cell(label):
        m = re.search(r'<th[^>]*>\s*%s.*?</th>\s*<td[^>]*>(.*?)</td>' % label, page, re.S)
        return m.group(1) if m else ''

    def items(label):
        return [_text(x) for x in re.findall(r'<li[^>]*>(.*?)</li>', cell(label), re.S) if _text(x) not in ('', '-')]
    where = re.search(r'\(([^()<>]+)\)', cell('서버'))
    shop = _text(cell('노점 이름'))
    who = re.match(r'(.*)\(([^()]*)\)\s*$', shop)
    return dict(map=where.group(1).strip() if where else '', shop=(who.group(1) if who else shop).strip(),
                character=who.group(2).strip() if who else '', cards=items('슬롯정보'), options=items('랜덤옵션'),
                element=_text(cell('속성')), maker=_text(cell('제조자')).strip('-').strip())


def parse_history(page):
    """과거 시세 → ([{day 'YYYY-MM-DD', min, avg, max}], 마지막 페이지 번호)."""
    out = []
    body = page[page.find('<tbody'):page.find('</tbody>')] if '<tbody' in page else ''
    for row in _ROW.findall(body):
        day = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', row)
        prices = [_num(_text(p)) for p in re.findall(r'class="price">(.*?)</td>', row, re.S)]
        if day and len(prices) >= 3:
            out.append(dict(day='%s-%s-%s' % day.groups(), min=prices[0], avg=prices[1], max=prices[2]))
    return out, _last_page(page)


def _last_page(page):
    """페이지 이동 영역의 CallXxx(..., N) 들 중 가장 큰 N (없으면 1쪽뿐)."""
    i = page.find('pagingNav')
    nums = [int(n) for n in re.findall(r'Call\w+\([^)]*?(\d+)\)', page[i:])] if i >= 0 else []
    return max(nums) if nums else 1


# ---------------------------------------------------------------- 홈페이지 요청 (천천히, 막히면 멈춤)

class GnjoyClient:
    """공식 홈페이지 요청 한 곳. 간격을 지키되 **쉴 때만 쉬고** 응답 시간은 간격에 포함한다.

    - 연결을 다시 쓴다(keep-alive): 요청마다 TCP·TLS를 새로 맺지 않아 한 번에 수백 ms를 아낀다.
    - 간격은 상황에 맞춘다: 잘 되면 FLOOR_INTERVAL 까지 조금씩 당기고, 429를 받으면 두 배로 물러난다.
      막히면 5분 전체 중지라 결국 더 느려지므로, 빠르게 가되 막히지 않는 쪽을 택한다.
    """

    def __init__(self, interval=MIN_INTERVAL, opener=None, floor=FLOOR_INTERVAL):
        self.interval = max(interval, 0.0)
        self.floor = min(floor, self.interval) if interval else 0.0
        self._last = 0.0
        self._lock = threading.Lock()
        self._open = opener or self._urlopen
        self.requests = 0
        self._ok_streak = 0
        self._paused_until = 0.0
        self._session = None

    @property
    def retry_after(self):
        return max(0.0, self._paused_until - time.monotonic())

    def _urlopen(self, url, referer):
        headers = {'User-Agent': USER_AGENT, 'Referer': referer, 'X-Requested-With': 'XMLHttpRequest'}
        session = self._http()
        if session is not None:                       # 연결 재사용(handshake 생략)
            r = session.get(url, headers=headers, timeout=20)
            if r.status_code == 429:
                raise RateLimited('공식 홈페이지가 잠시 요청을 막았습니다(429). 몇 분 뒤에 다시 받으세요.')
            r.raise_for_status()
            r.encoding = r.encoding or 'utf-8'
            return r.text
        req = urllib.request.Request(url, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited('공식 홈페이지가 잠시 요청을 막았습니다(429). 몇 분 뒤에 다시 받으세요.') from e
            raise

    def _http(self):
        """requests 세션(연결 재사용). 없으면 None → urllib 로 돌아간다."""
        if self._session is None:
            try:
                import requests
                self._session = requests.Session()
                self._session.headers.update({'Connection': 'keep-alive'})
            except Exception:
                self._session = False
        return self._session or None

    def get(self, path, params, referer):
        with self._lock:                                         # 여러 곳에서 불러도 간격을 지킨다
            if self.retry_after:
                raise RateLimited('공식 홈페이지 요청을 %d초 뒤에 재개합니다.' % self.retry_after)
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()        # 간격은 '보낸 시각' 기준 — 응답을 기다린 시간은 이미 쉰 것
            try:
                page = self._open(BASE + path + '?' + urllib.parse.urlencode(params), BASE + referer)
            except RateLimited:
                self._slow_down()
                raise
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    self._slow_down()
                    raise RateLimited('공식 홈페이지 요청을 5분 중지합니다(429).') from exc
                raise
            finally:
                self.requests += 1
            self._speed_up()
            return page

    def _slow_down(self):
        """막혔다 — 5분 쉬고, 다시 시작할 때는 더 천천히."""
        self._paused_until = time.monotonic() + RATE_PAUSE_SEC
        self._ok_streak = 0
        if self.interval:
            self.interval = min(MAX_INTERVAL, max(MIN_INTERVAL, self.interval * 2))

    def _speed_up(self):
        """잘 되는 동안만 조금씩 당긴다(바닥은 floor)."""
        self._ok_streak += 1
        if self.interval and self._ok_streak >= SPEEDUP_AFTER and self.interval > self.floor:
            self._ok_streak = 0
            self.interval = max(self.floor, self.interval * 0.85)

    def listings(self, server, name, max_pages=2, with_more=False, stop=None):
        """이름이 '포함'된 아이템 전부가 가격 낮은 순으로 온다(공식 검색에 '정확히 일치' 옵션 없음 — 2026-09-27 확인).
        with_more=True 면 (rows, 받지 않은 뒤 페이지가 더 있는지)."""
        rows, page, last = [], 1, 1
        while page <= min(last, max_pages) and not (stop and stop.is_set() and rows):   # 닫을 때 쪽 사이에서 멈춤
            got, last = parse_listings(self.get('itemDealList.asp', dict(svrID=server, itemFullName=name, itemOrder='',
                                                                            inclusion='', curpage=page),
                                                'dealSearch.asp'))
            rows += got
            page += 1
        return (rows, last > max_pages) if with_more else rows

    def deal_view(self, server, name, map_code, ssi):
        """노점 한 건의 상세(카드·랜덤옵션·맵). 요청 1번(간격·429 규칙 동일)."""
        return parse_deal_view(self.get('itemDealView.asp', dict(svrID=server, itemFullName=name, mapID=map_code,
                                                                  ssi=ssi, curpage=1), 'itemDealList.asp'))

    def history(self, server, name, pages=1, stop=None):
        rows, page, last = [], 1, 1
        while page <= min(last, pages) and not (stop and stop.is_set() and page > 1):
            got, last = parse_history(self.get('itemPriceExtendList.asp',
                                               dict(svrID=server, itemFullName=name, curpage=page), 'quoteSearch.asp'))
            rows += got
            page += 1
        return rows, last


_shared_client = None
_shared_lock = threading.Lock()


def shared_client():
    """앱 전체가 같은 요청 간격을 지키도록 하나만 쓴다(노점 비교 창·노점 개설 패널 공용)."""
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = GnjoyClient()
    return _shared_client


# ---------------------------------------------------------------- 기록 저장 (data/vending.db)

class VendingStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS history (server TEXT, item_id INTEGER, day TEXT, min INTEGER, avg INTEGER,
                max INTEGER, fetched_at REAL, PRIMARY KEY (server, item_id, day));
            CREATE TABLE IF NOT EXISTS listings (server TEXT, item_id INTEGER, price INTEGER, qty INTEGER, shop TEXT,
                kind TEXT, fetched_at REAL);
            CREATE INDEX IF NOT EXISTS ix_listings ON listings(server, item_id, fetched_at);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);''')
            cols = {r[1] for r in self.conn.execute('PRAGMA table_info(listings)')}
            for col in ('location', 'name', 'ssi', 'map_code'):            # 예전 DB에 없던 칸
                if col not in cols:
                    self.conn.execute("ALTER TABLE listings ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % col)
            self.conn.execute('CREATE TABLE IF NOT EXISTS deal_details (server TEXT, ssi TEXT, fetched_at REAL, '
                              'data TEXT, PRIMARY KEY (server, ssi))')
            self.conn.commit()

    def meta(self, key, default=None):
        with self.lock:
            r = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key, value):
        with self.lock:
            self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))
            self.conn.commit()

    def save_history(self, server, item_id, rows, now=None):
        now = now or time.time()
        with self.lock:
            self.conn.executemany('INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?,?)',
                                  [(server, item_id, r['day'], r['min'], r['avg'], r['max'], now) for r in rows])
            self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                              ('history_checked:%s:%s' % (server, item_id), str(now)))
            self.conn.commit()

    def save_listings(self, server, item_id, rows, now=None):
        """지금 노점 스냅샷을 통째로 남긴다(같은 시각 = 한 번 받은 목록)."""
        now = now or time.time()
        with self.lock:
            self.conn.executemany('INSERT INTO listings(server,item_id,price,qty,shop,kind,fetched_at,location,name,ssi,map_code) '
                                  'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                  [(server, item_id, r['price'], r['qty'], r['shop'], r['kind'], now, r.get('location') or '',
                                    r.get('name') or '', r.get('ssi') or '', r.get('map_code') or '') for r in rows])
            if not rows:                                        # '노점 없음'도 받은 기록으로 남긴다
                self.conn.execute('INSERT INTO listings(server,item_id,price,qty,shop,kind,fetched_at) VALUES (?,?,NULL,NULL,NULL,NULL,?)', (server, item_id, now))
            self.conn.commit()

    def history(self, server, item_id):
        with self.lock:
            return [dict(day=d, min=a, avg=b, max=c) for d, a, b, c in self.conn.execute(
                'SELECT day, min, avg, max FROM history WHERE server=? AND item_id=? ORDER BY day DESC',
                (server, item_id))]

    def latest_listings(self, server, item_id):
        """(받은 시각, [노점...]) — 받은 적 없으면 (None, [])."""
        with self.lock:
            r = self.conn.execute('SELECT MAX(fetched_at) FROM listings WHERE server=? AND item_id=?',
                                  (server, item_id)).fetchone()
            if not r or r[0] is None:
                return None, []
            rows = self.conn.execute('SELECT price, qty, shop, kind, location, name, ssi, map_code FROM listings '
                                     'WHERE server=? AND item_id=? AND fetched_at=? AND price IS NOT NULL ORDER BY price',
                                     (server, item_id, r[0]))
            out = []
            for p, q, s, k, loc, name, ssi, code in rows:
                d = dict(price=p, qty=q, shop=s, kind=k)
                d.update({key: v for key, v in (('location', loc), ('listing_name', name), ('ssi', ssi),
                                                  ('map_code', code)) if v})
                out.append(d)
            return r[0], out

    def detail(self, server, ssi):
        """(받은 시각, 상세 dict) 또는 (None, None)."""
        with self.lock:
            r = self.conn.execute('SELECT fetched_at, data FROM deal_details WHERE server=? AND ssi=?',
                                  (server, ssi)).fetchone()
        return (r[0], json.loads(r[1])) if r else (None, None)

    def save_detail(self, server, ssi, data, now=None):
        with self.lock:
            self.conn.execute('INSERT OR REPLACE INTO deal_details VALUES (?,?,?,?)',
                              (server, ssi, now or time.time(), json.dumps(data, ensure_ascii=False)))
            self.conn.commit()

    def history_fetched_at(self, server, item_id):
        with self.lock:
            checked = self.conn.execute('SELECT value FROM meta WHERE key=?',
                                       ('history_checked:%s:%s' % (server, item_id),)).fetchone()
            r = self.conn.execute('SELECT MAX(fetched_at) FROM history WHERE server=? AND item_id=?',
                                  (server, item_id)).fetchone()
        return max(float(checked[0]) if checked else 0, r[0] or 0) or None

    def close(self):
        with self.lock:
            self.conn.close()


# ---------------------------------------------------------------- 비교

def _mean(values):
    values = [v for v in values if v]
    return sum(values) / len(values) if values else None


def compare(history, listings, today=None):
    """과거 시세(날짜별 평균가) ↔ 지금 노점(판매 최저가) 비교.
    반환: now_min, sellers, buy_max, avg7/avg30/avg120(날짜별 평균가의 평균), days, diff30(%), verdict, suggest."""
    import datetime
    today = today or datetime.date.today()
    by_day = {h['day']: h for h in history}

    def avg_since(n):
        start = (today - datetime.timedelta(days=n - 1)).isoformat()
        return _mean([h['avg'] for d, h in by_day.items() if start <= d <= today.isoformat()])
    sales = [l for l in listings if l['kind'] == 'sale' and l['price']]
    buys = [l for l in listings if l['kind'] == 'buy' and l['price']]
    now_min = min((l['price'] for l in sales), default=None)
    out = dict(now_min=now_min, sellers=len(sales), buy_max=max((l['price'] for l in buys), default=None),
               avg7=avg_since(7), avg30=avg_since(30), avg120=avg_since(120), days=len(by_day),
               last_day=max(by_day) if by_day else None)
    base = out['avg30'] or out['avg7'] or out['avg120']
    if now_min and base:
        diff = (now_min - base) / base * 100
        out['diff'] = diff
        out['verdict'] = ('평소보다 쌈' if diff <= -10 else '평소보다 비쌈' if diff >= 10 else '평소 수준')
    else:
        out['diff'] = None
        out['verdict'] = '지금 판매 노점 없음' if base else ('거래 기록 없음' if not by_day else '')
    # 팔 때 참고: 최근 7일(없으면 30일) 평균 거래가와 지금 최저가 중 낮은 쪽 바로 아래
    ref = out['avg7'] or out['avg30']
    out['suggest'] = int(min(x for x in (ref, now_min) if x) * 0.99) if (ref or now_min) else None
    return out


# ---------------------------------------------------------------- 노점 개설 추천가

def recommend_price(cmp):
    """추천 판매가 = min(지금 판매 최저가 × 0.99, 최근 7일 평균 거래가), 1만 단위 내림.
    판매 노점이 없으면 7일 → 30일 → 받은 기간 평균. 자료가 없으면 None."""
    now_min = cmp.get('now_min')
    cands = [now_min * 0.99] if now_min else []
    avg = cmp.get('avg7') or (None if now_min else (cmp.get('avg30') or cmp.get('avg120')))
    if avg:
        cands.append(avg)
    if not cands:
        return None
    v = int(min(cands))
    return v - v % 10000 if v >= 10000 else v


def judge_price(mine, rec, now_min=None, cheap_pct=-15, dear_pct=3):
    """내 가격 판단 → (종류 'danger'|'warn'|'ok'|'muted', 글). 추천가보다 15% 이상 싸면 '너무 쌈'."""
    if not mine or not rec:
        return 'muted', ''
    d = (mine - rec) / rec * 100
    if d <= cheap_pct:
        return 'danger', '너무 쌈 %d%%' % round(d)
    if d > dear_pct:
        if now_min and mine > now_min:
            return 'warn', '최저가보다 %d%% 비쌈' % round((mine - now_min) / now_min * 100)
        return 'warn', '추천가보다 %d%% 비쌈' % round(d)
    return 'ok', '적정'
