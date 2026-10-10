"""Buff Timer: 사용자가 등록한 수동 타이머 + Replay에서 확인된 Buff 표시.

화면에서 Buff 아이콘을 읽는 기능은 없다. 따라서
- 수동 타이머(MANUAL)는 사용자가 [시작]을 누른 시각 기준의 계산값(○)이고,
- Replay 타이머(REPLAY)는 패킷의 remain time에서 온 값(●)이며 남은 시간이 없으면 '?'로 둔다.
게임에 입력을 보내지 않는다. 알림은 이 프로그램의 팝업/소리뿐이다.
"""
import time
from dataclasses import dataclass, field
from typing import Optional

from core.fmt import mmss                                # noqa: F401  (ui에서 live.buffs.mmss로도 쓴다)

MANUAL, REPLAY = 'MANUAL', 'REPLAY'
RUNNING, WARN, DANGER, EXPIRED = 'RUNNING', 'WARN', 'DANGER', 'EXPIRED'


def norm_key(name):
    return (name or '').strip().lower()


@dataclass
class BuffDef:
    """사용자가 등록한 버프 한 줄. 지속시간은 사용자가 직접 입력한 값만 쓴다(게임 값을 추정하지 않는다)."""
    name: str
    duration: int                       # 초
    alert_sec: int = 10                 # 만료 몇 초 전에 알릴지 (0이면 사전 알림 없음)
    sound: str = ''

    @property
    def key(self):
        return norm_key(self.name)

    def to_dict(self):
        return dict(name=self.name, duration=int(self.duration), alert_sec=int(self.alert_sec), sound=self.sound)

    @staticmethod
    def from_dict(d):
        return BuffDef(str(d.get('name', '')).strip(), int(d.get('duration') or 0),
                       int(d.get('alert_sec') or 0), str(d.get('sound') or ''))


@dataclass
class BuffTimer:
    name: str
    started_at: float
    expires_at: float
    alert_sec: int = 10
    sound: str = ''
    source: str = MANUAL
    warned: bool = False
    expired_notified: bool = False
    _paused_remaining: Optional[float] = field(default=None, repr=False)

    def remaining(self, now):
        if self._paused_remaining is not None:
            return self._paused_remaining
        return self.expires_at - now

    def total(self):
        return max(1e-6, self.expires_at - self.started_at)

    def state(self, now, warn_pct, danger_pct):
        left = self.remaining(now)
        if left <= 0:
            return EXPIRED
        ratio = left / self.total() * 100
        if ratio <= danger_pct or left <= max(0, self.alert_sec):
            return DANGER
        return WARN if ratio <= warn_pct else RUNNING


class BuffTimerManager:
    """수동 버프 타이머 모음. Qt에 의존하지 않는다(테스트/HUD/오버레이에서 같이 쓴다)."""

    def __init__(self, settings, alert=None, clock=time.time):
        self.s = settings
        self.clock = clock
        self.alert = alert or (lambda key, msg, sound: None)
        self.timers = {}                       # key → BuffTimer
        self.paused = False
        self._paused_at = None

    # ---------------------------------------------------------------- 정의(설정에 저장)
    @property
    def defs(self):
        return [BuffDef.from_dict(d) for d in (self.s.get('buff_timers') or []) if d.get('name')]

    def save_defs(self, defs):
        self.s['buff_timers'] = [d.to_dict() for d in defs]
        try:
            self.s.save()
        except Exception:
            pass

    def add_def(self, d):
        defs = [x for x in self.defs if x.key != d.key]
        defs.append(d)
        self.save_defs(defs)
        return d

    def remove_def(self, key):
        self.save_defs([x for x in self.defs if x.key != key])
        self.timers.pop(key, None)

    def find(self, key):
        return next((d for d in self.defs if d.key == key), None)

    # ---------------------------------------------------------------- 타이머
    def start(self, key, now=None):
        """시작 또는 갱신(다시 걸기). 등록되지 않은 key는 무시."""
        d = self.find(norm_key(key))
        if not d or d.duration <= 0:
            return None
        now = self.clock() if now is None else now
        t = BuffTimer(d.name, now, now + d.duration, d.alert_sec, d.sound, MANUAL)
        if self.paused:
            t._paused_remaining = d.duration
        self.timers[d.key] = t
        return t

    def stop(self, key):
        return self.timers.pop(norm_key(key), None)

    def stop_all(self):
        self.timers.clear()

    def clear_expired(self, now=None):
        now = self.clock() if now is None else now
        for k in [k for k, t in self.timers.items() if t.remaining(now) <= 0]:
            del self.timers[k]

    def set_paused(self, on, now=None):
        """전체 일시정지: 남은 시간을 그대로 붙잡아 둔다."""
        now = self.clock() if now is None else now
        if on == self.paused:
            return
        self.paused = on
        for t in self.timers.values():
            if on:
                t._paused_remaining = max(0.0, t.expires_at - now)
            elif t._paused_remaining is not None:
                total = t.total()
                t.expires_at = now + t._paused_remaining
                t.started_at = t.expires_at - total
                t._paused_remaining = None

    def tick(self, now=None):
        """알림 판정. 발생한 알림 목록 [(key, message)] 반환."""
        now = self.clock() if now is None else now
        fired = []
        if self.paused:
            return fired
        for key, t in list(self.timers.items()):
            left = t.remaining(now)
            if left <= 0:
                if not t.expired_notified:
                    t.expired_notified = True
                    fired.append((key, '⏱ %s 종료' % t.name))
                    self.alert('buff_%s' % key, '⏱ %s 종료' % t.name, t.sound)
                if not self.s.get('buff_keep_expired'):
                    del self.timers[key]
            elif t.alert_sec and left <= t.alert_sec and not t.warned:
                t.warned = True
                msg = '⏱ %s %d초 남음' % (t.name, int(round(left)))
                fired.append((key, msg))
                self.alert('buff_%s' % key, msg, t.sound)
        return fired

    # ---------------------------------------------------------------- 표시용
    def rows(self, now=None):
        now = self.clock() if now is None else now
        warn, danger = self.s.get('buff_warn_pct', 30), self.s.get('buff_danger_pct', 10)
        out = []
        for key, t in self.timers.items():
            left = t.remaining(now)
            out.append(dict(key=key, name=t.name, remaining=max(0.0, left), total=t.total(),
                            ratio=max(0.0, min(1.0, left / t.total())), source=t.source, icon='○',
                            monitor_source='SKILL_BUFF', icon_key='manual_%s' % key, raw='', internal=False,
                            state=t.state(now, warn, danger), paused=self.paused,
                            note='일시정지' if self.paused else ''))
        return sorted(out, key=lambda r: r['remaining'])


