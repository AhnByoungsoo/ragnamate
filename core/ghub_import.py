"""로지텍 G HUB에서 만든 매크로(순서 매크로)를 읽어 라그메이트 매크로로 바꾼다. G HUB 설정은 읽기만 한다.

G HUB 는 %LOCALAPPDATA%\\LGHUB\\settings.db (SQLite, WAL) 의 data.file 에 설정 전체를 JSON 으로 둔다.
실행 중에는 최근 변경이 settings.db-wal 에 있으므로 두 파일을 임시 폴더에 함께 복사해서 연다.
"""
import json
import os
import shutil
import sqlite3
import tempfile

GHUB_DB = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'LGHUB', 'settings.db')

# USB HID 키보드 사용 코드 → 라그메이트 키 이름
HID_KEYS = {4 + i: chr(ord('A') + i) for i in range(26)}
HID_KEYS.update({30 + i: str(i + 1) for i in range(9)})
HID_KEYS.update({39: '0', 40: 'Enter', 41: 'Esc', 42: 'Backspace', 43: 'Tab', 44: 'Space', 45: '-', 46: '=',
                 54: ',', 55: '.', 73: 'Insert', 74: 'Home', 75: 'PageUp', 76: 'Delete', 77: 'End', 78: 'PageDown',
                 79: 'Right', 80: 'Left', 81: 'Down', 82: 'Up', 98: 'Num0',
                 224: 'Ctrl', 225: 'Shift', 226: 'Alt', 228: 'Ctrl', 229: 'Shift', 230: 'Alt'})
HID_KEYS.update({58 + i: 'F%d' % (i + 1) for i in range(12)})
HID_KEYS.update({89 + i: 'Num%d' % (i + 1) for i in range(9)})
MOUSE = {1: 'click', 2: 'rclick'}
# G HUB 순서 종류 → 라그메이트 동작. toggle(켜 두면 계속 반복)은 가져오지 않는다.
KINDS = [('heldSequence', 'hold', '누르고 있는 동안'), ('simpleSequence', 'once', '한 번'),
         ('pressSequence', 'once', '누를 때 한 번'), ('releaseSequence', 'once', '뗄 때 한 번')]


def read_settings(path=GHUB_DB):
    """settings.db(+wal) 를 임시 폴더에 복사해 마지막 설정 JSON 을 읽는다."""
    if not os.path.isfile(path):
        raise FileNotFoundError('G HUB 설정 파일이 없습니다: %s' % path)
    tmp = tempfile.mkdtemp(prefix='ghub_')
    try:
        for suffix in ('', '-wal'):
            if os.path.isfile(path + suffix):
                shutil.copy2(path + suffix, os.path.join(tmp, 'settings.db' + suffix))
        conn = sqlite3.connect(os.path.join(tmp, 'settings.db'))
        try:
            row = conn.execute('SELECT file FROM data ORDER BY _id DESC LIMIT 1').fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def convert_components(components):
    """G HUB 순서 → (라그메이트 순서, 건너뛴 것 설명들).
    바로 뒤(대기 하나 사이)에 같은 키를 떼는 '누름'은 '누르고 떼기'로 합치고 대기는 그대로 둔다(다음 키 시각은 같음)."""
    items, skipped = [], []
    for c in components:
        if 'delay' in c:
            items.append(('wait', int(c['delay'].get('durationMs', 0))))
        elif 'keyboard' in c:
            k = HID_KEYS.get(int(c['keyboard'].get('hidUsage', -1)))
            if k is None:
                skipped.append('키 %s' % c['keyboard'].get('displayName', c['keyboard'].get('hidUsage')))
                continue
            items.append(('key', k, bool(c['keyboard'].get('isDown'))))
        elif 'mouse' in c and 'button' in c['mouse']:
            b = MOUSE.get(int(c['mouse']['button'].get('hidUsage', -1)))
            if b is None:
                skipped.append('마우스 버튼 %s' % c['mouse']['button'].get('hidUsage'))
                continue
            items.append(('mouse', b, bool(c['mouse']['button'].get('isDown'))))
        else:
            skipped.append(next(iter(c), '알 수 없는 동작'))
    steps, i = [], 0
    while i < len(items):
        it = items[i]
        if it[0] == 'wait':
            if it[1] > 0:
                steps.append(dict(type='wait', ms=it[1]))
            i += 1
            continue
        kind, name, down = it
        # 누름 [대기] 뗌 → 누르고 떼기 + 대기
        j = i + 1
        wait = 0
        if j < len(items) and items[j][0] == 'wait':
            wait, j = items[j][1], j + 1
        if down and j < len(items) and items[j][:2] == (kind, name) and not items[j][2]:
            steps.append(dict(type='tap', key=name) if kind == 'key' else dict(type=name))
            if wait > 0:
                steps.append(dict(type='wait', ms=wait))
            i = j + 1
            continue
        if kind == 'key':
            steps.append(dict(type='down' if down else 'up', key=name))
        elif down:                                       # 짝이 없는 마우스 누름은 클릭으로
            steps.append(dict(type=name))
        i += 1
    merged = []                                          # 이어진 대기는 하나로 (25 + 25 → 50ms)
    for s in steps:
        if s['type'] == 'wait' and merged and merged[-1]['type'] == 'wait':
            merged[-1] = dict(type='wait', ms=merged[-1]['ms'] + s['ms'])
        else:
            merged.append(s)
    return merged, skipped


def ghub_macros(settings):
    """G HUB 설정 JSON → [{name, profile, kind_label, mode, steps, skipped, importable, reason}]"""
    apps = {a.get('applicationId'): a.get('name') for a in (settings.get('applications') or {}).get('applications', [])}
    out = []
    for card in (settings.get('cards') or {}).get('cards', []):
        mac = card.get('macro') or {}
        if mac.get('type') != 'SEQUENCE' or card.get('readOnly'):
            continue
        seq = mac.get('sequence') or {}
        profile = apps.get(card.get('applicationId'), '')
        profile = '데스크톱' if profile == 'APPLICATION_NAME_DESKTOP' else (profile or '공통')
        base = dict(name=card.get('name') or '(이름 없음)', profile=profile)
        if (seq.get('toggleSequence') or {}).get('components'):
            out.append(dict(base, kind_label='토글', mode=None, steps=[], skipped=[], importable=False,
                            reason='토글(켜 두면 계속 반복)은 가져오지 않습니다'))
            continue
        for key, mode, label in KINDS:
            comps = (seq.get(key) or {}).get('components')
            if comps:
                steps, skipped = convert_components(comps)
                out.append(dict(base, kind_label=label, mode=mode, steps=steps, skipped=skipped,
                                importable=bool(steps), reason='' if steps else '바꿀 수 있는 동작이 없음'))
                break
    return out


def to_macro(g, key='F12'):
    """가져온 항목 → 라그메이트 매크로. 트리거 키는 사용자가 정하도록 꺼 둔 채로."""
    return dict(name=g['name'], key=key, mod='', mode=g['mode'], gap=30, enabled=False, steps=g['steps'],
                source='G HUB')
