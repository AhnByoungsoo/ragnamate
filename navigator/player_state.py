"""PlayerState: Replay에서 확인 가능한 값만 자동으로 채운다. 확인 불가 값은 None."""
import collections
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Holding:
    item_id: int
    total: int = 0
    by_source: dict = field(default_factory=dict)   # {'인벤토리': 3, '창고1': 7}


@dataclass
class PlayerState:
    character_name: Optional[str] = None
    job_id: Optional[int] = None
    job_name: Optional[str] = None
    level: Optional[int] = None
    job_level: Optional[int] = None          # Replay 메타데이터(Session) 값 — 추정(△)
    map: Optional[str] = None
    zeny: Optional[int] = None
    inventory_known: bool = False
    storage_known: bool = False
    cart_known: bool = False
    holdings: dict = field(default_factory=dict)     # item_id → Holding
    replay_file: Optional[str] = None
    replay_time: Optional[str] = None                 # 녹화 시각(창고/인벤토리 스냅샷 기준 시각)
    quest_states: dict = field(default_factory=dict)  # guide_id → COMPLETED/NOT_COMPLETED/UNKNOWN

    @property
    def known(self):
        return self.character_name is not None

    def holding(self, item_id):
        return self.holdings.get(item_id) or Holding(item_id)

    def sources_known(self):
        return [n for n, k in (('인벤토리', self.inventory_known), ('카트', self.cart_known),
                               ('창고', self.storage_known)) if k]


def from_analysis(res, job_name_fn=None):
    """AnalysisResult(Replay 분석 결과) → PlayerState."""
    pr, storage = res.parsed, res.storage
    zeny = None
    for e in pr.events:
        if e.event_type == 'ZENY_CHANGE':
            zeny = e.data['value']
    if zeny is None:
        zeny = pr.start_zeny
    last_map = next((e.map_name for e in reversed(pr.events) if e.map_name), None) or pr.last_map
    st = PlayerState(character_name=pr.character or None, job_id=pr.job_id, level=pr.base_level,
                     job_level=pr.job_level, map=last_map, zeny=zeny, replay_file=pr.path,
                     replay_time=pr.recorded_at.isoformat(sep=' ', timespec='minutes'))
    st.job_name = job_name_fn(pr.job_id) if job_name_fn and pr.job_id is not None else None
    add_entries(st, storage.entries)
    return st


def add_entries(st, entries):
    """창고 분석의 ItemEntry 목록으로 보유량 합산 (인벤토리 + 카트 + 창고1..N)."""
    agg = collections.defaultdict(lambda: collections.Counter())
    for e in entries:
        agg[e.item_id][e.source] += e.quantity
        if e.source == '인벤토리':
            st.inventory_known = True
        elif e.source == '카트':
            st.cart_known = True
        elif e.source.startswith('창고'):
            st.storage_known = True
    st.holdings = {iid: Holding(iid, sum(c.values()), dict(c)) for iid, c in agg.items()}
    return st
