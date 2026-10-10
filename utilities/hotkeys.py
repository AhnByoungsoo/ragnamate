"""전역 단축키 (Windows RegisterHotKey). 이 프로그램의 HUD/오버레이만 제어하며 게임에 입력을 보내지 않는다."""
import ctypes
import ctypes.wintypes as wt
import logging
import threading

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
MOD = {'ALT': 0x1, 'CTRL': 0x2, 'SHIFT': 0x4, 'WIN': 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def parse(combo):
    """'Ctrl+Alt+H' → (mods, vk)"""
    parts = [p.strip().upper() for p in combo.split('+') if p.strip()]
    mods = 0
    for p in parts[:-1]:
        mods |= MOD.get(p, 0)
    key = parts[-1]
    vk = ord(key) if len(key) == 1 else {'F%d' % i: 0x6F + i for i in range(1, 13)}.get(key, 0)
    return mods | MOD_NOREPEAT, vk


class HotkeyManager:
    def __init__(self, bindings, callback):
        """bindings: {name: 'Ctrl+Alt+H'}, callback(name)은 단축키 스레드에서 호출(Qt로는 시그널/타이머로 넘길 것)."""
        self.bindings, self.callback = bindings, callback
        self.failed = []
        self._tid = None
        self._thread = threading.Thread(target=self._run, name='Hotkeys', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        ids = {}
        for i, (name, combo) in enumerate(self.bindings.items(), 1):
            mods, vk = parse(combo)
            if vk and user32.RegisterHotKey(None, i, mods, vk):
                ids[i] = name
            else:
                self.failed.append(combo)          # 다른 프로그램이 이미 사용 중일 수 있음
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                try:
                    self.callback(ids[msg.wParam])
                except Exception:
                    log.exception('단축키 처리 오류')
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
