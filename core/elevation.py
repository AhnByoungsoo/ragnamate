"""GUI 실행 시 Windows의 정상 UAC 승인 절차로 관리자 재실행."""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

MARKER = '--elevated-launch'


def is_admin():
    fn = ctypes.windll.shell32.IsUserAnAdmin
    fn.argtypes = ()
    fn.restype = wt.BOOL
    return bool(fn())


def launch_admin(script, replay=None):
    executable = sys.executable
    windowed = os.path.join(os.path.dirname(executable), 'pythonw.exe')
    if os.path.isfile(windowed):
        executable = windowed
    args = [os.path.abspath(script), MARKER]
    if replay:
        args.append(os.path.abspath(replay))
    execute = ctypes.windll.shell32.ShellExecuteW
    execute.argtypes = (wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int)
    execute.restype = ctypes.c_ssize_t
    result = execute(None, 'runas', executable, subprocess.list2cmdline(args),
                     os.path.dirname(os.path.abspath(script)), 1)
    return result > 32


def show_error(text):
    fn = ctypes.windll.user32.MessageBoxW
    fn.argtypes = (wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
    fn(None, text, '라그메이트 관리자 실행', 0x10)


def ensure_gui_admin(script, replay=None, export=False, attempted=False):
    """None: 현재 프로세스에서 계속, 0: 재실행 성공 후 부모 종료, 1: 실패/취소."""
    if os.name != 'nt' or export:
        return None
    try:
        if is_admin():
            return None
        if attempted:
            show_error('관리자 권한을 확인하지 못했습니다. 프로그램을 종료합니다. 다시 실행하여 Windows 승인 창을 확인하세요.')
            return 1
        if launch_admin(script, replay):
            return 0
        show_error('관리자 권한 승인이 취소되었거나 실행에 실패했습니다. 프로그램을 실행하지 않았습니다.')
    except OSError as exc:
        show_error('관리자 실행 중 오류가 발생했습니다.\n%s' % exc)
    return 1
