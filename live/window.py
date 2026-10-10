"""Ragnarok 창 탐지 (Windows 일반 Window API만 사용 — 프로세스 메모리/입력 접근 없음)."""
import ctypes
import ctypes.wintypes as wt
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 64비트 HWND/HANDLE이 기본 c_int로 잘리지 않도록 명시한다.
kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = (wt.HANDLE,)
kernel32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
user32.GetForegroundWindow.restype = wt.HWND
user32.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
user32.ClientToScreen.argtypes = (wt.HWND, ctypes.POINTER(wt.POINT))
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
user32.GetWindowTextLengthW.argtypes = (wt.HWND,)
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.IsWindowVisible.argtypes = (wt.HWND,)
user32.IsIconic.argtypes = (wt.HWND,)
user32.IsWindow.argtypes = (wt.HWND,)
user32.GetDpiForWindow.argtypes = (wt.HWND,)

try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))    # PER_MONITOR_AWARE_V2: 물리 픽셀 좌표 사용
except Exception:
    pass


@dataclass
class GameWindow:
    hwnd: int
    pid: int
    process: str
    title: str
    client: tuple          # (x, y, w, h) 화면 물리 좌표
    foreground: bool
    minimized: bool
    dpi: int

    @property
    def dpi_scale(self):
        return self.dpi / 96.0


def _process_name(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wt.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.replace('\\', '/').split('/')[-1]
        return ''
    finally:
        kernel32.CloseHandle(h)


def client_rect(hwnd):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


def find_game_window(process_names=('Ragexe.exe', 'Ragnarok.exe')) -> Optional[GameWindow]:
    """설정된 프로세스 이름 중 보이는 최상위 창을 찾는다(가장 큰 클라이언트 영역)."""
    names = {n.lower() for n in process_names}
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name(pid.value)
        if name.lower() in names:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((hwnd, pid.value, name, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    best = None
    for hwnd, pid, name, title in found:
        rect = client_rect(hwnd)
        if rect[2] * rect[3] <= 0:
            continue
        gw = GameWindow(hwnd, pid, name, title, rect, user32.GetForegroundWindow() == hwnd,
                        bool(user32.IsIconic(hwnd)), _dpi(hwnd))
        if best is None or rect[2] * rect[3] > best.client[2] * best.client[3]:
            best = gw
    return best


def _dpi(hwnd):
    try:
        return user32.GetDpiForWindow(hwnd) or 96
    except Exception:
        return 96


def is_alive(hwnd):
    return bool(user32.IsWindow(hwnd))
