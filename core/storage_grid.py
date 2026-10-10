"""창고 슬롯별 가격 표시 모델. 화면 슬롯과 서버 인덱스를 동일시하지 않는다."""
import hashlib
import json

def inventory(rows, source):
    entries = [r for r in rows if r.entry.source == source and r.entry.location_group == '창고']
    entries.sort(key=lambda r: (r.entry.raw_data.get('index') or 0, r.entry.item_id))
    return entries

def fingerprint(rows, source, context=''):
    data = [[r.entry.item_id, r.entry.quantity, r.entry.variant_key(), r.entry.raw_data.get('index')]
            for r in inventory(rows, source)]
    return hashlib.sha256(json.dumps([source, context, data], ensure_ascii=False).encode()).hexdigest()

def compact_price(price):
    if price is None: return '시세 없음'
    if price >= 100000000: return ('%.2f' % (price/100000000)).rstrip('0').rstrip('.')+'억'
    if price >= 10000: return ('%.1f' % (price/10000)).rstrip('0').rstrip('.')+'만'
    return format(price, ',')

def labels(rows, config, context=''):
    if not config.get('confirmed') or config.get('fingerprint') != fingerprint(rows, config.get('source'), context):
        return None
    entries = inventory(rows, config['source'])
    out = []
    for index in config.get('slots', []):
        if index is None:
            out.append(None)
            continue
        if type(index) is not int or not 0 <= index < len(entries): return None
        r = entries[index]
        # 슬롯 가격은 항상 판매 최저가로 고정. 장비 옵션별 시세는 현재 DB에서 구별하지 못한다.
        price = r.price.lowest_price if r.price and r.price.has_price else None
        variant = bool(r.entry.is_equip)
        out.append(dict(text=('~' if variant and price is not None else '')+compact_price(price),
                        name=r.entry.name, price=price, updated_at=r.price.updated_at if r.price else None,
                        approximate=variant))
    return out

def physical_rect(config, client):
    left, top, width, height = client
    x, y = config['x'], config['y']
    w, h = config['columns']*config['cell_w'], config['rows']*config['cell_h']
    if x < 0 or y < 24 or x+w > width or y+h > height:
        raise ValueError('슬롯 표시 영역이 게임 창 밖입니다. 위치·행·열·칸 크기를 조절하세요.')
    return left+x, top+y-24, w, h+24
