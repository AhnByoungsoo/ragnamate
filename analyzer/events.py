"""Replay Event 표준 구조.

Packet 이름(ZC_...)과 Event 이름은 분리한다. 분석기는 Event만 본다.
"""
from dataclasses import dataclass, field
from typing import Optional

# 신뢰도 표시
MEASURED = '●'   # 패킷에서 직접 측정
CALC = '○'       # 측정값으로 계산
ESTIMATE = '△'   # 추정(가정 포함)


# ITEM_GAIN 의 data['source'] — 인벤토리에 들어온 까닭
LOOT, FROM_STORAGE, FROM_CART, PURCHASE, OTHER_GAIN = 'LOOT', 'STORAGE', 'CART', 'PURCHASE', 'OTHER'
EARNED = (LOOT, OTHER_GAIN)          # 결산에서 '획득'으로 세는 것(주운 것 + 상자·퀘스트 등). 창고·카트·구입은 뺀다
# ZENY_FLOW 의 data['kind'] — 금액은 실측(●), 상점/노점 구분은 표본이 없어 미검증(△)
NPC_SALE, VEND_SALE, OTHER_INCOME, SPEND = 'NPC_SALE', 'VEND_SALE', 'OTHER', 'SPEND'


class EventType:
    SESSION_START = 'SESSION_START'
    SESSION_END = 'SESSION_END'
    MAP_CHANGE = 'MAP_CHANGE'
    PLAYER_MOVE = 'PLAYER_MOVE'
    PLAYER_SPAWN = 'PLAYER_SPAWN'
    MONSTER_SPAWN = 'MONSTER_SPAWN'
    MONSTER_MOVE = 'MONSTER_MOVE'
    MONSTER_DEATH = 'MONSTER_DEATH'
    NPC_SPAWN = 'NPC_SPAWN'
    UNIT_SPAWN = 'UNIT_SPAWN'          # 펫/정령 등 기타 유닛
    UNIT_VANISH = 'UNIT_VANISH'        # 시야 이탈/로그아웃/순간이동
    DAMAGE = 'DAMAGE'
    ACTION = 'ACTION'                  # 앉기/서기/줍기 동작
    SKILL_CAST_START = 'SKILL_CAST_START'
    SKILL_USE = 'SKILL_USE'
    BUFF_START = 'BUFF_START'
    BUFF_END = 'BUFF_END'
    HP_CHANGE = 'HP_CHANGE'
    SP_CHANGE = 'SP_CHANGE'
    ZENY_CHANGE = 'ZENY_CHANGE'
    WEIGHT_CHANGE = 'WEIGHT_CHANGE'
    BASE_EXP_GAIN = 'BASE_EXP_GAIN'    # 미검증 후보 패킷에서만 생성
    JOB_EXP_GAIN = 'JOB_EXP_GAIN'
    ITEM_GAIN = 'ITEM_GAIN'
    ZENY_FLOW = 'ZENY_FLOW'            # 제니 증감 1건: amount(±), kind
    # ---- 2026-10-10 해외 공개 패킷 정의로 추가 ----
    SKILL_DELAY = 'SKILL_DELAY'        # 서버가 알려 준 '이 스킬을 다시 쓸 수 있을 때까지': skill, delay_ms
    SKILL_FAIL = 'SKILL_FAIL'          # 스킬 실패: skill, cause(사유 번호)
    MONSTER_HP = 'MONSTER_HP'          # 몬스터 HP 막대: hp_pct(5% 단위)
    ITEM_DROP = 'ITEM_DROP'            # 바닥에 떨어짐: gid, item_id, amount, mob(떨어뜨린 몬스터 종류, 모르면 None)
    ITEM_DROP_END = 'ITEM_DROP_END'    # 바닥에서 사라짐: gid, item_id, amount, picked(내가 주웠나), mob
    ITEM_USE = 'ITEM_USE'              # 내가 아이템을 씀: item_id, remaining
    SKILL_LIST = 'SKILL_LIST'          # 내 스킬 전체: skills{번호: {level, sp, range}}
    QUEST_ADD = 'QUEST_ADD'            # 퀘스트 받음: quest, goals[{mob_id, name, count}]
    QUEST_PROGRESS = 'QUEST_PROGRESS'  # 사냥 수: quest, need, count (+ ident 또는 mob_id)
    QUEST_REMOVE = 'QUEST_REMOVE'      # 퀘스트 끝/삭제: quest
    INSTANCE_QUEUE = 'INSTANCE_QUEUE'  # 인던 대기 순번: name(없을 수 있음), position
    INSTANCE_READY = 'INSTANCE_READY'  # 인던이 열림(대기 끝): name
    STORE_SIGN = 'STORE_SIGN'          # 주변 노점 간판: title, kind('sale'|'buy')
    STORE_SEARCH = 'STORE_SEARCH'      # 게임 안 노점 검색 결과: rows[{item_id, price, amount, shop, refine, cards}]
    STORE_ITEMS = 'STORE_ITEMS'        # 열어 본 노점의 물건: rows[{item_id, price, amount, refine, cards}]
    ITEM_REMOVE = 'ITEM_REMOVE'
    INVENTORY_UPDATE = 'INVENTORY_UPDATE'
    CART_UPDATE = 'CART_UPDATE'
    STORAGE_UPDATE = 'STORAGE_UPDATE'
    UNKNOWN = 'UNKNOWN'


@dataclass(slots=True)
class ReplayEvent:
    timestamp_ms: int                  # 녹화 시작 기준 경과(ms)
    event_type: str
    actor_id: Optional[int] = None
    target_id: Optional[int] = None
    map_name: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    data: dict = field(default_factory=dict)
    raw_packet: Optional[int] = None   # ParsedReplay.packets 인덱스
    confidence: str = MEASURED         # 미검증 후보 패킷이면 ESTIMATE
