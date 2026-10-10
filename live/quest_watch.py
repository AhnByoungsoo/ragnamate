"""게임 오른쪽 퀘스트 알림창을 화면 글자로 읽어 알린다(Navigator 오버레이가 켜져 있을 때만).

화면을 캡처해 글자만 읽는다(게임에 입력·메모리 접근 없음). 라그나로크 창이 맨 앞일 때만, 2초마다
오른쪽 알림창 자리(미니맵 아래)를 3배로 읽는다(약 0.15초).
"""
import logging
import threading
import time

from core import quest_tracker as Q
from live.vend_watch import foreground_game

log = logging.getLogger(__name__)


def tracker_region(w, h):
    """알림창 자리(클라이언트 기준 x, y, w, h): 오른쪽 끝 아이콘 줄을 뺀 오른쪽 300px, 위 10%~60%."""
    x0, x1 = max(0, w - 340), max(0, w - 30)
    y0, y1 = int(h * 0.10), int(h * 0.60)
    return x0, y0, x1 - x0, y1 - y0


class QuestWatcher:
    """on_update(quests: [TrackedQuest]) 를 감시 스레드에서 부른다(바뀌었을 때만)."""

    def __init__(self, on_update, process_names=('Ragexe.exe', 'Ragnarok.exe'), interval=2.0, ocr=None, active=None):
        self.on_update = on_update
        self.active = active             # 오버레이가 보이고 [자동]일 때만 읽는다
        self.process_names = process_names
        self.interval = interval
        self.ocr = ocr
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name='QuestWatch', daemon=True)
        self._cap = self._cap_hwnd = None
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
                log.exception('퀘스트 알림창 읽기 실패')

    def read(self, img):
        """캡처 한 장 → [TrackedQuest]."""
        x, y, w, h = tracker_region(img.shape[1], img.shape[0])
        crop = img[y:y + h, x:x + w]
        if not crop.size:
            return []
        lines = [(l.text, l.box, Q.is_title_color(Q.line_color(crop, l.box))) for l in self.ocr.recognize(crop, 3.0)]
        return Q.parse(lines)

    def tick(self):
        if self.active and not self.active():
            self._last = None            # 다시 켜면 바로 알린다
            return
        win = foreground_game(self.process_names)
        if win is None:
            return
        img = self._frame(win)
        if img is None:
            return
        quests = self.read(img)
        sig = tuple((q.title, tuple((o.text, o.cur, o.need) for o in q.objectives)) for q in quests)
        if sig != self._last:
            self._last = sig
            self.on_update(quests)
