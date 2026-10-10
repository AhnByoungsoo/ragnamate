"""게임 화면에서 '노점 개설' 창을 지켜보다가 열리면 줄(아이템·판매가)을 읽어 알린다. 리플레이가 없어도 된다.

화면을 캡처해 글자만 읽는다(게임에 입력·메모리 접근 없음).
- 라그나로크 창이 맨 앞일 때만 확인한다(여러 클라이언트 중 앞에 있는 창).
- 창을 아직 못 찾았으면 1.5초마다 화면 전체에서 제목을 찾고(약 0.35초),
  찾은 뒤에는 그 영역만 읽는다. 창이 바뀌지 않았으면(그림 비교) 다시 읽지 않는다.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

import numpy as np

from core import vend_ocr as V

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32


def foreground_game(process_names):
    """맨 앞 창이 라그나로크면 그 GameWindow, 아니면 None."""
    from live.window import GameWindow, _dpi, _process_name, client_rect
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = _process_name(pid.value)
    if name.lower() not in {n.lower() for n in process_names} or user32.IsIconic(hwnd):
        return None
    rect = client_rect(hwnd)
    if rect[2] * rect[3] <= 0:
        return None
    return GameWindow(hwnd, pid.value, name, '', rect, True, False, _dpi(hwnd))


class VendWatcher:
    """on_update(state) 를 감시 스레드에서 부른다. state: None(창 없음) 또는
    dict(hwnd, client=(x,y,w,h), region=(x,y,w,h) 클라이언트 기준, rows=[VendRow])."""

    def __init__(self, on_update, process_names=('Ragexe.exe', 'Ragnarok.exe'), interval=1.5, ocr=None):
        self.on_update = on_update
        self.process_names = process_names
        self.interval = interval
        self.ocr = ocr
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name='VendWatch', daemon=True)
        self._cap = self._cap_hwnd = None
        self._region = None
        self._last_crop = None
        self._last_sig = None
        self._shop = ''                  # 노점 창의 상점명(내 노점을 시세에서 빼는 데 씀)
        self._box = None                 # 바뀜 판단에 쓰는 창 안쪽 상자
        self._state = None               # 마지막으로 알린 상태
        self._dialog = False             # 게임 확인 창이 떠 있는지
        self.error = ''

    def start(self):
        if self.ocr is None:
            from live.ocr import create_ocr
            self.ocr = create_ocr('ko')
        if not getattr(self.ocr, 'available', False):
            self.error = getattr(self.ocr, 'error', '') or 'Windows OCR을 쓸 수 없습니다'
        self._thread.start()
        return self

    def force(self):
        """[다시 읽기]: 다음 확인 때 바뀜 여부와 상관없이 창을 다시 읽는다(스레드 안전: 값만 바꿈)."""
        self._last_crop = None
        self._last_sig = None

    def stop(self):
        self._stop.set()
        if self._cap:
            self._cap.stop()

    # ------------------------------------------------------------ 감시
    def _frame(self, win):
        from live.wgc import WGCCapture
        if self._cap_hwnd != win.hwnd:
            if self._cap:
                self._cap.stop()
            self._cap, self._cap_hwnd = WGCCapture(win.hwnd, 200).start(), win.hwnd
            self._region = None
            time.sleep(0.4)
        img, _t = self._cap.client_frame(win)
        return img

    def _emit(self, state):
        sig = None if state is None else (state['hwnd'], state['region'], state.get('dialog'),
                                          tuple((r.name_text, r.price) for r in state['rows']))
        if sig != self._last_sig:
            self._last_sig = sig
            try:
                self.on_update(state)
            except Exception:
                log.exception('노점 표시 갱신 실패')

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception('노점 창 확인 실패')

    def _read_shop(self, img, title_xy):
        """상점명 칸은 글자가 작아 창 전체를 읽을 때 자주 깨진다 → '상점명' 글자째 잘라 3배로 따로 읽는다."""
        tx, ty = title_xy[0], title_xy[1]
        crop = img[max(0, ty + 12):ty + 40, max(0, tx - 25):tx + 370]
        return V.find_shop(self.ocr.recognize(crop, 3.0)) if crop.size else ''

    def _dialog_open(self, img):
        """게임 가운데쯤의 확인 창('…의 가격이 1억 9천만Zeny 이상입니다. 입력금액이 맞습니까?')을 찾는다.
        노점 창 자리는 가린다(창 안의 '천만Zeny이상 안전확인' 글자와 헷갈리지 않게)."""
        h, w = img.shape[:2]
        y0, y1 = int(h * 0.15), int(h * 0.85)
        band = img[y0:y1].copy()
        x, _y, rw, _rh = self._region
        band[:, x:x + rw] = 0
        return V.find_dialog(self.ocr.recognize(band, 1.0))

    def _name_alts(self, img, lines, rows):
        """이름 칸만 잘라 3배로 다시 읽는다(창 전체 2배 읽기는 아이콘과 붙거나 두 줄 이름 뒷줄을 놓침)."""
        tb = V.find_title(lines)
        if tb is None:
            return
        x, y = self._region[:2]
        for i, r in enumerate(rows):
            x0, y0, x1, y1 = V.name_box(r, rows[i + 1].y if i + 1 < len(rows) else None, tb[0])
            crop = img[y + y0:y + y1, x + x0:x + x1]
            if crop.size:
                got = self.ocr.recognize(crop, 3.0)
                r.alts = [' '.join(l.text.strip() for l in sorted(got, key=lambda l: l.box[1]))]

    def _fix_prices(self, img, rows):
        """판매가·실거래가 숫자가 둘 다 깨진 줄('9.5叨.0叨')은 그 두 줄만 잘라 다시 읽는다(배율마다 깨지는 글자가 달라
        3·2·4배 순서로: 초록 '2,890,000' 은 3배 '긴 ,000', 2배 '2,890.000')."""
        x, y = self._region[:2]
        for r in rows:
            if r.price is None:
                crop = img[y + max(0, r.y - 3):y + r.y + r.h + 18, x + max(0, r.x - 4):x + r.x + 170]
                for scale in (3.0, 2.0, 4.0) if crop.size else ():
                    got = V.parse_rows(self.ocr.recognize(crop, scale))
                    if got and got[0].price is not None:
                        r.price, r.real = got[0].price, got[0].real
                        break

    def tick(self):
        win = foreground_game(self.process_names)
        if win is None:
            return                       # 다른 창을 보고 있으면 그대로 둔다(패널도 게임이 앞일 때만 보임)
        img = self._frame(win)
        if img is None:
            return
        h, w = img.shape[:2]
        if self._region is not None:
            dialog = self._dialog_open(img)
            if dialog != self._dialog:
                self._dialog = dialog
                if self._state:
                    self._state = dict(self._state, dialog=dialog)
                    self._emit(self._state)
            x, y, rw, rh = self._region
            crop = img[y:y + rh, x:x + rw]
            if not V.content_changed(self._last_crop, crop, self._box):
                return                   # 창 안쪽이 그대로면 글자를 다시 읽지 않는다(아이템 추가·가격 수정·스크롤은 바뀜)
            head = self.ocr.recognize(img[y:y + 60, x:x + 420], 1.0)   # 창이 아직 그 자리에 있는지 제목 확인
            if not V.find_title(head):
                self._region = self._last_crop = None
        if self._region is None:
            tb = V.find_title(self.ocr.recognize(img, 1.0))
            if tb is None:
                self._state, self._dialog = None, False
                self._emit(None)
                return
            self._region = V.window_region(tb, w, h)
        x, y, rw, rh = self._region
        crop = img[y:y + rh, x:x + rw]
        self._last_crop = crop.copy()
        lines = self.ocr.recognize(crop, 2.0)
        rows = V.parse_rows(lines)
        self._fix_prices(img, rows)
        self._name_alts(img, lines, rows)
        self._box = V.watch_box(lines)
        tb2 = V.find_title(lines)          # 2배로 읽은 제목 위치가 정확하다(큰 화면 1배 OCR은 10px쯤 어긋남)
        if tb2:
            self._shop = self._read_shop(img, (x + tb2[0], y + tb2[1])) or self._shop
        self._state = dict(hwnd=win.hwnd, client=win.client, region=self._region, rows=rows, shop=self._shop,
                           dialog=self._dialog)
        self._emit(self._state)
