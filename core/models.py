"""프로그램 내부 공통 데이터 구조."""
from dataclasses import dataclass, field
from typing import Optional

# 패킷의 아이템 타입 바이트 → 분류
ITEM_TYPES = {0: '회복', 2: '소비', 3: '기타', 4: '방어구', 5: '무기', 6: '카드', 7: '펫 알', 8: '펫 장비',
              10: '화살/탄환', 11: '소비(지연)', 12: '쉐도우', 18: '캐시'}


@dataclass
class ItemEntry:
    source: str                     # 창고1..N | 인벤토리 | 카트
    item_id: int
    name: str
    quantity: int
    refine: Optional[int] = None    # 장비가 아니면 None
    cards: list = field(default_factory=list)      # 카드/인챈트 item_id
    options: list = field(default_factory=list)    # [{'id','value','param'}]
    identified: Optional[bool] = None
    raw_data: dict = field(default_factory=dict)
    category: Optional[str] = None
    is_equip: bool = False

    @property
    def location_group(self):
        return '창고' if self.source.startswith('창고') else self.source

    def variant_key(self):
        """중복 구분 키: item_id + refine + cards + options."""
        opts = tuple(sorted((o['id'], o['value'], o.get('param', 0)) for o in self.options))
        return (self.item_id, self.refine or 0, tuple(self.cards), opts)


@dataclass(frozen=True)
class PriceKey:
    """장비 시세 확장용 키. 1차 버전은 item_id만 사용(variant='')."""
    item_id: int
    refine: int = 0
    cards: tuple = ()
    options: tuple = ()

    @property
    def variant(self):
        if not (self.refine or self.cards or self.options):
            return ''
        return 'r%d|c%s|o%s' % (self.refine, ','.join(map(str, self.cards)),
                                ','.join('%s:%s' % o[:2] for o in self.options))


@dataclass
class Listing:
    price: int
    quantity: int = 1
    kind: str = 'sale'              # sale | buy
    shop: str = ''
    seen_at: Optional[float] = None


@dataclass
class PriceInfo:
    item_id: int
    lowest_price: Optional[int]
    median_price: Optional[int]
    average_price: Optional[int]
    listing_count: int
    updated_at: float               # 시세 수집(또는 계산) 시각 epoch
    provider: str = ''
    highest_buy: Optional[int] = None

    @property
    def has_price(self):
        return self.lowest_price is not None
