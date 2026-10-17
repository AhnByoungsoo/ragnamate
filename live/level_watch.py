"""게임 화면의 기본정보 창에서 'Base Lv. 115'를 읽어 지금 레벨을 알린다(레벨업 코치가 켜져 있을 때만).

게임 창만 캡처해 글자만 읽는다(입력·메모리 접근 없음, 캡처는 저장하지 않음). 라그나로크 창이 맨 앞일 때만 읽는다.
- 처음 한 번 창 전체에서 'Base Lv' 글자 위치를 찾는다(1.5배, 실측 약 0.8초).
- 그다음부터는 그 작은 자리만 1초마다 읽는다(2배, 실측 약 0.01초). 못 읽으면 다시 전체에서 찾는다(5초에 한 번까지).
- 같은 숫자를 연달아 두 번 읽어야 받아들인다(한 번 잘못 읽은 숫자로 코스를 바꾸지 않는다).
- 배럭처럼 게임 창이 여럿이면 창마다 자리를 따로 기억하고, 맨 앞 창의 레벨을 알린다.
"""
import logging
import re
import threading
import time

from live.vend_watch import foreground_game

log = logging.getLogger(__name__)
LEVEL = re.compile(r'(?i)base[^0-9]{0,8}(\d{1,3})')
MAX_LEVEL = 300
LOCATE_EVERY = 5.0


def parse_level(text):
    """'Base Lv. 115' · 'Base 115' · 'Base 나,. 115'(작게 읽힌 Lv.) → 115. 못 찾으면 None."""
    m = LEVEL.search(text or '')
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= MAX_LEVEL else None


class LevelWatcher:
    """on_update(dict(level, hwnd, status)) 를 감시 스레드에서 부른다(바뀌었을 때만)."""

    def __init__(self, on_update, process_names=('Ragexe.exe', 'Ragnarok.exe'), interval=1.0, ocr=None, active=None):
        self.on_update, self.process_names, self.interval = on_update, process_names, interval
        self.ocr, self.active = ocr, active
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name='LevelWatch', daemon=True)
        self._cap = self._cap_hwnd = None
        self.boxes = {}                      # hwnd → 'Base Lv' 줄의 자리(x, y, w, h)
        self._raw = {}                       # hwnd → 마지막으로 읽은 숫자(확정 전)
        self.levels = {}                     # hwnd → 확정한 레벨
        self._located_at = {}
        self._last = None

    def start(self):
        if self.ocr is None:
            from live.ocr import create_ocr
            self.ocr = create_ocr('ko')
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._cap:
            self._cap.stop()

    def _frame(self, win):
        from live.wgc import WGCCapture
        if self._cap_hwnd != win.hwnd:
            if self._cap:
                self._cap.stop()
            self._cap, self._cap_hwnd = WGCCapture(win.hwnd, 500).start(), win.hwnd
            time.sleep(0.4)
        img, _t = self._cap.client_frame(win)
        return img

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception('레벨 읽기 실패')

    # ------------------------------------------------------------------ 읽기
    def locate(self, img):
        """창 전체에서 'Base Lv' 줄을 찾는다 → (자리, 레벨) 또는 (None, None)."""
        for line in self.ocr.recognize(img, 1.5):
            level = parse_level(line.text)
            if level and re.match(r'(?i)\s*base', line.text):
                return line.box, level
        return None, None

    def read_box(self, img, box):
        x, y, w, h = box
        crop = img[max(0, y - 6):y + h + 6, max(0, x - 6):x + w + 60]
        if not crop.size:
            return None
        return next((n for n in (parse_level(l.text) for l in self.ocr.recognize(crop, 2.0)) if n), None)

    def observe(self, hwnd, img, now=None):
        """캡처 한 장 → 확정한 레벨(없으면 None)과 상태 글."""
        now = time.monotonic() if now is None else now
        level = self.read_box(img, self.boxes[hwnd]) if hwnd in self.boxes else None
        if level is None and now - self._located_at.get(hwnd, -LOCATE_EVERY) >= LOCATE_EVERY:
            self._located_at[hwnd] = now
            box, level = self.locate(img)
            if box:
                self.boxes[hwnd] = box
        if level is None:
            return self.levels.get(hwnd), '기본정보 창의 Base Lv를 찾지 못함 (창을 펼쳐 두세요)'
        if self._raw.get(hwnd) == level:     # 연달아 같은 숫자 → 확정
            self.levels[hwnd] = level
        self._raw[hwnd] = level
        return self.levels.get(hwnd), '화면 인식'

    def tick(self):
        if self.active and not self.active():
            self._last = None
            return
        win = foreground_game(self.process_names)
        if win is None:
            return
        img = self._frame(win)
        if img is None:
            return
        level, status = self.observe(win.hwnd, img)
        state = (win.hwnd, level, status)
        if state != self._last:
            self._last = state
            self.on_update(dict(hwnd=win.hwnd, level=level, status=status))
