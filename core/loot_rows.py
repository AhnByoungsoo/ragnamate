"""획득 아이템 한 줄: 노점에 팔 때와 상점(NPC)에 팔 때를 나란히 놓고 더 나은 쪽을 고른다.

- 노점가: 공식 노점 시세(지금 판매 최저가 → 최근 7일 평균). 모르면 None.
- 상점가: 상점 판매 기준가 × (100 + 오버차지 %) / 100 (내림). 기준가는 rAthena 자료라 국내 서버와 다를 수 있다(△).
- 값을 모르면 None 그대로 둔다. 0으로 채우지 않는다.
"""
import json
import os

VEND, NPC = '노점', '상점'
OVERCHARGE_MAX = 24                  # 오버차지 Lv10. 스킬이 없으면 0
OVERCHARGE_SKILL = 38                # 오버챠지(MC_OVERCHARGE)


def overcharge_rate(level):
    """오버챠지 레벨 → 상점 판매가에 붙는 %. Lv1 7% … Lv9 23%, Lv10 24% (rAthena pc_modifysellvalue)."""
    level = max(0, min(10, int(level or 0)))
    return 0 if not level else 5 + 2 * level - (1 if level == 10 else 0)


def overcharge_level(events):
    """리플레이의 스킬 목록에서 오버챠지 레벨. 스킬 목록이 없으면 None, 스킬이 없으면 0."""
    level = None
    for e in events:
        if e.event_type == 'SKILL_LIST':
            level = (e.data['skills'].get(OVERCHARGE_SKILL) or {}).get('level', 0)
    return level


class NpcPrices:
    """상점 판매 기준가(data/rathena/item_prices.json — tools/build_item_prices.py 가 만든다)."""

    def __init__(self, path):
        self.path, self._sell, self.built_at = path, None, ''

    def _load(self):
        if self._sell is None:
            try:
                with open(self.path, encoding='utf-8') as f:
                    data = json.load(f)
                self._sell = {int(k): int(v) for k, v in data.get('sell', {}).items()}
                self.built_at = data.get('built_at', '')
            except (OSError, ValueError):
                self._sell = {}
        return self._sell

    def base(self, item_id):
        """오버차지 없이 상점에 팔 때 받는 값. 자료에 없거나 0이면 None."""
        return self._load().get(item_id) or None

    def sell(self, item_id, rate=OVERCHARGE_MAX):
        """오버차지 rate % 를 적용한 상점 판매가(내림)."""
        base = self.base(item_id)
        return None if base is None else base * (100 + max(0, int(rate))) // 100


def default_npc_prices(base_dir):
    return NpcPrices(os.path.join(base_dir, 'data', 'rathena', 'item_prices.json'))


def row(item_id, name, quantity, vend=None, vend_basis='', npc=None, **extra):
    """한 줄. unit/value 는 더 비싸게 팔리는 쪽 기준, sell_at 은 그쪽 이름('' = 둘 다 모름)."""
    best = max((p for p in (vend, npc) if p is not None), default=None)
    sell_at = '' if best is None else (VEND if vend is not None and vend == best else NPC)
    return dict(item_id=item_id, name=name, quantity=quantity, vend=vend, vend_basis=vend_basis, npc=npc,
                sell_at=sell_at, unit=best, value=None if best is None else best * quantity,
                vend_value=None if vend is None else vend * quantity,
                npc_value=None if npc is None else npc * quantity, **extra)


def build(items, name_of, vend_of=None, npc_of=None, extra=None):
    """items{아이템 번호: 수량} → 줄 목록(가치 큰 순). vend_of(id) → (단가, 근거) · npc_of(id) → 단가."""
    rows = []
    for item_id, quantity in items.items():
        vend, basis = vend_of(item_id) if vend_of else (None, '')
        rows.append(row(item_id, name_of(item_id), quantity, vend, basis, npc_of(item_id) if npc_of else None,
                        **((extra or {}).get(item_id) or {})))
    rows.sort(key=lambda r: (-(r['value'] or 0), str(r['name'])))
    return rows


def totals(rows):
    """합계. vend/npc/best 는 그 값을 아는 줄만 더한 것이고, 하나도 모르면 None 이다."""
    def total(key):
        known = [r[key] for r in rows if r[key] is not None]
        return sum(known) if known else None
    return dict(count=len(rows), quantity=sum(r['quantity'] for r in rows),
                vend=total('vend_value'), npc=total('npc_value'), best=total('value'),
                unpriced=sum(1 for r in rows if r['value'] is None),
                no_vend=sum(1 for r in rows if r['vend'] is None))
