"""게임 창 클라이언트 영역만 캡처.

1순위 PrintWindow(PW_RENDERFULLCONTENT): 해당 창의 내용만 받아오므로 겹친 다른 창이 섞이지 않는다.
2순위 화면 BitBlt: 게임 창이 맨 앞(foreground)이고 가려지지 않았을 때만 사용(개인정보 보호).
캡처 프레임은 디스크에 저장하지 않는다(디버그 설정 시에만 별도 저장).
"""
import ctypes
import ctypes.wintypes as wt
import logging

import numpy as np

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


def _dib_to_array(hdc, hbmp, w, h):
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()   # BGR


class CaptureEngine:
    def __init__(self, allow_screen_fallback=True):
        self.allow_screen_fallback = allow_screen_fallback
        self.method = None          # 'printwindow' | 'screen'
        self.last_error = ''

    def capture(self, win):
        """GameWindow → BGR ndarray (클라이언트 영역) 또는 None."""
        x, y, w, h = win.client
        if w <= 0 or h <= 0 or win.minimized:
            self.last_error = '창이 최소화되었거나 크기가 0'
            return None
        img = self._printwindow(win.hwnd, w, h)
        if img is not None and img.mean() > 1.0:
            self.method = 'printwindow'
            return img
        if self.allow_screen_fallback and win.foreground and self._unobstructed(win):
            img = self._screen(x, y, w, h)
            if img is not None:
                self.method = 'screen'
                return img
        self.last_error = 'PrintWindow 결과가 비어 있고, 창이 가려져 있어 화면 캡처를 사용하지 않음'
        return None

    def _printwindow(self, hwnd, w, h):
        hdc_win = user32.GetDC(hwnd)
        hdc = gdi32.CreateCompatibleDC(hdc_win)
        bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
        old = gdi32.SelectObject(hdc, bmp)
        try:
            ok = user32.PrintWindow(hwnd, hdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            return _dib_to_array(hdc, bmp, w, h) if ok else None
        except Exception:
            log.debug('PrintWindow 실패', exc_info=True)
            return None
        finally:
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(hdc)
            user32.ReleaseDC(hwnd, hdc_win)

    def _screen(self, x, y, w, h):
        hdc_scr = user32.GetDC(0)
        hdc = gdi32.CreateCompatibleDC(hdc_scr)
        bmp = gdi32.CreateCompatibleBitmap(hdc_scr, w, h)
        old = gdi32.SelectObject(hdc, bmp)
        try:
            gdi32.BitBlt(hdc, 0, 0, w, h, hdc_scr, x, y, SRCCOPY)
            return _dib_to_array(hdc, bmp, w, h)
        finally:
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(hdc)
            user32.ReleaseDC(0, hdc_scr)

    @staticmethod
    def _unobstructed(win):
        """클라이언트 영역의 여러 지점이 모두 게임 창(또는 그 자식)에 속하는지 확인."""
        x, y, w, h = win.client
        GA_ROOT = 2
        for fx in (0.05, 0.5, 0.95):
            for fy in (0.05, 0.5, 0.95):
                hit = user32.WindowFromPoint(wt.POINT(int(x + w * fx), int(y + h * fy)))
                if user32.GetAncestor(hit, GA_ROOT) != win.hwnd:
                    return False
        return True
