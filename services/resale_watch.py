"""고가 매물의 보수적 재판매 후보. 자동 구매 없이 저장 시세와 공식 검색만 사용한다."""
import datetime
import hashlib
import json
import math
import time

from core.vend_ocr import is_own_listing, real_amount
from services import gnjoy_market as G
from services import shadow_deals as SD

DEFAULT_CONFIG = dict(enabled=False, server='129', shadows=True, targets=[],
                      min_base=100_000_000, max_ratio=80, min_profit=1_000_000, haircut=5)
MAX_TARGETS = 20
MIN_PEER_SHOPS = 2
MAX_CARD_DETAILS = 3        # 카드·인챈트를 정한 대상: 한 번 확인할 때 후보 상세 최대 요청 수


def config(value=None):
    """잘못된 저장값은 안전한 기본값으로. 호출자/전역 중첩값을 변경하지 않는다."""
    out = dict(DEFAULT_CONFIG, targets=[])
    if isinstance(value, dict):
        for key in out:
            if key in value:
                out[key] = value[key]
    out['enabled'] = out['enabled'] is True
    out['shadows'] = out['shadows'] is True
    if out['server'] not in G.SERVERS:
        out['server'] = '129'
    for key, low, high in [('min_base', 100_000_000, 2_000_000_000), ('max_ratio', 1, 80),
                           ('min_profit', 1, 2_000_000_000), ('haircut', 0, 30)]:
        try:
            v = int(out[key])
            out[key] = max(low, min(high, v))
        except (ValueError, TypeError, OverflowError):
            out[key] = DEFAULT_CONFIG[key]
    out['targets'] = [dict(t) for t in out['targets'][:MAX_TARGETS] if isinstance(t, dict)] if isinstance(out['targets'], list) else []
    return out


def norm(text):
    return ''.join((text or '').split())


def variant(name):
    refine, grade, base, slots = G.split_name(name)
    return refine, grade, norm(base), slots


def target_from_query(item_db, query):
    """사용자가 선택한 정확한 제련/등급/슬롯을 ItemDB ID와 연결. 부분 일치 추측 금지."""
    query = query.strip()
    refine, grade, base, slots = G.split_name(query)
    hits = []
    for iid, item in item_db.items.items():
        if not isinstance(item, dict):
            continue
        name = item.get('name') or ''
        _, _, db_base, db_slots = G.split_name(name)
        if norm(db_base) == norm(base) and (db_slots is None or slots == db_slots):
            hits.append((int(iid), name))
    if len(hits) != 1:
        raise ValueError('아이템 이름과 슬롯을 정확히 입력해 주세요. 예: +10인피니티 웨폰 쉐도우')
    iid, name = hits[0]
    # 공식 목록 표기: 등급이 맨 앞('[UNIQUE]+12러쉬 가드[1]') — 거래 이력은 이 전체 이름이 정확해야 나온다
    return dict(item_id=iid, name=name, query=G.join_name(refine, grade, base, slots))


def target_from_item(item_db, item_id, refine, grade, slots, cards=None, base=None):
    """자동완성으로 고른 아이템 번호 + 구성(국민 옵션 또는 사용자가 바꾼 값). 같은 이름이 여러 번호여도 번호로 정확히 등록."""
    item = item_db.items.get(item_id)
    if not isinstance(item, dict) or not item.get('name'):
        raise ValueError('아이템을 자동완성 목록에서 골라 주세요.')
    t = dict(item_id=item_id, name=item['name'], query=G.join_name(refine, grade, base or item['name'], slots))
    if cards:
        t['cards'] = list(cards)
    return t


