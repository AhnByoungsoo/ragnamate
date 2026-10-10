"""라그메이트 자체 키 매크로.

- 트리거 키를 저수준 키보드 훅(WH_KEYBOARD_LL)으로 '지켜보기만' 한다 — 키를 가로채지 않으므로 W를 누르면 게임에도 W가 그대로 간다.
- 실제로 키보드에서 누른 입력만 트리거로 본다(매크로가 보낸 입력은 LLKHF_INJECTED 라 무시) — W 누르는 동안 W 난타가 가능.
- 순서는 SendInput 스캔 코드(키보드와 같은 하드웨어 코드)로 보낸다.
- 사람이 트리거 키를 누를 때만 동작: '반복'은 누르고 있는 동안만, 떼면 멈춘다. 켜 두면 혼자 도는 반복·무작위 지연은 없다.
주의: 2026-09-25 시험에서 라그나로크는 SendInput 입력에 반응하지 않았다. 켠 뒤 게임에서 실제로 동작하는지 확인해야 한다.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

log = logging.getLogger(__name__)

# 표시 이름 → Windows 가상 키
KEYS = {}
for _c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    KEYS[_c] = ord(_c)
for _d in '0123456789':
    KEYS[_d] = ord(_d)
for _i in range(1, 13):
    KEYS['F%d' % _i] = 0x6F + _i
for _i in range(10):
    KEYS['Num%d' % _i] = 0x60 + _i
KEYS.update({'Space': 0x20, 'Enter': 0x0D, 'Esc': 0x1B, 'Tab': 0x09, 'Backspace': 0x08, 'Shift': 0xA0, 'Ctrl': 0xA2,
             'Alt': 0xA4, 'Insert': 0x2D, 'Delete': 0x2E, 'Home': 0x24, 'End': 0x23, 'PageUp': 0x21, 'PageDown': 0x22,
             'Up': 0x26, 'Down': 0x28, 'Left': 0x25, 'Right': 0x27, '-': 0xBD, '=': 0xBB, ',': 0xBC, '.': 0xBE})
KEY_NAMES = list(KEYS)
EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27}
_VK_TO_NAME = {vk: name for name, vk in KEYS.items()}
_VK_TO_NAME.update({0x10: 'Shift', 0xA1: 'Shift', 0x11: 'Ctrl', 0xA3: 'Ctrl', 0x12: 'Alt', 0xA5: 'Alt'})

def vk_name(vk):
    """Windows 가상 키 → 키 이름 (모르면 None)."""
    return _VK_TO_NAME.get(vk)


STEP_TYPES = {'tap': '키 누르고 떼기', 'down': '키 누른 채로', 'up': '키 떼기', 'wait': '대기(ms)',
              'click': '마우스 왼쪽 클릭', 'rclick': '마우스 오른쪽 클릭', 'move': '마우스 좌표로 이동'}
MODES = {'hold': '누르고 있는 동안 반복', 'once': '누를 때 한 번'}
MODIFIERS = {'': '없음', 'ctrl': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift'}
MIN_GAP_MS = 30            # 반복 사이 최소 간격


def new_macro(name='새 매크로'):
    return dict(name=name, key='W', mod='', mode='hold', gap=30, enabled=True, steps=[dict(type='tap', key='W')])


TEMPLATES = {
    '누르는 동안 같은 키 난타': [dict(type='tap', key='W')],
    '스킬 → 대상 클릭': [dict(type='tap', key='F1'), dict(type='wait', ms=50), dict(type='click')],
    '버프 3개 연속': [dict(type='tap', key='F1'), dict(type='wait', ms=300), dict(type='tap', key='F2'),
                   dict(type='wait', ms=300), dict(type='tap', key='F3')],
}


def migrate(m):
    """예전(G HUB 연계 시절) 형식을 새 형식으로. 새 형식이면 그대로."""
    if 'key' in m:
        return m
    parts = [p.strip() for p in (m.get('hotkey') or '').split('+') if p.strip()] if m.get('engine') == 'app' else []
    key = parts[-1].upper() if parts else 'F12'
    key = next((k for k in KEYS if k.upper() == key), 'F12')
    mod = next((p.lower() for p in parts[:-1] if p.lower() in MODIFIERS), '')
    out = {k: v for k, v in m.items() if k not in ('engine', 'button', 'modifier', 'hotkey')}
    return dict(out, key=key, mod=mod, mode=m.get('mode', 'once'), gap=m.get('gap', 100))


def step_text(s):
    t = s.get('type')
    if t in ('tap', 'down', 'up'):
        return '⌨  %s  [%s]' % (STEP_TYPES[t], s.get('key', '?'))
    if t == 'wait':
        return '⏱  대기  %d ms' % int(s.get('ms', 0))
    label = '🖱  ' + STEP_TYPES.get(t, t)
    if s.get('position', 'current') != 'current':
        label += ' [%s: %s, %s]' % ('게임' if s['position'] == 'game' else '화면', s.get('x'), s.get('y'))
    return label


def trigger_text(m):
    return (MODIFIERS[m['mod']] + ' + ' if m.get('mod') else '') + m.get('key', '?')


def total_ms(m):
    return sum(int(s.get('ms', 0)) for s in m.get('steps', []) if s.get('type') == 'wait')


def validate(macros):
    """문제 목록(한국어). 비어 있으면 켤 수 있다."""
    problems, seen = [], {}
    for m in macros:
        if not m.get('enabled', True):
            continue
        name = m.get('name') or '(이름 없음)'
        if not m.get('steps'):
            problems.append('%s: 순서가 비어 있음' % name)
        if m.get('key') not in KEYS:
            problems.append('%s: 트리거 키 %r 을(를) 모름' % (name, m.get('key')))
        for s in m.get('steps', []):
            if s.get('type') in ('move', 'click', 'rclick'):
                from core.mouse_coords import validate_position
                problem = validate_position(s)
                if problem: problems.append('%s: %s' % (name, problem))
            if s.get('type') in ('tap', 'down', 'up') and s.get('key') not in KEYS:
                problems.append('%s: 알 수 없는 키 %r' % (name, s.get('key')))
            if s.get('type') not in STEP_TYPES:
                problems.append('%s: 알 수 없는 단계' % name)
            if s.get('type') == 'wait':
                try:
                    valid = 0 <= int(s.get('ms', 0)) <= 60000
                except (ValueError, TypeError, OverflowError):
                    valid = False
                if not valid:
                    problems.append('%s: 대기는 0~60000ms' % name)
        if m.get('mode') not in MODES or m.get('mod', '') not in MODIFIERS:
            problems.append('%s: 실행 방식 또는 조합 키를 확인하세요' % name)
        trig = (m.get('key'), m.get('mod', ''))
        if trig in seen:
            problems.append('%s: "%s"와 같은 트리거 키' % (name, seen[trig]))
        seen[trig] = name
    return problems


def match(macros, key, mods):
    """눌린 키(이름)와 지금 눌린 조합키 집합 → 실행할 매크로(없으면 None). 조합 없는 매크로는 조합키가 없을 때만."""
    for m in macros:
        if m.get('enabled', True) and m.get('key') == key:
            want = m.get('mod', '')
            if mods == ({want} if want else set()):
                return m
    return None


# ---------------------------------------------------------------- 입력 보내기 (SendInput 스캔 코드)

class _KI(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class _MI(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [('ki', _KI), ('mi', _MI)]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _U)]


def _send(inp):
    arr = (_INPUT * 1)(inp)
    fn = ctypes.windll.user32.SendInput
    fn.argtypes = (wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    fn.restype = wt.UINT
    ctypes.windll.kernel32.SetLastError(0)
    if fn(1, arr, ctypes.sizeof(_INPUT)) != 1:
        raise OSError('SendInput 입력 삽입 실패 (Windows 오류 %d). 권한 차이 또는 입력 제한을 확인하세요.' %
                      ctypes.windll.kernel32.GetLastError())


def _key(vk, up):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = 0x8 | (0x2 if up else 0) | (0x1 if vk in EXTENDED else 0)     # SCANCODE | KEYUP | EXTENDEDKEY
    _send(_INPUT(1, _U(ki=_KI(0, scan, flags, 0, 0))))


def _click(right):
    down, up = (0x8, 0x10) if right else (0x2, 0x4)
    _send(_INPUT(0, _U(mi=_MI(0, 0, 0, down, 0, 0))))
    _send(_INPUT(0, _U(mi=_MI(0, 0, 0, up, 0, 0))))


def _move_to(x, y):
    from core.mouse_coords import absolute_position
    metric = ctypes.windll.user32.GetSystemMetrics
    dx, dy = absolute_position(x, y, tuple(metric(i) for i in (76, 77, 78, 79)))
    _send(_INPUT(0, _U(mi=_MI(dx, dy, 0, 0x0001 | 0x8000 | 0x4000, 0, 0))))


def run_steps(steps, stop=None, key=_key, click=_click, sleep=time.sleep, move=_move_to, resolve=None):
    """순서 한 번 실행. 누른 채로 끝난 키는 마지막에 뗀다. stop 이벤트가 켜지면 즉시 멈춘다."""
    down = set()
    try:
        for s in steps:
            if stop is not None and stop.is_set():
                break
            t = s['type']
            if t == 'tap':
                key(KEYS[s['key']], False)
                down.add(s['key'])
                key(KEYS[s['key']], True)
                down.discard(s['key'])
            elif t == 'down':
                key(KEYS[s['key']], False)
                down.add(s['key'])
            elif t == 'up':
                key(KEYS[s['key']], True)
                down.discard(s['key'])
            elif t == 'wait':
                delay = int(s.get('ms', 0)) / 1000
                if stop is not None and sleep is time.sleep:
                    if stop.wait(delay):
                        break
                else:
                    sleep(delay)
            elif t in ('click', 'rclick', 'move'):
                from core.mouse_coords import resolve_position
                position = (resolve or resolve_position)(s)
                if position is not None: move(*position)
                if t != 'move' and not (stop is not None and stop.is_set()): click(t == 'rclick')
    finally:
        for k in down:
            key(KEYS[k], True)


# ---------------------------------------------------------------- 트리거 감시 (저수준 키보드 훅, 가로채지 않음)

WH_KEYBOARD_LL, LLKHF_INJECTED = 13, 0x10
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_QUIT = 0x100, 0x101, 0x104, 0x105, 0x12


class _KBD(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class _RunStop:
    """키 해제·창 전환을 대기 중에도 확인하고 취소 상태를 유지한다."""
    def __init__(self, engine, macro):
        self.engine, self.macro = engine, macro
        self.cancelled = threading.Event()

    def is_set(self):
        e, m = self.engine, self.macro
        released = m.get('mode') == 'hold' and (m['key'] not in e.held or
                   (m.get('mod') and m['mod'].title() not in e.held))
        if e.stop_event.is_set() or released or (e.game_only and not e.is_game_foreground()):
            self.cancelled.set()
        return self.cancelled.is_set()

    def wait(self, timeout):
        end = time.monotonic() + timeout
        while not self.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            self.cancelled.wait(min(.01, remaining))
        return True


class MacroEngine:
    def __init__(self, macros, game_only=True, on_status=None, is_game_foreground=None, runner=run_steps):
        self.macros = [m for m in macros if m.get('enabled', True) and m.get('steps')]
        self.runner = runner
        self.game_only = game_only
        self.on_status = on_status or (lambda text: None)
        self.is_game_foreground = is_game_foreground or _game_foreground
        self.held = set()                 # 실제로 누르고 있는 키 이름
        self.running = {}                 # 매크로 이름 → 실행 스레드
        self.controls = {}
        self.stop_event = threading.Event()
        self.error = ''
        self._tid = None
        self._proc = _HOOKPROC(self._hook)          # 가비지 수집되지 않게 붙잡아 둔다
        self._thread = threading.Thread(target=self._loop, name='MacroHook', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _loop(self):
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.SetWindowsHookExW.restype = wt.HHOOK
        u32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, wt.HINSTANCE, wt.DWORD)
        u32.CallNextHookEx.argtypes = (wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        u32.CallNextHookEx.restype = ctypes.c_ssize_t
        u32.UnhookWindowsHookEx.argtypes = (wt.HHOOK,)
        k32.GetModuleHandleW.restype = wt.HMODULE
        self._tid = k32.GetCurrentThreadId()
        hook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, k32.GetModuleHandleW(None), 0)
        if not hook:
            self.error = '키보드 감시를 시작하지 못했습니다 (오류 %d)' % k32.GetLastError()
            return
        self._hook_handle = hook
        msg = wt.MSG()
        # 메시지 큐를 먼저 만든 뒤 정지 여부를 확인한다(시작 직후 끄기 경쟁 방지).
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        while not self.stop_event.is_set() and u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        u32.UnhookWindowsHookEx(hook)

    def _hook(self, code, wparam, lparam):
        try:
            if code == 0:
                k = ctypes.cast(lparam, ctypes.POINTER(_KBD)).contents
                if not k.flags & LLKHF_INJECTED:            # 매크로가 보낸 키는 무시
                    self.on_key(_VK_TO_NAME.get(k.vkCode), wparam in (WM_KEYDOWN, WM_SYSKEYDOWN))
        except Exception:
            log.exception('키보드 감시 오류')
        return ctypes.windll.user32.CallNextHookEx(None, code, wparam, lparam)   # 항상 그대로 넘긴다

    def on_key(self, name, down):
        """훅 스레드에서 호출: 여기서는 표시만 하고 실제 실행은 별도 스레드."""
        if not name:
            return
        if not down:
            self.held.discard(name)
            for control in list(self.controls.values()):
                m = control.macro
                if m.get('mode') == 'hold' and name in (m['key'], m.get('mod', '').title()):
                    control.cancelled.set()
            return
        if name in self.held:                                # 키를 누르고 있어 생기는 자동 반복
            return
        self.held.add(name)
        mods = {n.lower() for n in ('Ctrl', 'Alt', 'Shift') if n in self.held and n != name}
        m = match(self.macros, name, mods)
        if m is None or id(m) in self.running or self.stop_event.is_set():
            return
        if self.game_only and not self.is_game_foreground():
            return
        t = threading.Thread(target=self._run, args=(m,), daemon=True, name='Macro')
        self.controls[id(m)] = _RunStop(self, m)
        self.running[id(m)] = t
        t.start()

    def _run(self, m):
        control = self.controls[id(m)]
        failed = False
        try:
            self.on_status('▶ %s' % m['name'])
            while not control.is_set():
                self.runner(m['steps'], control)
                if m.get('mode') != 'hold':
                    break
                last_wait = m['steps'][-1].get('ms', 0) if m['steps'][-1].get('type') == 'wait' else 0
                if last_wait < MIN_GAP_MS:                    # 순서 끝의 대기가 이미 있으면 그만큼은 쉬었다
                    control.wait(max(MIN_GAP_MS, int(m.get('gap', MIN_GAP_MS))) / 1000)
                if m['key'] not in self.held or (self.game_only and not self.is_game_foreground()):
                    break
        except Exception as exc:
            failed = True
            self.error = '매크로 실행 실패: %s' % exc
            self.on_status(self.error)
            log.exception('매크로 실행 오류')
        finally:
            self.running.pop(id(m), None)
            self.controls.pop(id(m), None)
            if not failed:
                self.on_status('■ %s' % m['name'])

    def stop(self):
        self.stop_event.set()
        for control in list(self.controls.values()):
            control.cancelled.set()
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


def is_elevated(pid=None):
    """프로세스가 관리자 권한(높은 무결성)으로 실행 중인지. 확인할 수 없으면 None."""
    k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    k32.CloseHandle.argtypes = (wt.HANDLE,)
    adv.OpenProcessToken.argtypes = (wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE))
    adv.GetTokenInformation.argtypes = (wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD))
    h = k32.OpenProcess(0x1000, False, pid or k32.GetCurrentProcessId())      # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    tok = wt.HANDLE()
    try:
        if not adv.OpenProcessToken(h, 0x8, ctypes.byref(tok)):              # TOKEN_QUERY
            return None
        val, size = wt.DWORD(), wt.DWORD()
        ok = adv.GetTokenInformation(tok, 20, ctypes.byref(val), 4, ctypes.byref(size))   # TokenElevation
        return bool(val.value) if ok else None
    finally:
        if tok:
            k32.CloseHandle(tok)
        k32.CloseHandle(h)


def game_needs_admin():
    """확인된 권한 차이만 알린다. UIPI는 SendInput 삽입을 제한할 수 있다."""
    u32 = ctypes.windll.user32
    u32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    try:
        from live.window import find_game_window
        w = find_game_window()
        if not w:
            return False
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(w.hwnd, ctypes.byref(pid))
        return is_elevated(pid.value) is True and is_elevated() is False
    except Exception:
        log.exception('권한 확인 실패')
        return False


def relaunch_as_admin(main_script):
    """관리자 권한으로 라그메이트를 다시 실행(Windows 확인 창이 뜬다). 성공하면 True."""
    import os
    import sys
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    exe = w if os.path.isfile(w) else exe
    execute = ctypes.windll.shell32.ShellExecuteW
    execute.argtypes = (wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int)
    execute.restype = ctypes.c_ssize_t
    rc = execute(None, 'runas', exe, '"%s"' % main_script, os.path.dirname(main_script), 1)
    return rc > 32


_game_hwnd = [None]


def _game_foreground():
    """라그나로크 창이 맨 앞인지. 창 핸들은 한 번 찾아 두고, 창이 사라졌을 때만 다시 찾는다."""
    u32 = ctypes.windll.user32
    u32.GetForegroundWindow.restype = wt.HWND
    u32.IsWindow.argtypes = (wt.HWND,)
    try:
        if not _game_hwnd[0] or not u32.IsWindow(_game_hwnd[0]):
            from live.window import find_game_window
            w = find_game_window()
            _game_hwnd[0] = w.hwnd if w else None
        return bool(_game_hwnd[0]) and u32.GetForegroundWindow() == _game_hwnd[0]
    except Exception:
        return False
