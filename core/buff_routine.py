"""버프 루틴: 내 버퍼 캐릭터의 Replay에서 '실제로 쓴 버프 스킬'과 '버프를 받은 사람'을 읽어 순서를 만든다.

- 버프 스킬 판정은 **증거 기반**이다: 스킬을 쓴 직후(기본 0.5초) 그 대상에게 상태(BUFF_START)가 생긴 것만 버프로 본다.
  스킬 이름만 보고 버프라고 단정하지 않는다.
- 상태가 **10초 이상 가는 것**만 버프 루틴에 넣는다. 공격 스킬도 0.1~1초짜리 상태를 남기기 때문이다
  (실측: 천라지망 90ms / 블레싱·속도증가 60~120초). 지속시간을 모르는 상태는 지우지 않고 그대로 둔다.
- 대상이 PC(플레이어)인 스킬만 '타겟팅 버프'로 본다. 몬스터 대상 스킬은 버프 루틴에 넣지 않는다.
- 이 파일은 순서를 만들고 보여 주기 위한 것이다. **게임에 입력을 보내지 않는다.**
"""
import statistics
from dataclasses import asdict, dataclass, field

WINDOW_MS = 500          # 스킬 사용 후 이 시간 안에 생긴 상태만 그 스킬의 결과로 본다
MIN_BUFF_MS = 10000     # 이보다 짧게 가는 상태는 버프로 보지 않는다(공격 스킬의 부수 상태)
PARTY, TARGET = 'PARTY', 'TARGET'
SCOPE_LABEL = {PARTY: '파티·자신', TARGET: '대상 지정'}
OBJ_PC = 0               # analyzer.normalizer 의 유닛 종류: 0 = 플레이어


def _is_pc(parsed, actor_id):
    return (getattr(parsed, 'actors', {}) or {}).get(actor_id, {}).get('obj_type') == OBJ_PC


def _name(parsed, actor_id):
    return ((getattr(parsed, 'actors', {}) or {}).get(actor_id) or {}).get('name') or ''


def _duration(buff):
    """그 상태가 얼마나 가는지(ms). 패킷에 없으면 None — 추정하지 않는다."""
    return buff.data.get('max_ms') or buff.data.get('remain_ms') or None


def analyze(parsed, skill_name=None, window_ms=WINDOW_MS, min_ratio=0.6, min_buff_ms=MIN_BUFF_MS):
    """Replay → dict(party[자신·파티 버프], target[대상 지정 버프], members[버프 받은 사람], unknown[버프로 보지 않음]).

    skill_name(skill_id) → 표시 이름. 없으면 '스킬 #id'.
    unknown 의 reason: '상태 없음' (스킬 직후 아무 상태도 안 생김) / '너무 짧음' (상태가 min_buff_ms 미만).
    """
    skill_name = skill_name or (lambda sid: '스킬 #%s' % sid)
    me = getattr(parsed, 'player_aid', None)
    uses = [e for e in parsed.events
            if e.event_type == 'SKILL_USE' and e.actor_id == me and e.data.get('skill') is not None]
    buffs = [e for e in parsed.events if e.event_type == 'BUFF_START']
    party, target, members, unknown, tried = {}, {}, {}, {}, {}

    def put_unknown(sid, name, count, reason):
        row = unknown.setdefault(sid, dict(skill_id=sid, name=name, count=0, reason=reason))
        row['count'] += count

    for e in uses:
        sid = e.data['skill']
        tid = e.target_id if e.target_id not in (None, me) else me
        tried[sid] = tried.get(sid, 0) + 1
        if tid != me and not _is_pc(parsed, tid):
            continue                     # 몬스터 대상 = 공격 스킬. 버프 루틴에 넣지 않는다
        # 이 스킬 직후 **그 대상에게** 생긴 상태 — 이름이 아니라 패킷으로 판정한다
        got = [b for b in buffs if b.actor_id == tid and 0 <= b.timestamp_ms - e.timestamp_ms <= window_ms]
        if not got:
            put_unknown(sid, skill_name(sid), 1, '상태 없음')
            continue
        if tid == me:
            row = party.setdefault(sid, dict(skill_id=sid, name=skill_name(sid), count=0, scope=PARTY, ms=[]))
        else:
            row = target.setdefault(sid, dict(skill_id=sid, name=skill_name(sid), count=0, scope=TARGET,
                                              targets=[], ms=[]))
            who = _name(parsed, tid)
            if who and who not in row['targets']:
                row['targets'].append(who)
            if who:
                members[who] = members.get(who, 0) + 1
        row['count'] += 1
        row['ms'] += [ms for ms in map(_duration, got) if ms]
    for rows in (party, target):
        for sid in list(rows):
            row = rows[sid]
            row['remain_ms'] = statistics.median(row['ms']) if row['ms'] else None
            del row['ms']
            # 가끔 우연히 겹친 것, 그리고 공격 스킬이 남기는 짧은 상태는 버프로 보지 않는다
            if row['count'] < tried.get(sid, 0) * min_ratio:
                put_unknown(sid, row['name'], rows.pop(sid)['count'], '상태 없음')
            elif row['remain_ms'] is not None and row['remain_ms'] < min_buff_ms:
                put_unknown(sid, row['name'], rows.pop(sid)['count'], '너무 짧음')
    order = lambda rows: sorted(rows.values(), key=lambda r: -r['count'])          # noqa: E731
    return dict(party=order(party), target=order(target),
                members=[n for n, _ in sorted(members.items(), key=lambda kv: -kv[1])],
                unknown=order(unknown))


