"""Passive Hunt Session: Replay 없이 화면 이벤트만으로 사냥 세션을 만들고, Replay가 생기면 Hybrid로 승격.

규칙
- 시작: 맵 진입 후 활동 이벤트(아이템/EXP/Zeny 등)가 start_window 안에 start_min 회 이상.
- 자동 일시정지: pause_sec 동안 활동이 없으면 PAUSED, 활동이 다시 오면 RESUME.
- 수익: 화면에서 실제 감지한 아이템 가치 + 확인된 Zeny (신뢰도 LOW 아이템은 제외하고 검토 목록으로).
- 가짜 데이터 금지: 화면으로 알 수 없는 Kill/DPS는 Replay가 붙었을 때만 채운다.
"""
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from live.events import ACTIVITY_TYPES, GE, LOW, REPLAY, SCREEN_OCR, SCREEN_VISION

WAITING, SCREEN, HYBRID, REPLAY_ONLY, PAUSED = 'WAITING', 'SCREEN', 'HYBRID', 'REPLAY', 'PAUSED'


@dataclass
class PassiveHuntSession:
    start_time: float
    map: Optional[str] = None
    map_source: str = '?'
    mode: str = SCREEN
    state: str = 'ACTIVE'                 # ACTIVE / PAUSED / ENDED
    end_time: Optional[float] = None
    paused_time: float = 0.0
    last_activity: float = 0.0
    _pause_started: Optional[float] = None
    items: dict = field(default_factory=dict)          # key → {item_id,name,qty,unit,value,source,confidence}
    review: list = field(default_factory=list)         # 신뢰도 LOW 아이템(수익 제외)
    zeny_gain: int = 0
    zeny_known: bool = False
    exp_first: Optional[float] = None                  # 화면 EXP %
    exp_last: Optional[float] = None
    exp_levelups: int = 0
    base_exp_gain: int = 0                             # 채팅 등으로 확인된 절대 EXP
    hp: list = field(default_factory=list)
    sp: list = field(default_factory=list)
    ap: list = field(default_factory=list)
    weight: Optional[float] = None
    replay: dict = field(default_factory=dict)         # Replay 보강 통계(kills, dps 등)
    replay_since: Optional[float] = None
    alerts: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)   # 출처별 이벤트 수(정확도 비교용)

    @property
    def elapsed(self):
        end = self.end_time or time.time()
        paused = self.paused_time + ((end - self._pause_started) if self._pause_started else 0)
        return max(0.0, end - self.start_time - paused), max(0.0, end - self.start_time)

    def item_value(self):
        return sum(v['value'] or 0 for v in self.items.values())

    def profit(self):
        return self.item_value() + (self.zeny_gain if self.zeny_known else 0)

    def per_hour(self, v):
        active, _ = self.elapsed
        return v * 3600 / active if active >= 60 and v is not None else None

    def exp_pct_per_hour(self):
        if self.exp_first is None or self.exp_last is None:
            return None
        delta = self.exp_last - self.exp_first + 100 * self.exp_levelups
        return self.per_hour(delta)

    def top_drops(self, n=5):
        return sorted(self.items.values(), key=lambda v: -(v['value'] or 0))[:n]


