"""G HUB 매크로 분석: 매크로가 키를 누르는 간격을 서버가 알려 준 스킬 재사용 대기·실패와 나란히 본다(읽기만).

- 키 → 스킬바 칸: 게임 폴더 savedata/UserKeys.lua (사용자가 게임에서 정한 키). 1줄(F1~F9)은 바꾸지 않았으면 기본값.
- 칸 → 스킬: 리플레이의 단축키 목록 패킷 0x0b20(서버가 보낸 그 캐릭터의 스킬바).
- 스킬의 재사용 대기·'스킬 딜레이 중' 실패: 리플레이의 0x043d · 0x0110.
매크로를 바꾸거나 게임에 입력하지 않는다 — 고칠지는 사용자가 G HUB 에서 정한다.
"""
import os
import re
import struct
from collections import Counter, defaultdict

from analyzer.analyzers.tracking import DELAY_BLOCKED

USERKEYS = os.path.join('savedata', 'UserKeys.lua')
SLOTS = 38                                            # 스킬바 4줄 × 9칸 + 2
BURST_MS = 5000                                       # 이보다 오래 쉰 사이는 '연달아 쓴 간격'에 넣지 않는다
VK = {8: 'Backspace', 9: 'Tab', 13: 'Enter', 32: 'Space', 186: ';', 187: '=', 188: ',', 189: '-', 190: '.', 191: '/',
      192: '`', 219: '[', 220: '\\', 221: ']', 222: "'"}
VK.update({48 + i: str(i) for i in range(10)})
VK.update({65 + i: chr(65 + i) for i in range(26)})
VK.update({112 + i: 'F%d' % (i + 1) for i in range(12)})
MOD = {16: 'Shift', 17: 'Ctrl', 18: 'Alt'}