@dataclass
class Step:
    """순서 한 줄. hotkey 는 사용자가 게임에서 그 스킬에 걸어 둔 키(라그메이트가 누르지 않는다)."""
    skill_id: int = 0
    name: str = ''
    scope: str = PARTY
    target: str = ''
    hotkey: str = ''
    note: str = ''

    @property
    def label(self):
        base = '%s%s' % (self.name, (' → %s' % self.target) if self.target else '')
        return '%s  [%s]' % (base, self.hotkey) if self.hotkey else base


@dataclass
class Routine:
    name: str = '기본 버프'
    steps: list = field(default_factory=list)

    def to_dict(self):
        return dict(name=self.name, steps=[asdict(s) for s in self.steps])

    @staticmethod
    def from_dict(d):
        return Routine(d.get('name') or '기본 버프', [Step(**s) for s in d.get('steps') or []])


def build(analysis, members=None, hotkeys=None):
    """분석 결과 → 순서: 파티·자신 버프 먼저, 그다음 사람마다 대상 지정 버프.

    members 를 주면 그 사람들에게 돌린다(없으면 Replay에서 버프를 받은 사람).
    """
    hotkeys = hotkeys or {}
    members = list(members if members is not None else analysis.get('members') or [])
    steps = [Step(r['skill_id'], r['name'], PARTY, '', hotkeys.get(r['skill_id'], '')) for r in analysis.get('party', [])]
    for who in members:
        for r in analysis.get('target', []):
            steps.append(Step(r['skill_id'], r['name'], TARGET, who, hotkeys.get(r['skill_id'], '')))
    return Routine('Replay에서 만든 버프 루틴', steps)


def party_only(routine):
    """대상을 고르지 않아도 되는 구간(파티·자신 버프)만. 키 순서로 내보낼 때 쓴다."""
    return [s for s in routine.steps if s.scope == PARTY and s.hotkey]


def macro_steps(routine, gap_ms=600):
    """파티·자신 버프 구간을 기존 매크로 순서(core.macros 형식: tap/wait)로.

    **대상 지정 버프는 넣지 않는다** — 파티원을 자동으로 골라 버프를 주는 동작은 만들지 않는다.
    """
    steps = []
    for i, s in enumerate(party_only(routine)):
        if i:
            steps.append(dict(type='wait', ms=int(gap_ms)))
        steps.append(dict(type='tap', key=s.hotkey))
    return steps
