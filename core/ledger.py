"""사냥 결산: 리플레이 한 개의 획득 아이템·제니 수입/지출을 모으고, 여러 개를 하루치로 합친다.

- 획득 아이템: 획득 패킷으로 센 것(●). 창고·카트에서 꺼낸 것과 산 것은 뺀다.
- 제니 수입·지출: 제니 변화 패킷의 차이(●). 상점 판매/노점 판매로 나누는 것은 표본이 없어 미검증(△)이다.
- 아이템 가치는 시세가 있을 때만 계산한다(○). 시세를 모르는 아이템은 합계에 넣지 않고 따로 센다.
"""
import collections

from analyzer.events import EARNED, NPC_SALE, OTHER_INCOME, SPEND, VEND_SALE, EventType as E
from core import loot_rows as LR

INCOME_LABEL = {NPC_SALE: '상점 판매', VEND_SALE: '노점 판매', OTHER_INCOME: '기타 제니 수입'}
INCOME_KINDS = (NPC_SALE, VEND_SALE, OTHER_INCOME)


def empty():
    return dict(items={}, income={}, spent=0)


def from_events(events, player=None):
    """ReplayEvent 목록 → dict(items{아이템 번호: 수량}, income{종류: 제니}, spent)."""
    items, income, spent = collections.Counter(), collections.Counter(), 0
    for e in events:
        if e.event_type == E.ITEM_GAIN and e.data.get('source', EARNED[0]) in EARNED:
            items[e.data['item_id']] += e.data.get('amount', 1)
        elif e.event_type == E.ZENY_FLOW and (player is None or e.actor_id == player):
            if e.data['kind'] == SPEND:
                spent -= e.data['amount']
            else:
                income[e.data['kind']] += e.data['amount']
    return dict(items=dict(items), income=dict(income), spent=spent)


def merge(entries):
    """여러 리플레이의 결산을 하나로."""
    items, income, spent = collections.Counter(), collections.Counter(), 0
    for entry in entries:
        for item_id, qty in (entry.get('items') or {}).items():
            items[int(item_id)] += qty
        for kind, amount in (entry.get('income') or {}).items():
            income[kind] += amount
        spent += entry.get('spent') or 0
    return dict(items=dict(items), income=dict(income), spent=spent)


def settle(entry, vend_of, name_of, jackpot_min=None, npc_of=None):
    """결산표. vend_of(item_id) → (노점 단가 또는 None, 근거) · npc_of(item_id) → 상점 단가 또는 None.

    줄마다 노점가·상점가 중 더 비싼 쪽으로 가치를 매긴다(core.loot_rows). 값을 모르는 아이템은 합계에서 뺀다.
    loot_value: 값을 아는 아이템만의 합(○) — 하나도 모르면 None. unpriced: 값을 몰라 뺀 아이템 종류 수.
    jackpot 은 노점 시세가 기준 이상인 줄이다.
    """
    rows = LR.build({int(k): q for k, q in (entry.get('items') or {}).items()}, name_of, vend_of, npc_of)
    for r in rows:
        r['jackpot'] = bool(jackpot_min and r['vend'] is not None and r['vend'] >= jackpot_min)
    totals = LR.totals(rows)
    income = {kind: (entry.get('income') or {}).get(kind, 0) for kind in INCOME_KINDS}
    zeny_in, spent = sum(income.values()), entry.get('spent') or 0
    return dict(rows=rows, loot_value=totals['best'], vend_value=totals['vend'], npc_value=totals['npc'],
                unpriced=totals['unpriced'],
                income=income, zeny_in=zeny_in, spent=spent, zeny_net=zeny_in - spent,
                sales=income[NPC_SALE] + income[VEND_SALE],
                # 획득 가치와 판매 제니는 더하지 않는다 — 주운 것을 팔면 양쪽에 다 잡힌다
                jackpots=[r for r in rows if r['jackpot']])