class SessionManager:
    def __init__(self, price_lookup, settings, alert=None, clock=time.time):
        self.price_lookup = price_lookup
        self.s = settings
        self.alert = alert or (lambda kind, msg: None)
        self.clock = clock
        self.session = None
        self.history = []
        self._recent_activity = []
        self._pending = []                  # 세션 시작 판정 전 이벤트(시작되면 세션에 포함)
        self.map = None
        self.map_source = '?'
        self.replay_active = False

    # ---------------------------------------------------------------- 이벤트 입력
    def on_event(self, ev, superseded=()):
        now = ev.timestamp
        if ev.event_type in (GE.MAP_CHANGED, GE.MAP_DETECTED):
            self._set_map(ev)
            return
        if ev.event_type == GE.GAME_CLOSED:
            self.end(now)
            return
        for old in superseded:                       # 낮은 우선순위 출처로 이미 집계된 것 되돌리기
            self._undo(old)
        if self.session is None:
            win = self.s.get('live_start_window', 60)
            self._pending = [e for e in self._pending if now - e.timestamp <= win] + [ev]
        if ev.event_type in ACTIVITY_TYPES and not (ev.event_type == GE.ITEM_GAIN and ev.confidence == LOW):
            started = self.session is None
            self._activity(now)
            if started and self.session is not None:          # 방금 시작: 시작 판정에 쓰인 이벤트까지 포함
                pending, self._pending = self._pending, []
                for e in pending:
                    if e.timestamp >= self.session.start_time:
                        self._apply(self.session, e)
                return
        s = self.session
        if s is None:
            return
        self._apply(s, ev)

    def _apply(self, s, ev):
        now = ev.timestamp
        s.counts[(ev.source, ev.event_type)] += 1
        et = ev.event_type
        if et == GE.ITEM_GAIN:
            self._add_item(s, ev)
        elif et == GE.ZENY_CHANGE and ev.value is not None:
            s.zeny_gain += int(ev.value) if ev.metadata.get('delta') else 0
            if ev.metadata.get('delta'):
                s.zeny_known = True
        elif et == GE.EXP_CHANGE and ev.value is not None:
            if s.exp_first is None:
                s.exp_first = ev.value
            elif s.exp_last is not None and ev.value + 50 < s.exp_last:     # 100% → 0% 근처: 레벨업
                s.exp_levelups += 1
            s.exp_last = ev.value
        elif et == GE.BASE_EXP_GAIN and ev.value:
            s.base_exp_gain += int(ev.value)
        elif et in (GE.HP_CHANGE, GE.SP_CHANGE, GE.AP_CHANGE):
            getattr(s, {GE.HP_CHANGE: 'hp', GE.SP_CHANGE: 'sp', GE.AP_CHANGE: 'ap'}[et]).append((now, ev.value))
        elif et == GE.WEIGHT_CHANGE:
            s.weight = ev.value

    def _set_map(self, ev):
        name = ev.metadata.get('map_name') or ev.map
        src = '●' if ev.source == REPLAY else '○'
        if self.map and name != self.map and self.session:
            self.end(ev.timestamp)                  # 맵이 바뀌면 사냥 세션을 나눈다
        self.map, self.map_source = name, src
        if self.session:
            self.session.map, self.session.map_source = name, src

    def _activity(self, now):
        s = self.session
        if s is None:
            self._recent_activity = [t for t in self._recent_activity if now - t <= self.s.get('live_start_window', 60)]
            self._recent_activity.append(now)
            if len(self._recent_activity) >= self.s.get('live_start_min', 2):
                self.start(self._recent_activity[0]).last_activity = now
            return
        if s.state == 'PAUSED':
            s.paused_time += now - s._pause_started
            s._pause_started = None
            s.state = 'ACTIVE'
        s.last_activity = now

    def start(self, t):
        self.session = PassiveHuntSession(start_time=t, map=self.map, map_source=self.map_source, last_activity=t,
                                          mode=HYBRID if self.replay_active else SCREEN)
        if self.replay_active:
            self.session.replay_since = t
        self._recent_activity = []
        return self.session

    def tick(self, now=None):
        """주기적으로 호출: 자동 일시정지 판단."""
        now = now or self.clock()
        s = self.session
        if s and s.state == 'ACTIVE' and now - s.last_activity >= self.s.get('live_pause_sec', 120):
            s.state = 'PAUSED'
            s._pause_started = s.last_activity + self.s.get('live_pause_sec', 120)

    def end(self, t=None):
        s = self.session
        if not s:
            return None
        t = t or self.clock()
        if s._pause_started:
            s.paused_time += max(0, t - s._pause_started)
            s._pause_started = None
        s.end_time, s.state = t, 'ENDED'
        self.history.append(s)
        self.session = None
        return s

    # ---------------------------------------------------------------- Replay 보강
    def set_replay(self, active, stats=None, now=None):
        """Replay 녹화 감지/중단. 현재 세션을 새로 만들지 않고 HYBRID로 승격·강등한다."""
        self.replay_active = active
        s = self.session
        if s is None:
            return
        if active:
            if s.mode != HYBRID:
                s.mode, s.replay_since = HYBRID, now or self.clock()
            if stats:
                s.replay = stats
        elif s.mode == HYBRID:
            s.mode = SCREEN

    # ---------------------------------------------------------------- 아이템/가격
    def _add_item(self, s, ev):
        if ev.confidence == LOW or ev.item_id is None:
            s.review.append(dict(time=ev.timestamp, name=ev.item_name, qty=ev.quantity, text=ev.raw_data,
                                 reason=ev.metadata.get('match', 'low_confidence')))
            return
        key = ev.item_id
        cur = s.items.get(key)
        price = None
        try:
            price = self.price_lookup({ev.item_id: ev.item_name}).get(ev.item_id)
        except Exception:
            price = None
        unit = price.lowest_price if price and price.has_price else None
        if cur:
            cur['qty'] += ev.quantity or 1
            cur['sources'].add(ev.source)
        else:
            cur = s.items[key] = dict(item_id=ev.item_id, name=ev.item_name, qty=ev.quantity or 1, unit=unit,
                                      sources={ev.source}, ids=[])
        cur['ids'].append(ev.id)
        cur['value'] = cur['unit'] * cur['qty'] if cur['unit'] is not None else None
        total = (unit or 0) * (ev.quantity or 1)
        if unit is not None and total >= self.s.get('high_value_threshold', 5_000_000):
            msg = '★ HIGH VALUE DROP  %s ×%d  %s Z' % (ev.item_name, ev.quantity or 1, '{:,}'.format(total))
            s.alerts.append((ev.timestamp, msg))
            self.alert('high_value', msg)

    def _undo(self, old):
        s = self.session
        if not s or old.event_type != GE.ITEM_GAIN or old.item_id not in s.items:
            return
        cur = s.items[old.item_id]
        if old.id in cur['ids']:
            cur['ids'].remove(old.id)
            cur['qty'] -= old.quantity or 1
            if cur['qty'] <= 0:
                del s.items[old.item_id]
            else:
                cur['value'] = cur['unit'] * cur['qty'] if cur['unit'] is not None else None


def accuracy(session):
    """Replay를 기준(ground truth)으로 화면 감지율 비교."""
    c = session.counts
    out = {}
    for et in (GE.ITEM_GAIN, GE.ZENY_CHANGE, GE.BASE_EXP_GAIN):
        rep = c.get((REPLAY, et), 0)
        scr = c.get((SCREEN_OCR, et), 0) + c.get((SCREEN_VISION, et), 0)
        if rep:
            out[et] = dict(replay=rep, screen=scr, rate=round(scr * 100 / rep, 1))
    return out