def targets_for(item_db, cfg):
    targets = []
    if cfg['shadows']:
        targets.extend(dict(t, query=SD.ShadowScanner.term(t)) for t in SD.shadow_targets(item_db))
    for t in cfg['targets']:
        # 번호로 등록한 대상은 그 번호가 아이템 DB에 있으면 그대로 쓴다(같은 이름 여러 번호도 가능)
        item = item_db.items.get(t.get('item_id')) if isinstance(t.get('item_id'), int) else None
        if isinstance(item, dict) and item.get('name') and t.get('query'):
            checked = dict(item_id=t['item_id'], name=item['name'], query=t['query'])
            if t.get('cards'):
                checked['cards'] = list(t['cards'])
            targets.append(checked)
    unique = {}
    for t in targets:
        item = item_db.items.get(t['item_id'], {})
        if isinstance(item, dict) and any(w in item.get('description', '') for w in ('거래 불가', '거래불가', '귀속')):
            continue
        unique[(t['item_id'], variant(t['query']))] = t
    return list(unique.values())


def exact_listing(row, target):
    return row.get('item_id') == target['item_id'] and variant(row.get('name') or row.get('listing_name')) == variant(target['query'])


def base_price(history, today=None):
    today = today or datetime.date.today()
    start = today - datetime.timedelta(days=6)
    days = {}
    for row in history:
        try:
            day = datetime.date.fromisoformat(row['day'])
            value = float(row['avg'])
        except (KeyError, ValueError, TypeError):
            continue
        if start <= day <= today and math.isfinite(value) and value > 0:
            days[day] = value
    return (sum(days.values()) / len(days) if days else None), len(days)


class ResaleScanner(SD.ShadowScanner):
    """기존 +10 검색 캐시를 재사용. 알림 중복 기록은 별도 테이블로 분리."""
    def __init__(self, store, client=None, clock=time.time):
        super().__init__(store, client, clock)
        with store.lock:
            store.conn.execute('CREATE TABLE IF NOT EXISTS resale_alert_seen '
                               '(fingerprint TEXT PRIMARY KEY, seen_at REAL, payload TEXT)')
            store.conn.commit()

    @staticmethod
    def term(target):
        return target['query']

    def fetch(self, server, target, stop=None, network=True):
        term = self.term(target)
        rows, page, last, at = [], 1, 1, None
        if network and stop and stop.is_set():
            return self.fetch(server, target, network=False)
        while page <= min(last, SD.MAX_PAGES):
            if network and stop and stop.is_set():
                break
            got_at, got_last, got, _ = self._page(server, term, page, network)
            if got is None:
                break
            at = got_at if at is None else min(at, got_at)
            last = max(last, got_last or 1, page)
            rows.extend(got)
            page += 1
        hist_at, hist = None, []
        if at is not None and not (network and stop and stop.is_set()):
            hist_at, hist, _ = self._history(server, term, network)
        return dict(listings=[dict(r) for r in rows if exact_listing(r, target)],
                    ambiguous=any(not exact_listing(r, target) for r in rows),
                    history=hist, history_at=hist_at, fetched_at=at, pages=page-1, last=last)

    def checked_key(self, server, target):
        return 'resale_checked:' + server + ':' + self.term(target)

    def next_due(self, server, targets):
        now = self.clock()
        due = []
        for t in targets:
            checked = float(self.store.meta(self.checked_key(server, t), '0'))
            snap = self.fetch(server, t, network=False)
            fresh = all(at is not None and 0 <= now-at < SD.PAGE_TTL for at in (snap['fetched_at'], snap['history_at']))
            if now-checked >= SD.ITEM_INTERVAL and not fresh:
                due.append((checked, t))
        return min(due, key=lambda pair: pair[0])[1] if due else None

    def scan(self, server, targets, cfg, own=(), stop=None):
        due = self.next_due(server, targets)
        if due is not None and not (stop and stop.is_set()):
            # 실패 때도 같은 대상만 연속 요청하지 않고 다른 대상으로 순환한다.
            self.store.set_meta(self.checked_key(server, due), self.clock())
            self.fetch(server, due, stop)
        snapshots = {t['query']: self.snapshot(server, t) for t in targets}
        result = evaluate(snapshots, targets, cfg, own, now=self.clock())
        # 카드·인챈트를 정한 대상: 가격 조건은 맞는데 상세가 없는 후보만 몇 건 받아 다시 판단한다
        need = [r for r in result['rows'] if r.get('cards_pending')][:MAX_CARD_DETAILS]
        for r in need:
            if stop and stop.is_set():
                break
            try:
                det = self.client.deal_view(server, r['listing_name'], r.get('map_code', ''), r['ssi'])
            except G.RateLimited:
                raise
            except Exception:
                continue
            self.store.save_detail(server, r['ssi'], det, now=self.clock())
        if need:
            snapshots = {t['query']: self.snapshot(server, t) for t in targets}
            result = evaluate(snapshots, targets, cfg, own, now=self.clock())
        result.update(server=server, checked=due and due['query'], checked_at=self.clock())
        return result

    def snapshot(self, server, target):
        """저장된 검색·이력 + (카드·인챈트를 정한 대상이면) 저장된 매물 상세. 요청 없음."""
        snap = self.fetch(server, target, network=False)
        if target.get('cards'):
            snap['details'] = {l['ssi']: self.store.detail(server, l['ssi'])[1] for l in snap['listings'] if l.get('ssi')}
        return snap

    def seen(self, row):
        with self.store.lock:
            return self.store.conn.execute('SELECT 1 FROM resale_alert_seen WHERE fingerprint=?', (fingerprint(row),)).fetchone() is not None

    def mark_seen(self, row):
        with self.store.lock:
            self.store.conn.execute('INSERT OR IGNORE INTO resale_alert_seen VALUES (?,?,?)',
                                    (fingerprint(row), self.clock(), json.dumps(row, ensure_ascii=False)))
            self.store.conn.commit()


