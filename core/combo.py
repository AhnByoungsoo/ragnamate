"""리플레이로 연격 콤보 매크로 만들기.

- 스킬 사이 간격: 내가 실제로 스킬을 시작한 시각(SKILL_CAST_START)의 간격을 재서, 빠른 쪽(기본 하위 20%) 값을 쓴다.
  관측 간격과 서버 후딜레이·캐스팅·쿨타임, 공격 모션 참고값으로 등록 초안을 만든다.
- 스킬 → 키: 리플레이의 단축키 창 패킷(0x0B20: 38칸 × 스킬/아이템)에서 칸 위치를 읽고, 줄마다 키를 붙인다.
  1줄 F1~F9 는 게임 기본값, 2줄 이후는 키 설정에 따라 다르므로 화면에서 바꿀 수 있게 한다.
"""
import bisect
import copy
import hashlib
import json
import struct
from collections import Counter, defaultdict

from analyzer.events import EventType as E

HOTKEY_PACKET, HOTKEY_LEN, HOTKEY_SLOTS = 0x0B20, 271, 38
# 줄별 키 (9칸). 2줄 Q~O 는 천라지망이 2줄 2칸 = W 로 확인됨(사용자 설명). 3·4줄은 게임 키 설정 기본 배치 추정.
ROW_KEYS = [['F%d' % i for i in range(1, 10)], list('QWERTYUIO'), list('ASDFGHJKL'), list('ZXCVBNM') + [',', '.']]
CHAIN_GAP_MS = 1500          # 이보다 오래 쉬면 콤보가 끊긴 것으로 본다
DEDUPE_MS = 30               # 같은 스킬 시작이 이 안에 두 번 기록되면 한 번으로
MIN_WAIT_MS = 50


def hotkey_bar(packets):
    """[(time, bytes)] → {칸 번호: (종류 1=스킬/0=아이템, id, 레벨/개수)} — 마지막으로 받은 0번 탭."""
    bar = None
    for _t, b in packets:
        if len(b) == HOTKEY_LEN and struct.unpack_from('<H', b)[0] == HOTKEY_PACKET:
            _rotate, tab = struct.unpack_from('<BH', b, 2)
            if tab == 0:
                bar = {i: struct.unpack_from('<BIH', b, 5 + 7 * i) for i in range(HOTKEY_SLOTS)}
    return {i: v for i, v in (bar or {}).items() if v[1]}


def slot_key(slot, row_keys=ROW_KEYS):
    row, col = divmod(slot, 9)
    return row_keys[row][col] if row < len(row_keys) and col < len(row_keys[row]) else None


def skill_keys(bar, row_keys=ROW_KEYS):
    """스킬 id → (키, 칸 번호). 같은 스킬이 여러 칸이면 앞 칸."""
    out = {}
    for slot in sorted(bar):
        kind, sid, _lv = bar[slot]
        if kind == 1 and sid not in out:
            out[sid] = (slot_key(slot, row_keys), slot)
    return out


def merge_keys(saved, bar):
    """칸 위치로 추정한 키보다 사용자가 저장한 키를 우선한다."""
    return {**{sid: key for sid, (key, _slot) in skill_keys(bar).items() if key}, **saved}


def casts(events, player):
    """내가 시작한 스킬 [(ms, skill id)] — 같은 스킬 중복 기록은 합친다."""
    out = []
    for e in sorted(events, key=lambda e: e.timestamp_ms):
        if e.event_type == E.SKILL_CAST_START and e.actor_id == player and e.data.get('skill') is not None:
            sid = e.data['skill']
            if out and out[-1][1] == sid and e.timestamp_ms - out[-1][0] < DEDUPE_MS:
                continue
            out.append((e.timestamp_ms, sid))
    return out


def chains(cs, gap=CHAIN_GAP_MS):
    groups, cur = [], []
    for t, sid in cs:
        if cur and t - cur[-1][0] > gap:
            groups.append(cur)
            cur = []
        cur.append((t, sid))
    if cur:
        groups.append(cur)
    return groups


def intervals(cs, gap=CHAIN_GAP_MS):
    """(앞 스킬, 다음 스킬) → 간격 목록(ms), 콤보가 이어진 경우만."""
    out = defaultdict(list)
    for g in chains(cs, gap):
        for (t1, a), (t2, b) in zip(g, g[1:]):
            out[(a, b)].append(t2 - t1)
    return out


def percentile(values, q):
    v = sorted(values)
    if not v:
        return None
    i = (len(v) - 1) * q
    lo = int(i)
    return v[lo] + (v[min(lo + 1, len(v) - 1)] - v[lo]) * (i - lo)


