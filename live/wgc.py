"""Windows Graphics Capture 백엔드 (windows-capture 패키지, MIT).

게임 창 하나만 캡처하며(다른 창이 겹쳐도 섞이지 않음), 최신 프레임만 메모리에 보관한다.
DirectX 게임에서 PrintWindow가 검은 화면을 주는 문제를 해결한다.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

log = logging.getLogger(__name__)
DWMWA_EXTENDED_FRAME_BOUNDS = 9


def frame_rect(hwnd):
    r = wt.RECT()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
    except Exception:
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


class WGCCapture:
    available = False
    try:
        from windows_capture import WindowsCapture  # noqa: F401
        available = True
    except Exception:
        pass

    def __init__(self, hwnd, interval_ms=100):
        self.hwnd = hwnd
        self.interval_ms = interval_ms
        self.latest = None
        self.latest_t = 0
        self.frames = 0
        self.closed = False
        self.error = ''
        self._lock = threading.Lock()
        self._ctl = None

    def start(self):
        from windows_capture import WindowsCapture
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=self.hwnd,
                             minimum_update_interval=self.interval_ms)

        @cap.event
        def on_frame_arrived(frame, ctl):
            buf = frame.frame_buffer
            with self._lock:
                self.latest = buf[:, :, :3].copy()
                self.latest_t = time.time()
                self.frames += 1

        @cap.event
        def on_closed():
            self.closed = True

        try:
            self._ctl = cap.start_free_threaded()
        except Exception as e:
            self.error = str(e)
            self.closed = True
            log.warning('WGC 시작 실패: %s', e)
        return self

    def stop(self):
        try:
            if self._ctl:
                self._ctl.stop()
        except Exception:
            pass
        self.closed = True

    def client_frame(self, win):
        """최신 프레임에서 창 테두리/제목줄을 잘라 클라이언트 영역만 반환(BGR)."""
        with self._lock:
            img = self.latest
        if img is None:
            return None, 0
        fx, fy, fw, fh = frame_rect(win.hwnd)
        cx, cy, cw, ch = win.client
        ox, oy = max(0, cx - fx), max(0, cy - fy)
        crop = img[oy:oy + ch, ox:ox + cw]
        return (crop if crop.size else None), self.latest_t