def evaluate(snapshots, targets, cfg, own=(), now=None, today=None):
    """기준가 1억 이상 + 기준가의 80% 이하. 예상 차익은 1개당, 판매 완료를 보장하지 않는다."""
    cfg = config(cfg)
    now = time.time() if now is None else now
    out, unchecked, stale, insufficient = [], 0, 0, 0
    for t in targets:
        snap = snapshots.get(t['query']) or {}
        if snap.get('fetched_at') is None:
            unchecked += 1
            continue
        fresh = all(at is not None and 0 <= now-at < SD.PAGE_TTL for at in (snap.get('fetched_at'), snap.get('history_at')))
        if not fresh:
            stale += 1
        base, days = base_price(snap.get('history', []), today)
        if days < SD.MIN_DAYS or base is None:
            insufficient += 1
            continue
        if base < cfg['min_base']:
            continue
        mine = [o for o in own if o.get('server', cfg['server']) == cfg['server'] and
                (o.get('item_id') == t['item_id'] or norm(o.get('name')) in (norm(t['name']), norm(t['query'])))]
        sales = [l for l in snap.get('listings', []) if exact_listing(l, t) and l.get('kind') == 'sale'
                 and isinstance(l.get('price'), int) and l['price'] > 0 and (l.get('qty') or 0) > 0
                 and not any(o.get('shop') and is_own_listing(l, o.get('price'), o['shop']) for o in mine)]
        unique = set()
        for listing in sales:
            price = listing['price']
            if price * 100 > base * cfg['max_ratio'] + 1e-6:
                continue
            key = (listing.get('shop'), price, listing.get('qty'), listing.get('ssi'))
            if key in unique:
                continue
            unique.add(key)
            peers = {}
            for peer in sales:
                shop = (peer.get('shop') or '').strip()
                if shop and shop != (listing.get('shop') or '').strip():
                    peers[shop] = min(peers.get(shop, peer['price']), peer['price'])
            peer_min = min(peers.values()) if peers else None
            resale = int(min(base, peer_min) * (100-cfg['haircut']) / 100) if peer_min else None
            net = real_amount(resale)
            profit = net-price if net is not None else None
            partial = snap.get('last', 1) > snap.get('pages', 0)
            maybe_own = any(not o.get('shop') and is_own_listing(listing, o.get('price'), '') for o in mine)
            reasons = []
            cards_pending = False
            if t.get('cards'):
                det = (snap.get('details') or {}).get(listing.get('ssi'))
                if det is None:
                    cards_pending = bool(listing.get('ssi'))
                    reasons.append('카드·인챈트 확인 전')
                elif list(det.get('cards') or []) != t['cards']:
                    reasons.append('카드·인챈트가 정한 구성과 다름')
            if not fresh: reasons.append('시세 30분 경과 또는 시각 확인 필요')
            if partial: reasons.append('조회한 매물 기준·미조회 페이지 있음')
            if snap.get('ambiguous'): reasons.append('검색 결과에 다른 품목·제련 포함: 기준가 확인 필요')
            if not listing.get('shop'): reasons.append('상점명 확인 필요')
            if len(peers) < MIN_PEER_SHOPS: reasons.append('다른 상점 2곳 미만')
            if maybe_own: reasons.append('내 노점 여부 확인 필요')
            if profit is None or profit < cfg['min_profit']: reasons.append('세후 예상 차익 기준 미달')
            out.append(dict(server=cfg['server'], item_id=t['item_id'], name=t['query'], price=price,
                            qty=listing['qty'], base=base, days=days, ratio=price/base*100,
                            peer_min=peer_min, peer_shops=len(peers), resale=resale, net=net, profit=profit,
                            shop=listing.get('shop') or '', location=listing.get('location') or '',
                            ssi=listing.get('ssi') or '', fetched_at=snap['fetched_at'],
                            listing_name=listing.get('name') or '', map_code=listing.get('map_code') or '',
                            cards_pending=cards_pending and len(reasons) == 1,
                            state='확인 필요' if reasons else '구매 검토 추천', eligible=not reasons,
                            reason=' · '.join(reasons) if reasons else '80% 이하·다른 상점 2곳 이상·세후 차익 충족',
                            caution='카드·옵션 가치 미반영 / 예상 수익이며 판매 보장 없음'))
    out.sort(key=lambda r: (not r['eligible'], -(r['profit'] if r['profit'] is not None else -10**15), r['price']))
    # 목록에서 실제로 본 최저가인지 구분. 더 비싼 후보에도 무조건 최저가라고 쓰지 않는다.
    for r in out:
        snap = snapshots[r['name']]
        prices = [l['price'] for l in snap.get('listings', []) if exact_listing(l, next(t for t in targets if t['query']==r['name']))
                  and l.get('kind') == 'sale' and l.get('price') and (l.get('qty') or 0)>0]
        r['lowest'] = bool(prices and r['price']==min(prices))
    return dict(rows=out, unchecked=unchecked, stale=stale, insufficient=insufficient, targets=len(targets))


def fingerprint(row):
    # 같은 가게·가격·수량은 재검색/프로그램 재시작 후에도 반복 알림하지 않는다.
    key = [row['server'], row['item_id'], variant(row['name']), row.get('shop') or '', row['price'], row['qty']]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode('utf-8')).hexdigest()


def alert_text(row):
    return ('%s\n구매 %s Z / 기준 %s Z (%.1f%%)\n'
            '재판매 예상 %s Z → 세후 차익 +%s Z/개\n상점: %s%s\n확인 %s · 옵션 가치 미반영·수익 보장 아님') % (
                row['name'], format(row['price'], ','), format(int(row['base']), ','), row['ratio'],
                format(row['resale'], ','), format(row['profit'], ','), row['shop'],
                (' · '+row['location']) if row.get('location') else '',
                datetime.datetime.fromtimestamp(row['fetched_at']).strftime('%m-%d %H:%M'))