def suggest(cs, max_len=5, top=8, gap=CHAIN_GAP_MS):
    """실제로 관측된 스킬 순서 후보. count = 콤보가 이어진 구간 안에서 그 순서가 나온 횟수.
    같은 순서의 반복(A A A…)은 짧은 쪽(A)으로 세고, 순위는 횟수 × 길이(콤보를 더 많이 덮는 순서가 위)."""
    counts = Counter()
    for g in chains(cs, gap):
        ids = [sid for _t, sid in g]
        for n in range(1, max_len + 1):
            for i in range(len(ids) - n + 1):
                seq = ids[i:i + n]
                if any(n % p == 0 and seq == seq[:p] * (n // p) for p in range(1, n)):
                    continue                                  # 더 짧은 순서의 반복이면 그쪽에서 센다
                counts[tuple(seq)] += 1  # 관측하지 않은 회전 순서를 만들어 내지 않는다
    ranked = sorted(counts.items(), key=lambda kv: (-(kv[1] * len(kv[0])), len(kv[0])))
    out, seen = [], set()
    for seq, c in ranked:
        if c < 2 or seq in seen:
            continue
        seen.add(seq)
        out.append(dict(skills=list(seq), count=c))
        if len(out) >= top:
            break
    return out


def plan(sequence, stats, q=0.2, extra_ms=0):
    """순서(스킬 id) → 칸마다 다음 스킬까지 기다릴 시간. 마지막은 처음 스킬로 이어지는 간격(반복 시)."""
    rows = []
    for i, sid in enumerate(sequence):
        nxt = sequence[(i + 1) % len(sequence)]
        vals = stats.get((sid, nxt), [])
        fast, med = percentile(vals, q), percentile(vals, 0.5)
        wait = max(MIN_WAIT_MS, int(round(fast)) + extra_ms) if fast is not None else None
        rows.append(dict(skill=sid, next=nxt, samples=len(vals), fast=fast, median=med, wait=wait))
    return rows


# ---------------------------------------------------------------- 내 공격속도·후딜레이 (리플레이의 서버 값)
VAR_ASPD = 53                # ZC_PAR_CHANGE 변수 53: 서버가 보내는 값 = 공격 모션(ms). ASPD = 200 - 값/10 (rAthena 구현)
EFST_POSTDELAY = 46          # 후딜레이 상태. 시작/캐스팅 완료 인접 기록으로 스킬 연관을 추정한다.
PAR_CHANGE = 0x00B0
SKILL_COOLDOWN = 0x043D      # ZC_SKILL_POSTDELAY: 스킬 id(2) + 쿨타임 ms(4) — 스킬별 재사용 대기


def cooldowns(packets, q=0.5):
    """스킬 id → 서버가 알려 준 쿨타임(ms, 중앙값)."""
    out = defaultdict(list)
    for _t, b in packets:
        if len(b) >= 8 and struct.unpack_from('<H', b)[0] == SKILL_COOLDOWN:
            sid, ms = struct.unpack_from('<HI', b, 2)
            out[sid].append(ms)
    return {sid: int(percentile(v, q)) for sid, v in out.items()}


def cast_times(events, player, q=0.5):
    """스킬 id → 관측 캐스팅 시간(ms, 분위수). 0과 미관측을 구분한다."""
    out = defaultdict(list)
    for e in events:
        if e.event_type == E.SKILL_CAST_START and e.actor_id == player and e.data.get('skill') is not None:
            if e.data.get('cast_ms') is not None and e.data['cast_ms'] >= 0:
                out[e.data['skill']].append(e.data['cast_ms'])
    return {sid: int(percentile(v, q)) for sid, v in out.items()}


def amotion_timeline(packets):
    """[(ms, 공격 모션 ms)] — 장비·버프가 바뀔 때마다 서버가 다시 보낸다."""
    out = []
    for t, b in packets:
        if len(b) >= 8 and struct.unpack_from('<H', b)[0] == PAR_CHANGE:
            var, val = struct.unpack_from('<Hi', b, 2)
            if var == VAR_ASPD and val > 0:
                out.append((t, val))
    return sorted(out, key=lambda x: x[0])


def value_at(timeline, t):
    i = bisect.bisect_right([x for x, _ in timeline], t) - 1
    return timeline[i][1] if i >= 0 else None


def postdelays(events, player, window_ms=400):
    """스킬 id → [서버 후딜레이 ms]. 내가 시작한 스킬 바로 뒤(window 안)에 온 후딜레이 상태를 짝짓는다."""
    out, last = defaultdict(list), None
    for e in sorted(events, key=lambda e: e.timestamp_ms):
        if e.event_type == E.MAP_CHANGE:
            last = None
        if e.actor_id != player:
            continue
        if e.event_type == E.SKILL_CAST_START and e.data.get('skill') is not None:
            last = e
        elif (e.event_type == E.BUFF_START and e.data.get('efst') == EFST_POSTDELAY and last is not None
              and e.map_name == last.map_name
              and 0 <= e.timestamp_ms - last.timestamp_ms - (last.data.get('cast_ms') or 0) <= window_ms
              and e.data.get('remain_ms') is not None and e.data['remain_ms'] >= 0):
            out[last.data['skill']].append(e.data['remain_ms'])
            last = None
    return out


def character_profile(parsed):
    """리플레이 → 콤보 계산에 쓰는 내 캐릭터 값. 없으면 None 칸."""
    cs = casts(parsed.events, parsed.player_aid)
    origin = parsed.packets[0][0] if parsed.packets else 0
    tl = [(t - origin, m) for t, m in amotion_timeline(parsed.packets)]
    motions = [m for m in (value_at(tl, t) for t, _ in cs) if m]
    amotion = int(percentile(motions, 0.5)) if motions else (tl[-1][1] if tl and not cs else None)
    pd_samples = postdelays(parsed.events, parsed.player_aid)
    pd = {sid: max(v) for sid, v in pd_samples.items() if v}
    per_skill = defaultdict(list)
    for t, sid in cs:
        motion = value_at(tl, t)
        if motion is not None:
            per_skill[sid].append(motion * 2)
    return dict(casts=cs, stats=intervals(cs), amotion=amotion,
                aspd=round(200 - amotion / 10, 1) if amotion else None,
                motion_ms=2 * amotion if amotion else None, postdelay=pd,
                motion_by_skill={sid: int(percentile(v, .9)) for sid, v in per_skill.items()},
                motion_range=[min(motions), max(motions)] if motions else [],
                postdelay_samples={sid: len(v) for sid, v in pd_samples.items()},
                cooldown=cooldowns(parsed.packets, q=1), cast=cast_times(parsed.events, parsed.player_aid, q=1),
                character=getattr(parsed, 'character', ''), server=getattr(parsed, 'server', ''),
                base_level=getattr(parsed, 'base_level', None), job_id=getattr(parsed, 'job_id', None),
                job_level=getattr(parsed, 'job_level', None),
                warnings=['공격 모션×2는 참고 추정값이며 한국 서버의 스킬별 최소 간격을 보장하지 않습니다.',
                          '스킬 시작 기록만으로 시전 성공률·명중률은 판정할 수 없습니다.'])


def plan_auto(sequence, stats, prof, margin_ms=10):
    """최적화 초안: max(관측 하위20%, 캐스팅+max(후딜, 모션 참고값))+여유.
    모션×2는 가정이다. 쿨타임·캐스팅은 최대 관측값으로 제약을 적용한다.
    응답 간격에는 네트워크 지연이 섞여 있으므로 게임 내 최적값·성공률을 보장하지 않는다."""
    if margin_ms < 0:
        raise ValueError('자동 계산 여유는 음수가 될 수 없습니다.')
    rows = []
    for i, sid in enumerate(sequence):
        nxt = sequence[(i + 1) % len(sequence)]
        vals = stats.get((sid, nxt), [])
        acd = prof['postdelay'].get(sid)
        motion = prof.get('motion_by_skill', {}).get(sid, prof.get('motion_ms'))
        cast = (prof.get('cast') or {}).get(sid)
        known = [x for x in (acd, motion) if x is not None]
        theory = max(known) if known else None
        fast = percentile(vals, 0.2)
        if theory is not None:
            base, basis = (cast or 0) + theory, '후딜레이' if (acd or 0) >= (motion or 0) else '공격 모션 추정'
            if cast:
                basis = '캐스팅+' + basis
        elif fast is not None:
            base, basis = fast, '실측'
        else:
            base, basis = None, '자료 없음'
        if fast is not None and (base is None or fast > base):
            base, basis = fast, '실측 하위 20%'
        rows.append(dict(skill=sid, next=nxt, samples=len(vals), fast=fast, median=percentile(vals, 0.5),
                acd=acd, motion=motion, cast=cast, cooldown=(prof.get('cooldown') or {}).get(sid),
                         basis=basis,
                         wait=max(MIN_WAIT_MS, int(round(base)) + margin_ms) if base is not None else None))
    _fit_cooldowns(rows, margin_ms)
    return rows


def _fit_cooldowns(rows, margin_ms):
    """같은 스킬이 다시 나오기까지(한 바퀴 돌아 자기 자신까지 포함) 대기 합이 쿨타임보다 짧으면 부족한 만큼 늘린다."""
    n = len(rows)
    for _ in range(n + 1):                                   # 늘리면 다른 스킬 조건도 만족될 수 있어 몇 번 반복
        changed = False
        for i, r in enumerate(rows):
            if not r['cooldown'] or any(x['wait'] is None for x in rows):
                continue
            j = next(k for k in range(1, n + 1) if rows[(i + k) % n]['skill'] == r['skill'])   # 다음 같은 스킬
            span = sum(rows[(i + k) % n]['wait'] for k in range(j))
            need = r['cooldown'] + (r.get('cast') or 0) + margin_ms - span
            if need > 0:
                # 한 바퀴를 넘어가면 마지막 줄(처음으로 돌아가기 전) 대기를 늘려 연격 중간은 빠르게 둔다.
                # 한 바퀴 안에서 같은 스킬을 다시 쓰는 경우만 그 직전 줄을 늘린다.
                before = rows[n - 1] if i + j >= n else rows[i + j - 1]
                before['wait'] += need
                if '쿨타임' not in before['basis']:
                    before['basis'] += ' + 쿨타임'
                if r['skill'] not in before.setdefault('cooldown_for', []):   # 화면에 '어느 스킬 쿨타임 때문' 표시용
                    before['cooldown_for'].append(r['skill'])
                changed = True
        if not changed:
            break


def build_steps(rows, key_of, fallback_ms=300):
    """plan() 결과 + 스킬→키 → 매크로 순서. 측정이 없는 칸은 fallback_ms(화면에 표시)."""
    steps = []
    for r in rows:
        steps.append(dict(type='tap', key=key_of[r['skill']]))
        steps.append(dict(type='wait', ms=r['wait'] if r['wait'] is not None else fallback_ms))
    return steps


def make_macro(parsed, rows, key_of, name, trigger, mod='', mode='hold', basis='auto', margin_ms=10):
    """분석 근거까지 저장할 수 있는 등록 결과. 미측정/범위 초과 간격은 자동 보정하지 않는다."""
    from core import macros as M
    if not rows:
        raise ValueError('등록할 스킬이 없습니다.')
    if any(r.get('wait') is None or not MIN_WAIT_MS <= r['wait'] <= 60000 for r in rows):
        raise ValueError('모든 대기를 50~60000ms 범위에서 확인하세요. 자료 없음은 직접 지정해야 합니다.')
    if any(key_of.get(r['skill']) not in M.KEYS for r in rows):
        raise ValueError('스킬 키를 확인하세요.')
    identity = [parsed.server, parsed.character, parsed.job_id, [r['skill'] for r in rows]]
    macro = dict(M.new_macro(name), key=trigger, mod=mod, mode=mode, steps=build_steps(rows, key_of))
    macro['replay_combo'] = dict(version=2, identity=identity,
        id=hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20],
        replay=parsed.path, recorded_at=str(parsed.recorded_at), base_level=parsed.base_level,
        basis=basis, margin_ms=margin_ms, rows=copy.deepcopy(rows),
        profile={k: v for k, v in character_profile(parsed).items() if k not in ('casts', 'stats')},
        game_input_verified=False)
    problems = M.validate([macro])
    if problems:
        raise ValueError(' / '.join(problems))
    return macro


def register_macro(macros, macro):
    """같은 출처/캐릭터/직업/순서는 갱신. 다른 트리거 충돌은 신규 항목을 꺼서 보존."""
    out = copy.deepcopy(macros)
    source = macro['replay_combo']
    same = [i for i, old in enumerate(out) if old.get('replay_combo', {}).get('id') == source['id']]
    if not same:
        # 이전 UI가 만든, 이름과 키 순서까지 같은 연격만 마이그레이션한다.
        compact = lambda s: ''.join(s.split())
        taps = lambda m: [s.get('key') for s in m.get('steps', []) if s.get('type') == 'tap']
        same = [i for i, old in enumerate(out) if not old.get('replay_combo')
                and compact(old.get('name', '')) == compact(macro['name'])
                and taps(old) == taps(macro) and old.get('key') == macro['key']
                and old.get('mod', '') == macro.get('mod', '')]
    index = same[0] if same else len(out)
    for i in same[1:]:
        out[i]['enabled'] = False
        out[i]['superseded_by'] = source['id']
    m = copy.deepcopy(macro)
    if any(i != index and old.get('enabled', True)
           and (old.get('key'), old.get('mod', '')) == (m['key'], m.get('mod', ''))
           for i, old in enumerate(out)):
        m['enabled'] = False
    if same:
        out[index] = m
    else:
        out.append(m)
    return out, index
