"""게임 안 노점 검색 결과를 시세 자료로: 리플레이에 찍힌 '노점 검색' 응답을 data/vending.db 에 넣는다.

게임에서 노점 검색을 하면 서버가 매물(아이템·가격·수량·상점명·제련·카드)을 10건씩 보내 준다. 리플레이를 켠 채
검색하면 그 응답이 그대로 남으므로, 공식 홈페이지에 묻지 않고도 그 시각의 실제 시세를 얻는다.

- 가격 낮은 순으로 온 **첫 쪽이 들어 있는 검색**만 넣는다(그래야 '지금 최저가'가 맞다).
- 이미 더 새로운 자료가 저장돼 있으면 덮어쓰지 않는다(옛 리플레이를 다시 분석해도 시세가 뒤로 가지 않는다).
- 열어 본 노점 한 곳의 물건(STORE_ITEMS)은 넣지 않는다 — 한 가게의 값은 시장 최저가가 아니다.
"""
import collections

from analyzer.events import EventType as E

SAME_SEARCH_MS = 120_000          # 이 안에 이어진 쪽은 같은 검색으로 본다


def listing_name(name, refine):
    return ('+%d' % refine if refine else '') + str(name)


def searches(events):
    """STORE_SEARCH 이벤트 → [dict(item_id, at_ms, rows)] — 아이템별로 '첫 쪽부터 이어 받은' 검색 한 번씩."""
    open_, out = {}, []
    for e in events:
        if e.event_type != E.STORE_SEARCH:
            continue
        by_item = collections.defaultdict(list)
        for r in e.data['rows']:
            by_item[r['item_id']].append(r)
        for item_id, rows in by_item.items():
            cur = open_.get(item_id)
            if e.data.get('first'):                      # 새 검색의 첫 쪽
                cur = open_[item_id] = dict(item_id=item_id, at_ms=e.timestamp_ms, rows=[])
                out.append(cur)
            elif cur is None or e.timestamp_ms - cur['at_ms'] > SAME_SEARCH_MS:
                continue                                 # 첫 쪽 없이 뒤쪽만 보임 → 최저가를 알 수 없다
            cur['rows'] += rows
            cur['at_ms'] = e.timestamp_ms
    # 가격 낮은 순으로 온 것만 믿는다
    return [s for s in out if all(a['price'] <= b['price'] for a, b in zip(s['rows'], s['rows'][1:]))]


def ingest(store, server, events, recorded_at, name_of):
    """검색 결과를 store(VendingStore)에 저장. 반환: 저장한 아이템 번호 목록."""
    saved = []
    latest = {}
    for s in searches(events):                          # 같은 아이템을 여러 번 검색했으면 마지막 것
        latest[s['item_id']] = s
    for item_id, s in latest.items():
        at = recorded_at.timestamp() + s['at_ms'] / 1000
        if (store.latest_listings(server, item_id)[0] or 0) >= at:
            continue
        name = name_of(item_id)
        store.save_listings(server, item_id, [
            dict(price=r['price'], qty=r['amount'], shop=r['shop'], kind='sale', name=listing_name(name, r['refine']),
                 location='게임 안 노점 검색') for r in s['rows']], now=at)
        saved.append(item_id)
    return saved
