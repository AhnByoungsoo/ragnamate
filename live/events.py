"""공통 GameEvent 계층: Replay / Screen(OCR·Vision) / DB / Manual 이벤트를 한 형식으로 다룬다."""
import hashlib
import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

REPLAY, SCREEN_VISION, SCREEN_OCR, DATABASE, MANUAL = 'REPLAY', 'SCREEN_VISION', 'SCREEN_OCR', 'DATABASE', 'MANUAL'
HIGH, MEDIUM, LOW = 'HIGH', 'MEDIUM', 'LOW'
SOURCE_ICON = {REPLAY: '● Replay', SCREEN_VISION: '○ Screen', SCREEN_OCR: '○ Screen', DATABASE: '△ DB',
               MANUAL: '! 수동'}
DEFAULT_PRIORITY = [REPLAY, SCREEN_VISION, SCREEN_OCR, DATABASE, MANUAL]


class GE:
    GAME_STARTED = 'GAME_STARTED'
    GAME_CLOSED = 'GAME_CLOSED'
    MAP_DETECTED = 'MAP_DETECTED'
    MAP_CHANGED = 'MAP_CHANGED'
    ITEM_GAIN = 'ITEM_GAIN'
    ITEM_REMOVE = 'ITEM_REMOVE'
    EXP_CHANGE = 'EXP_CHANGE'            # 화면 EXP %(또는 수치) 변화
    BASE_EXP_GAIN = 'BASE_EXP_GAIN'
    JOB_EXP_GAIN = 'JOB_EXP_GAIN'
    ZENY_CHANGE = 'ZENY_CHANGE'
    HP_CHANGE = 'HP_CHANGE'
    SP_CHANGE = 'SP_CHANGE'
    AP_CHANGE = 'AP_CHANGE'
    WEIGHT_CHANGE = 'WEIGHT_CHANGE'
    BUFF_DETECTED = 'BUFF_DETECTED'
    BUFF_LOST = 'BUFF_LOST'
    MONSTER_DEATH = 'MONSTER_DEATH'
    DAMAGE = 'DAMAGE'
    SKILL_USE = 'SKILL_USE'
    PLAYER_ACTIVITY = 'PLAYER_ACTIVITY'
    PLAYER_IDLE = 'PLAYER_IDLE'
    SESSION_START = 'SESSION_START'
    SESSION_PAUSE = 'SESSION_PAUSE'
    SESSION_RESUME = 'SESSION_RESUME'
    SESSION_END = 'SESSION_END'
    QUEST_EVENT = 'QUEST_EVENT'
    UNKNOWN = 'UNKNOWN'


# 출처별로 만들 수 있는 이벤트 (Replay 전용 이벤트를 화면에서 지어내지 않기 위해)
SCREEN_CAPABLE = {GE.GAME_STARTED, GE.GAME_CLOSED, GE.MAP_DETECTED, GE.MAP_CHANGED, GE.ITEM_GAIN, GE.EXP_CHANGE,
                  GE.BASE_EXP_GAIN, GE.JOB_EXP_GAIN, GE.ZENY_CHANGE, GE.HP_CHANGE, GE.SP_CHANGE, GE.AP_CHANGE,
                  GE.WEIGHT_CHANGE, GE.BUFF_DETECTED, GE.BUFF_LOST, GE.PLAYER_ACTIVITY, GE.PLAYER_IDLE,
                  GE.QUEST_EVENT}
REPLAY_ONLY = {GE.MONSTER_DEATH, GE.DAMAGE, GE.SKILL_USE}
ACTIVITY_TYPES = {GE.ITEM_GAIN, GE.EXP_CHANGE, GE.BASE_EXP_GAIN, GE.JOB_EXP_GAIN, GE.ZENY_CHANGE, GE.PLAYER_ACTIVITY,
                  GE.MONSTER_DEATH, GE.DAMAGE, GE.SKILL_USE}

_ids = itertools.count(1)


@dataclass
class GameEvent:
    event_type: str
    source: str
    confidence: str = MEDIUM
    timestamp: float = field(default_factory=time.time)
    character: Optional[str] = None
    map: Optional[str] = None
    actor_id: Optional[int] = None
    target_id: Optional[int] = None
    item_id: Optional[int] = None
    item_name: Optional[str] = None
    quantity: Optional[int] = None
    value: Optional[float] = None
    x: Optional[int] = None
    y: Optional[int] = None
    metadata: dict = field(default_factory=dict)
    raw_data: object = None
    id: int = field(default_factory=lambda: next(_ids))

    def key(self):
        """중복 판정 키 (시간 제외)."""
        return (self.event_type, self.item_id or self.item_name, self.quantity)


class EventDeduplicator:
    """같은 사건이 Replay와 Screen에서 둘 다 잡히거나, OCR이 같은 줄을 여러 번 읽는 경우를 걸러낸다.

    - 같은 출처 + 같은 message_hash 는 window 안에서 1회만.
    - 다른 출처의 같은 key(종류/아이템/수량)가 window 안에 있으면 우선순위가 높은 출처만 남긴다.
      이미 반영된 낮은 출처 이벤트는 superseded 목록으로 돌려줘 집계에서 빼게 한다.
    """

    def __init__(self, window_sec=5.0, priority=None, per_type_priority=None):
        self.window = window_sec
        self.priority = priority or DEFAULT_PRIORITY
        self.per_type = per_type_priority or {}
        self.recent = deque()
        self.hashes = {}

    def rank(self, ev):
        order = self.per_type.get(ev.event_type, self.priority)
        return order.index(ev.source) if ev.source in order else len(order)

    def offer(self, ev):
        """반환: (accept: bool, superseded: [GameEvent])"""
        now = ev.timestamp
        while self.recent and now - self.recent[0].timestamp > self.window:
            self.recent.popleft()
        for h, t in list(self.hashes.items()):
            if now - t > max(self.window, 30):
                del self.hashes[h]
        mh = ev.metadata.get('message_hash')
        if mh:
            if mh in self.hashes:
                return False, []
            self.hashes[mh] = now
        superseded = []
        if ev.event_type in (GE.ITEM_GAIN, GE.ZENY_CHANGE, GE.BASE_EXP_GAIN, GE.JOB_EXP_GAIN):
            for old in list(self.recent):
                if old.key() == ev.key() and old.source != ev.source and abs(now - old.timestamp) <= self.window:
                    if self.rank(old) <= self.rank(ev):
                        return False, []
                    superseded.append(old)
                    self.recent.remove(old)
        self.recent.append(ev)
        return True, superseded


def message_hash(text):
    return hashlib.sha1((text or '').strip().encode('utf-8')).hexdigest()[:16]


class EventBus:
    """스레드 안전 pub/sub. 구독자는 발행 스레드에서 호출되므로 UI는 Qt 시그널로 넘겨받아야 한다."""

    def __init__(self, dedup=None, keep=2000):
        self.subs = []
        self.dedup = dedup or EventDeduplicator()
        self.log = deque(maxlen=keep)
        self.lock = threading.Lock()

    def subscribe(self, fn):
        self.subs.append(fn)

    def publish(self, ev):
        with self.lock:
            ok, superseded = self.dedup.offer(ev)
            if not ok:
                return False
            self.log.append(ev)
        for s in superseded:
            s.metadata['superseded_by'] = ev.id
        for fn in list(self.subs):
            try:
                fn(ev, superseded)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('이벤트 구독자 오류')
        return True
