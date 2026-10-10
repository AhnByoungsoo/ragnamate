"""LIVE 상태 모니터: 버프·소모품·스킬 효과를 한 가지 형식으로 다룬다 (Qt 비의존).

수명 주기
    ACTIVE  ──(남은 시간 ≤ 경고)──▶ EXPIRING ──(≤ 위험)──▶ CRITICAL ──(종료)──▶ EXPIRED_ALERT ──(N초)──▶ HIDDEN
EXPIRED_ALERT 동안 '재사용!!'로 붉게 강조한다. 이것은 **효과가 끝났다는 알림**이지
게임의 스킬 재사용 대기시간(Cooldown)이 끝났다는 뜻이 아니다. 둘을 섞지 않는다.
- 화면에서 버프 아이콘을 읽는 기능은 없다. 출처는 Replay 패킷(●) 또는 사용자가 건 수동 타이머(○)뿐이다.
"""
import time

ACTIVE, EXPIRING, CRITICAL, EXPIRED_ALERT, HIDDEN = 'ACTIVE', 'EXPIRING', 'CRITICAL', 'EXPIRED_ALERT', 'HIDDEN'
STATE_LABEL = {ACTIVE: '적용 중', EXPIRING: '종료 임박', CRITICAL: '곧 종료', EXPIRED_ALERT: '재사용!!', HIDDEN: '종료'}
STATE_COLOR = {ACTIVE: '#6ddb7c', EXPIRING: '#ffd166', CRITICAL: '#ff9f43', EXPIRED_ALERT: '#ff4d4d',
               HIDDEN: '#7a7a7a'}

# 효과의 출처(무엇이 걸어 준 효과인지)
ITEM_BUFF, SKILL_BUFF, PASSIVE, COOLDOWN, CONSUMABLE, OTHER = \
    'ITEM_BUFF', 'SKILL_BUFF', 'PASSIVE', 'COOLDOWN', 'CONSUMABLE', 'OTHER'
SOURCE_LABEL = {ITEM_BUFF: '아이템 효과', SKILL_BUFF: '스킬 버프', PASSIVE: '상시', COOLDOWN: '재사용 대기',
                CONSUMABLE: '소모품', OTHER: '기타'}
# 이 효과가 어디서 확인된 값인지
FROM_REPLAY, FROM_MANUAL = 'REPLAY', 'MANUAL'
FROM_LABEL = {FROM_REPLAY: '● Replay 패킷에서 확인', FROM_MANUAL: '○ 직접 시작한 계산 타이머'}

# 효과의 성격: 버프 타이머에는 BUFF만 올린다
BUFF, STATE, DEBUFF = 'buff', 'state', 'debuff'
KIND_LABEL = {BUFF: '버프', STATE: '상태 표시(타이머 없음)', DEBUFF: '상태이상'}

DEFAULTS = dict(warn_sec=60, critical_sec=10, reuse_sec=60)


def settings_times(settings):
    s = settings or {}
    return (int(s.get('status_warn_sec', DEFAULTS['warn_sec'])),
            int(s.get('status_critical_sec', DEFAULTS['critical_sec'])),
            int(s.get('status_reuse_sec', DEFAULTS['reuse_sec'])))


class StatusItem:
    """모니터 한 줄. 아이콘·이름·남은 시간·상태·출처를 가진다."""

    def __init__(self, key, name, source=OTHER, origin=FROM_MANUAL, icon_key='', raw='', confidence='',
                 internal=False, favorite=False, kind=BUFF):
        self.key = key
        self.name = name
        self.source = source
        self.origin = origin
        self.icon_key = icon_key or key
        self.raw = raw
        self.confidence = confidence
        self.internal = internal
        self.kind = kind               # 버프가 아닌 것(상태 표시·상태이상)은 '재사용' 알림을 하지 않는다
        self.favorite = favorite
        self.suggest = ''              # 관측으로 얻은 이름 후보(확정 아님)
        self.suggest_skill = None
        self.efst = None
        self.remaining = None          # 초. 모르면 None (숫자를 지어내지 않는다)
        self.total = None
        self.expired_at = None         # 효과가 끝난 시각(재사용 알림용)
        self.state = ACTIVE
        self.alerted = set()

    # ---------------------------------------------------------------- 상태 계산
    def update(self, remaining, total=None, now=None):
        now = time.time() if now is None else now
        if remaining is None or remaining > 0:
            if self.expired_at is not None:        # 다시 걸었다 → 알림 해제하고 처음부터
                self.expired_at = None
                self.alerted.clear()
            # 남은 시간을 모르면(패킷에 remain time이 없는 버프) None 그대로 둔다 — 숫자를 만들지 않는다
            self.remaining, self.total = remaining, total or self.total
        elif self.expired_at is None:
            self.expired_at = now
            self.remaining = 0
        return self

    def expire(self, now=None):
        """더 이상 보이지 않는 효과 → 재사용 알림 구간으로."""
        if self.expired_at is None:
            self.expired_at = time.time() if now is None else now
            self.remaining = 0
        return self

    def refresh_state(self, settings=None, now=None):
        now = time.time() if now is None else now
        warn, critical, reuse = settings_times(settings)
        if self.internal or self.kind != BUFF:   # 우편 개수·무게 70%·상태이상: '재사용' 개념이 없다 → 끝나면 그냥 치운다
            self.state = ACTIVE if self.expired_at is None else HIDDEN
            return self.state
        if self.expired_at is not None:
            gone = now - self.expired_at
            self.state = EXPIRED_ALERT if gone < reuse else HIDDEN
            self.remaining = max(0.0, reuse - gone) if self.state == EXPIRED_ALERT else 0.0
            return self.state
        left = self.remaining
        if left is None:
            self.state = ACTIVE
        elif left <= critical:
            self.state = CRITICAL
        elif left <= warn:
            self.state = EXPIRING
        else:
            self.state = ACTIVE
        return self.state

    @property
    def ratio(self):
        if self.state == EXPIRED_ALERT or self.remaining is None or not self.total:
            return None
        return max(0.0, min(1.0, self.remaining / self.total))

    @property
    def label(self):
        return STATE_LABEL.get(self.state, '')

    @property
    def color(self):
        return STATE_COLOR.get(self.state, STATE_COLOR[ACTIVE])

    def to_row(self):
        return dict(key=self.key, name=self.name, source=self.source, origin=self.origin, icon_key=self.icon_key,
                    remaining=self.remaining, total=self.total, ratio=self.ratio, state=self.state,
                    label=self.label, color=self.color, raw=self.raw, confidence=self.confidence,
                    internal=self.internal, favorite=self.favorite, kind=self.kind,
                    icon='●' if self.origin == FROM_REPLAY else '○',
                    suggest=self.suggest, suggest_skill=self.suggest_skill, efst=self.efst,
                    note='%s · %s' % (SOURCE_LABEL.get(self.source, ''), FROM_LABEL.get(self.origin, '')))


