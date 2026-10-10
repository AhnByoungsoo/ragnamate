"""LootAnalyzer / ConsumableAnalyzer / ProfitAnalyzer.

우선순위: ITEM_GAIN/ITEM_REMOVE 이벤트 → 없으면 구간 앞뒤 인벤토리 목록 비교.
시세는 기존 PriceProvider(MarketService)를 그대로 사용한다.
"""
import collections

from analyzer.analyzers.context import per_hour
from analyzer.events import CALC, EARNED, ESTIMATE, MEASURED, EventType as E
from core.analysis import unit_price

SNAPSHOT_TOLERANCE_MS = 20_000   # 맵 도착 후 인벤토리 목록이 오는 지연 허용


def _inv_totals(items):
    c = collections.Counter()
    for it in items:
        c[it['id']] += it['amount']
    return c


def inventory_snapshots(ctx):
    """(구간 시작 인벤토리, 구간 끝 인벤토리) 또는 (None, None)."""
    lists = [(tm, items) for typ, items, tm in ctx.pr.inventory_lists if typ == 0]
    t0 = ctx.pr.packets[0][0] if ctx.pr.packets else 0
    lists = [(tm - t0, items) for tm, items in lists]
    start = next((l for l in lists if ctx.start_ms <= l[0] <= ctx.start_ms + SNAPSHOT_TOLERANCE_MS), None) \
        or next((l for l in reversed(lists) if l[0] < ctx.start_ms), None)
    end = next((l for l in lists if ctx.end_ms <= l[0] <= ctx.end_ms + SNAPSHOT_TOLERANCE_MS), None) \
        or next((l for l in reversed(lists) if start and start[0] < l[0] <= ctx.end_ms), None)
    if not start or not end or start[0] == end[0]:
        return None, None
    return start, end


def analyze(ctx, price_lookup, basis='lowest'):
    """price_lookup(dict item_id→name) → {item_id: PriceInfo}"""
    items = ctx.gamedata.items
    gains, losses, method = collections.Counter(), collections.Counter(), None
    packets = False                                # 획득 패킷이 하나라도 있으면 인벤토리 비교보다 그쪽이 정확하다
    for e in ctx.of(E.ITEM_GAIN):                  # 창고·카트에서 꺼낸 것, 산 것은 획득이 아니다
        packets = True
        if e.data.get('source', EARNED[0]) in EARNED:
            gains[e.data['item_id']] += e.data.get('amount', 1)
    s, t = inventory_snapshots(ctx)
    diff = collections.Counter()
    if s:
        diff = _inv_totals(t[1])
        diff.subtract(_inv_totals(s[1]))
        losses = collections.Counter({k: -v for k, v in diff.items() if v < 0})
    if packets:
        method = ('획득 패킷(주운 것·상자·퀘스트 보상)', MEASURED)
    elif s:
        gains = collections.Counter({k: v for k, v in diff.items() if v > 0})
        method = ('인벤토리 비교 %s → %s' % (_fmt(s[0]), _fmt(t[0])), CALC)
    if method is None:
        return dict(available=False, note='구간 앞뒤 인벤토리 목록이 없어 획득/소모를 계산할 수 없음 '
                                          '(맵 이동 시 인벤토리가 기록됨)', loot=[], consumables=[],
                    loot_value=None, consumable_cost=None, rare=[])

    ids = {i: items.name(i) for i in list(gains) + list(losses)}
    prices = price_lookup(ids) if ids else {}
    rare_min = int(ctx.settings.get('rare_item_value', 1_000_000))

    def rows(counter):
        out = []
        for iid, qty in counter.items():
            p = prices.get(iid)
            u = unit_price(p, basis)
            out.append(dict(item_id=iid, name=ids[iid], quantity=qty, unit_price=u,
                            value=u * qty if u is not None else None, lowest=p.lowest_price if p else None,
                            median=p.median_price if p else None, listings=p.listing_count if p else 0))
        out.sort(key=lambda r: -(r['value'] or 0))
        return out

    loot, cons = rows(gains), rows(losses)
    return dict(available=True, note='', method=method[0], conf=method[1],
                consumable_conf=ESTIMATE, consumable_note='인벤토리 감소 기준(판매/보관/버리기 포함 가능)',
                loot=loot, consumables=cons,
                loot_value=sum(r['value'] or 0 for r in loot), consumable_cost=sum(r['value'] or 0 for r in cons),
                unpriced_loot=sum(1 for r in loot if r['unit_price'] is None),
                rare=[r for r in loot if (r['unit_price'] or 0) >= rare_min])


def profit(ctx, loot, zeny):
    lv = loot.get('loot_value') if loot.get('available') else None
    cc = loot.get('consumable_cost') if loot.get('available') else None
    zn = zeny.get('net') if zeny.get('available') else None
    parts = [v for v in (lv, zn) if v is not None]
    if not parts:
        return dict(available=False, note='드랍/Zeny 정보가 없어 계산 불가')
    net = sum(parts) - (cc or 0)
    return dict(available=True, loot_value=lv, zeny_net=zn, consumable_cost=cc, profit=net,
                per_hour=per_hour(net, ctx.duration_ms), per_min=per_hour(net, ctx.duration_ms) / 60
                if ctx.duration_ms else None,
                note='드랍가치는 현재 시세 기반 계산(○), 실제 판매금액은 다를 수 있음(△)')


def _fmt(ms):
    s = ms // 1000
    return '%02d:%02d' % (s // 60, s % 60)