def slot_label(slot):
    return '스킬바 %d-%d' % (slot // 9 + 1, slot % 9 + 1) if slot < 36 else '스킬바 칸 %d' % (slot + 1)


def parse_userkeys(text):
    """UserKeys.lua 의 USERKEY_1(스킬바) → {칸 번호: 키 이름}. 1줄은 기본 F1~F9 로 채우고, 파일에 있으면 그 값."""
    keys = {i: 'F%d' % (i + 1) for i in range(9)}
    block = re.search(r'USERKEY_1\s*=\s*\{(.*?)\n\}', text or '', re.S)
    for slot, body in re.findall(r'\[(\d+)\]\s*=\s*\{([^}]*)\}', block.group(1) if block else ''):
        k1 = re.search(r'KEY1\s*=\s*(\d+)', body)
        k2 = re.search(r'KEY2\s*=\s*(\d+)', body)
        if not k1:
            continue
        name = VK.get(int(k1.group(1)), 'VK%s' % k1.group(1))
        if k2:
            name = '%s+%s' % (MOD.get(int(k2.group(1)), 'VK%s' % k2.group(1)), name)
        keys[int(slot)] = name
    return keys


def read_userkeys(ro_dir):
    path = os.path.join(ro_dir, USERKEYS)
    with open(path, 'rb') as f:
        return parse_userkeys(f.read().decode('cp949', 'replace'))


def hotbar(packets):
    """리플레이 패킷 [(시각, bytes)] → 마지막 0x0b20 의 {칸: dict(skill, id, level)}. 없으면 {}.
    0x0b20 (271): 머리.W 회전.B 탭.W + 칸 38개 × (스킬인가.B 번호.L 레벨·개수.W)."""
    last = None
    for _t, b in packets:
        if len(b) == 271 and struct.unpack_from('<H', b)[0] == 0x0b20:
            last = b
    if last is None:
        return {}
    out = {}
    for i in range(SLOTS):
        is_skill, ident, n = struct.unpack_from('<BIH', last, 5 + i * 7)
        if ident:
            out[i] = dict(skill=bool(is_skill), id=ident, level=n)
    return out


def key_presses(macro):
    """매크로 한 바퀴에서 키별로 몇 번 누르는지와 한 바퀴 시간(ms). 반복(누르고 있는 동안)이 아니면 cycle 은 None."""
    presses, total = Counter(), 0
    for s in macro.get('steps') or []:
        if s['type'] == 'wait':
            total += s['ms']
        elif s['type'] in ('tap', 'down'):
            presses[s['key']] += 1
    return presses, (total if macro.get('mode') == 'hold' and total else None)


def skill_stats(events, player):
    """{스킬: dict(uses, cooldown_ms, blocked, failures)} — 이 리플레이에서 내가 쓴 것."""
    uses, delays, fails = Counter(), defaultdict(Counter), defaultdict(Counter)
    last, gaps = {}, defaultdict(list)
    for e in events:
        if e.actor_id != player:
            continue
        if e.event_type == 'SKILL_USE':
            sid = e.data.get('skill')
            uses[sid] += 1
            if sid in last and e.timestamp_ms - last[sid] <= BURST_MS:      # 연달아 쓴 사이만(쉬는 시간 빼고)
                gaps[sid].append(e.timestamp_ms - last[sid])
            last[sid] = e.timestamp_ms
        elif e.event_type == 'SKILL_DELAY' and not e.data.get('carried'):
            delays[e.data['skill']][e.data['delay_ms']] += 1
        elif e.event_type == 'SKILL_FAIL':
            fails[e.data['skill']][e.data['cause']] += 1
    out = {}
    for sid in set(uses) | set(delays) | set(fails):
        g = sorted(gaps.get(sid) or [])
        out[sid] = dict(uses=uses.get(sid, 0), gap_ms=g[len(g) // 2] if len(g) >= 5 else None,
                        cooldown_ms=delays[sid].most_common(1)[0][0] if delays.get(sid) else None,
                        blocked=fails[sid].get(DELAY_BLOCKED, 0), failures=sum(fails[sid].values()))
    return out


def analyze(macro, keys, bar, stats, skill_name=str, item_name=None):
    """매크로 하나 → 키마다 dict(key, slot, what, interval_ms, cooldown_ms, uses, blocked, verdict)."""
    slot_of = {k: s for s, k in keys.items()}
    presses, cycle = key_presses(macro)
    rows = []
    for key, n in presses.items():
        slot = slot_of.get(key)
        cell = bar.get(slot) if slot is not None else None
        interval = round(cycle / n) if cycle else None
        row = dict(key=key, slot=slot_label(slot) if slot is not None else '', what='', interval_ms=interval,
                   cooldown_ms=None, uses=None, blocked=None, gap_ms=None, verdict='')
        if slot is None:
            row['verdict'] = '스킬바에 연결된 키가 아님(UserKeys.lua 에 없음)'
        elif cell is None:
            row['verdict'] = '이 리플레이의 스킬바에서 %s 칸이 비어 있음' % row['slot']
        elif not cell['skill']:
            row['what'] = (item_name and item_name(cell['id'])) or '아이템 %d' % cell['id']
            row['verdict'] = '아이템 칸 — 스킬 재사용 대기 비교 대상 아님'
        else:
            st = stats.get(cell['id']) or {}
            row.update(what='%s Lv.%d' % (skill_name(cell['id']), cell['level']), cooldown_ms=st.get('cooldown_ms'),
                       uses=st.get('uses'), blocked=st.get('blocked'), gap_ms=st.get('gap_ms'))
            row['verdict'] = verdict(interval, st)
        rows.append(row)
    return rows


def verdict(interval, st):
    """측정한 것만 말한다: 누름 간격(매크로) · 재사용 대기(서버) · 실제로 연달아 쓴 간격 · '스킬 딜레이 중' 실패."""
    cd, uses, blocked, gap = st.get('cooldown_ms'), st.get('uses') or 0, st.get('blocked') or 0, st.get('gap_ms')
    if not uses and not blocked:
        return '이 리플레이에서 이 스킬을 쓴 기록이 없음'
    parts = []
    if cd and interval and interval < cd:
        parts.append('%dms마다 누르지만 재사용 대기가 %dms라 그보다 자주 쓸 수 없음 — 누름 %d번에 1번꼴로만 쓰임'
                     % (interval, cd, -(-cd // interval)))
    elif cd and interval:
        parts.append('누름 간격이 재사용 대기(%dms) 이상' % cd)
    elif cd:
        parts.append('재사용 대기 %dms' % cd)
    else:
        parts.append('서버가 재사용 대기를 보내지 않는 스킬')
    if gap:
        parts.append('실제로 연달아 쓴 간격 %dms(중앙값)' % gap)
    if blocked:
        parts.append("'스킬 딜레이 중' 실패 %d번 / 사용 %d번 (%.1f%%)" % (blocked, uses, blocked * 100 / (blocked + uses)))
    return ' · '.join(parts)