class StatusMonitor:
    """지금 걸려 있는 효과 모음. Replay 버프와 수동 타이머를 같은 형식으로 담는다."""

    def __init__(self, settings, alert=None, clock=time.time):
        self.s = settings
        self.clock = clock
        self.alert = alert or (lambda key, kind, item: None)
        self.items = {}

    # ---------------------------------------------------------------- 입력
    def sync(self, rows, now=None):
        """표시용 행 목록(live.buffs.rows + replay_rows) → 모니터 상태 갱신.

        목록에서 사라진 효과는 지우지 않고 '재사용 알림' 구간으로 넘긴다."""
        now = self.clock() if now is None else now
        seen = set()
        for r in rows:
            key = r['key']
            seen.add(key)
            item = self.items.get(key)
            if item is None:
                item = self.items[key] = StatusItem(
                    key, r['name'], source=r.get('monitor_source', OTHER),
                    origin=FROM_REPLAY if r.get('source') == 'REPLAY' else FROM_MANUAL,
                    icon_key=r.get('icon_key') or key, raw=r.get('raw', ''),
                    confidence=r.get('confidence', ''), internal=r.get('internal', False))
            item.name = r['name']
            item.internal = r.get('internal', item.internal)
            item.kind = r.get('kind', item.kind)
            item.raw = r.get('raw', item.raw)
            item.favorite = key in (self.s.get('status_favorites') or [])
            item.suggest, item.suggest_skill = r.get('suggest', ''), r.get('suggest_skill')
            item.efst = r.get('efst', item.efst)
            item.update(r.get('remaining'), r.get('total'), now)
        for key, item in self.items.items():
            if key not in seen:
                item.expire(now)
        return self.tick(now)

    def tick(self, now=None):
        """상태 갱신 + 알림 발생. 반환: 사라져도 되는 항목을 뺀 행 목록."""
        now = self.clock() if now is None else now
        warn, critical, reuse = settings_times(self.s)
        rows, drop = [], []
        for key, item in self.items.items():
            state = item.refresh_state(self.s, now)
            if state == HIDDEN:
                drop.append(key)
                continue
            self._alerts(item, state, warn, critical)
            rows.append(item)
        for key in drop:
            del self.items[key]
        rows.sort(key=lambda i: (not i.favorite, i.state != EXPIRED_ALERT,
                                 i.remaining if i.remaining is not None else 1e9))
        return rows

    def _alerts(self, item, state, warn, critical):
        cfg = (self.s.get('status_alerts') or {}).get(item.key, {})
        for stage, enabled_key, message in (
                (EXPIRING, 'warn', '%s 곧 종료 (%d초 남음)'),
                (CRITICAL, 'critical', '%s 종료 직전 (%d초)'),
                (EXPIRED_ALERT, 'reuse', '%s 효과 종료 — 재사용!!')):
            if state != stage or stage in item.alerted:
                continue
            if not cfg.get(enabled_key, True):
                item.alerted.add(stage)
                continue
            item.alerted.add(stage)
            left = int(item.remaining or 0)
            text = message % (item.name, left) if '%d' in message else message % item.name
            self.alert(item.key, stage, text)

    # ---------------------------------------------------------------- 즐겨찾기
    def toggle_favorite(self, key):
        favs = list(self.s.get('status_favorites') or [])
        favs.remove(key) if key in favs else favs.append(key)
        self.s['status_favorites'] = favs
        try:
            self.s.save()
        except Exception:
            pass
        if key in self.items:
            self.items[key].favorite = key in favs
        return key in favs

    def visible(self, rows, favorites_only=False, show_internal=None, show_nonbuff=None):
        """기본 화면에 내보낼 것만. 게임 내부 상태(우편 수 등)와 버프가 아닌 것(무게 70%·탑승·상태이상)은
        기본으로 감춘다 — 즐겨찾기한 것은 보인다."""
        if show_internal is None:
            show_internal = bool(self.s.get('status_show_internal', False))
        if show_nonbuff is None:
            show_nonbuff = bool(self.s.get('status_show_nonbuff', False))
        out = [i for i in rows if (show_internal or not i.internal)
               and (show_nonbuff or i.kind == BUFF or i.favorite)]
        if favorites_only:
            out = [i for i in out if i.favorite] or out
        limit = int(self.s.get('status_max_items', 12) or 0)
        return out[:limit] if limit else out
