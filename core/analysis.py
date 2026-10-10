"""ItemEntry + 시세 → 분석 행(Row), 요약, 필터."""
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from core.models import ItemEntry, PriceInfo

EQUIP_NOTE = '장비 가격은 제련/카드/옵션으로 실제 거래가격과 차이가 있을 수 있음'


@dataclass
class Row:
    entry: ItemEntry
    price: Optional[PriceInfo]
    unit_price: Optional[int]      # 선택된 기준(최저/중앙/평균)의 단가
    value: Optional[int]           # 예상 판매금액

    @property
    def has_price(self):
        return self.price is not None and self.price.has_price


def merge_entries(entries):
    """같은 위치 + 같은 variant_key(item_id+refine+cards+options) 아이템을 합산.
    장비는 개별 아이템 정보를 보존하기 위해 합산하지 않는다."""
    merged = OrderedDict()
    out = []
    for e in entries:
        if e.is_equip:
            out.append(e)
            continue
        key = (e.source,) + e.variant_key()
        if key in merged:
            m = merged[key]
            m.quantity += e.quantity
            m.raw_data.setdefault('merged_indexes', [m.raw_data.get('index')]).append(e.raw_data.get('index'))
        else:
            merged[key] = e
            out.append(e)
    return out


def unit_price(price, basis):
    if not price or not price.has_price:
        return None
    val = {'lowest': price.lowest_price, 'median': price.median_price, 'average': price.average_price}.get(basis)
    return val if val is not None else price.lowest_price


def build_rows(entries, prices, basis):
    rows = []
    for e in entries:
        p = prices.get(e.item_id)
        u = unit_price(p, basis)
        rows.append(Row(e, p, u, u * e.quantity if u is not None else None))
    return rows


def is_sellable(row, min_value):
    return row.has_price and (row.value or 0) >= min_value


def summarize(rows):
    kinds = {r.entry.item_id for r in rows}
    assets = OrderedDict((k, 0) for k in ('창고', '인벤토리', '카트'))
    for r in rows:
        assets[r.entry.location_group] = assets.get(r.entry.location_group, 0) + (r.value or 0)
    return dict(
        kinds=len(kinds),
        quantity=sum(r.entry.quantity for r in rows),
        priced_kinds=len({r.entry.item_id for r in rows if r.has_price}),
        total_value=sum(r.value or 0 for r in rows),
        assets=assets,
    )


def overlay_rows(rows, query='', only_sellable=False, min_value=0, limit=60):
    """창고 시세 Overlay용 행: 같은 아이템을 위치에 상관없이 합치고 예상금액 순으로.

    '팔 만한 것만'은 위치를 합친 뒤의 예상금액으로 판단한다(창고·카트에 나눠 둔 것도 같이 본다).
    시세가 없으면 0으로 채우지 않고 unit/value를 None으로 둔다."""
    q = (query or '').strip().lower()
    agg = {}
    for r in rows:
        if q and q not in r.entry.name.lower() and q != str(r.entry.item_id):
            continue
        a = agg.get(r.entry.item_id)
        if a is None:
            a = agg[r.entry.item_id] = dict(item_id=r.entry.item_id, name=r.entry.name, quantity=0, value=None,
                                            unit=r.unit_price, average=None, locations=set(), updated_at=None)
        a['quantity'] += r.entry.quantity
        if r.value is not None:
            a['value'] = (a['value'] or 0) + r.value
        if r.has_price:
            a['unit'] = r.unit_price if a['unit'] is None else min(a['unit'], r.unit_price)
            a['average'] = r.price.average_price
            a['updated_at'] = r.price.updated_at
        a['locations'].add(r.entry.location_group)
    keep = [a for a in agg.values() if not only_sellable or (a['value'] or 0) >= min_value]
    out = sorted(keep, key=lambda a: -(a['value'] or 0))[:limit]
    for a in out:
        a['locations'] = '·'.join(sorted(a['locations']))
    return out


def top_items(rows, n=20):
    """예상 자산 TOP N (같은 item_id는 위치를 합쳐서)."""
    agg = {}
    for r in rows:
        if not r.value:
            continue
        a = agg.setdefault(r.entry.item_id, dict(name=r.entry.name, value=0, quantity=0, sources=set()))
        a['value'] += r.value
        a['quantity'] += r.entry.quantity
        a['sources'].add(r.entry.source)
    return sorted(agg.values(), key=lambda a: -a['value'])[:n]