LONG_TERM_SEC = 86400
NEW_SEC = 15                                  # 이 시간 안에 새로 걸린 Buff는 '새로 걸림'으로 표시


def visible_rows(rows, settings):
    """오버레이/HUD에 실제로 내보낼 행. 만료·상시 버프를 감추고 남은 시간 순으로 자른다(사용자가 조절).
    buff_overlay_under_sec(기본 30초)가 있으면 곧 끝나는 것과 방금 끝난 '재사용!!'만 남겨 게임 화면을 덜 가린다."""
    under = int(settings.get('buff_overlay_under_sec', 0) or 0)
    out = []
    for r in rows:
        left = r.get('remaining')
        if settings.get('buff_hide_expired', True) and (r.get('state') == EXPIRED or left == 0):
            continue
        if settings.get('buff_hide_long', True) and left is not None and left > LONG_TERM_SEC:
            continue
        if under and r.get('state') != 'EXPIRED_ALERT' and (left is None or left > under):
            continue                                   # 남은 시간을 모르는 것도 오버레이에서는 감춘다
        out.append(r)
    out.sort(key=lambda r: (r.get('remaining') is None, r.get('remaining') or 0))
    limit = int(settings.get('buff_max_rows', 12) or 0)
    return out[:limit] if limit else out


def _replay_note(end, left):
    if end is None:
        return '남은 시간 미수신'
    if left <= 0:
        return '예상 만료 시각 지남 · 종료 패킷 미수신'
    if left > LONG_TERM_SEC:
        return '패킷 remain time 기준 · 24시간 이상(상시 버프일 수 있음)'
    return '패킷 remain time 기준 예상 만료'


ORIGIN_NOTE = {'user': '', 'rathena': '외부 목록(rAthena) · 확인 필요', 'import': '가져온 이름 · 확인 필요'}


def replay_rows(snap, name_fn=None, origin_fn=None, display=None):
    """Replay 스냅샷에서 확인된 Buff를 표시용 행으로. 남은 시간을 모르면 만들어내지 않는다.

    display(core.display.DisplayNames)를 주면 사용자 화면용 한국어 이름과 내부 Symbol을 함께 담는다."""
    if not snap:
        return []
    name_fn = name_fn or (lambda i: '버프 #%s' % i)
    origin_fn = origin_fn or (lambda i: '')
    clock = snap.get('duration_ms') or 0
    stale = snap.get('state') in ('STALLED', 'FINALIZING', 'FINISHED')
    out = []
    for b in snap.get('buffs') or []:
        end = b.get('expected_end')
        left = (end - clock) / 1000 if end is not None else None
        started = b.get('refreshed_at') or b.get('started_at')
        shown = display.status(b.get('buff_id')) if display is not None else None
        # 버프 타이머에 올릴 것인가: 타이머가 없는 상태(무게·탑승 등)와 상태이상은 '버프'가 아니다
        kind = 'buff'
        if b.get('timed') is False:
            kind = 'state'
        elif hasattr(display, 'status_kind'):
            kind = display.status_kind(b.get('buff_id'))
        out.append(dict(key='replay_%s' % b.get('buff_id'), efst=b.get('buff_id'),
                        name=shown.text if shown else name_fn(b.get('buff_id')),
                        raw=shown.raw if shown else '', internal=bool(shown and shown.internal), kind=kind,
                        confidence=shown.confidence if shown else '',
                        monitor_source='OTHER',
                        icon_key='efst_%s' % b.get('buff_id'),
                        new=started is not None and 0 <= clock - started <= NEW_SEC * 1000,
                        remaining=max(0.0, left) if left is not None else None,
                        total=(end - (b.get('refreshed_at') or b.get('started_at') or 0)) / 1000 if end is not None else None,
                        source=REPLAY, icon='●', state=EXPIRED if b.get('ending') == 'ESTIMATED' else RUNNING,
                        paused=stale, name_source=origin_fn(b.get('buff_id')),
                        note=' · '.join(x for x in (_replay_note(end, left), 'Replay 정체' if stale else '',
                                                    ORIGIN_NOTE.get(origin_fn(b.get('buff_id')), '')) if x)))
    return out
