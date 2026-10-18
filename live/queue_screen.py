"""메모리얼 던전 창을 화면에서 읽어 대기 순위와 '입장 가능'을 바로 알린다(대기열 알림이 켜져 있을 때만).

리플레이는 게임이 파일에 쓴 뒤에야 읽히지만, 화면은 게임이 그리는 그 순간 읽힌다.
게임 창만 캡처해 글자만 읽는다(입력·메모리 접근 없음, 캡처는 저장하지 않음).
맨 앞 창이 아니어도 읽는다 — 배럭 창에서 대기를 걸어 두고 다른 캐릭터로 놀아도 된다.
- 창마다 처음 한 번 '메모리얼 던전' 제목 위치를 찾는다(창 전체, 실측 0.7~1.3초). 없으면 8초 뒤 다시 찾는다.
- 찾은 뒤에는 그 창 자리만 0.5초마다 읽는다(실측 수십 ms): '대기 순위 : 13' / '[바곳의 연구실] 입장 가능'.
- 같은 순위를 연달아 두 번 읽어야 받아들인다. 순위는 줄기만 하므로, 늘어난 숫자는 새로 신청한 대기로 본다.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import re
import threading
import time

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32
TITLE = '메모리얼'
RANK = re.compile(r'순위\D{0,4}(\d{1,4})')
READY = re.compile(r'\[([^\]]+)\]\s*입장\s*가')
LOCATE_EVERY = 8.0


def parse(lines):
    """메모리얼 던전 창의 글 줄들 → dict(position, ready, name). 읽을 것이 없으면 None.
    실제 화면에서 읽힌 예: 'CH기 순위 : 13'(대기 → CH기), '[바곳의 연구실] 입장 가통'(능 → 통)."""
    for text in lines:
        m = RANK.search(text)
        if m:
            return dict(position=int(m.group(1)), ready=False, name='')
    for text in lines:
        m = READY.search(text)
        if m:
            return dict(position=0, ready=True, name=m.group(1).strip())
    return None


def game_windows(process_names):
    from live.window import GameWindow, _dpi, _process_name, client_rect
    names = {n.lower() for n in process_names}
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _process_name(pid.value).lower() in names:
                rect = client_rect(hwnd)
                if rect[2] > 300 and rect[3] > 200:
                    found.append(GameWindow(hwnd, pid.value, '', '', rect, False, False, _dpi(hwnd)))
        return True

    user32.EnumWindows(cb, 0)
    return found


class QueueScreenWatcher:
    """on_update(hwnd, state) 를 감시 스레드에서 부른다. state: dict(position, ready, name) 또는 None(창 없음)."""

    def __init__(self, on_update, process_names=('Ragexe.exe', 'Ragnarok.exe'), interval=0.5, ocr=None, active=None):
        self.on_update, self.process_names, self.interval = on_update, process_names, interval
        self.ocr, self.active = ocr, active
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name='QueueScreen', daemon=True)
        self._caps = {}                    # hwnd → WGCCapture
        self.boxes = {}                    # hwnd → 메모리얼 던전 창 자리(x, y, w, h)
        self._located_at = {}
        self._raw, self.states = {}, {}

    def start(self):
        if self.ocr is None:
            from live.ocr import create_ocr
            self.ocr = create_ocr('ko')
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        for cap in self._caps.values():
            cap.stop()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception('메모리얼 던전 창 읽기 실패')

    # ------------------------------------------------------------------ 읽기
    def locate(self, img):
        """창 전체에서 '메모리얼 던전' 제목을 찾아 그 아래 창 자리를 돌려준다."""
        for line in self.ocr.recognize(img, 1.5):
            if TITLE in line.text:
                x, y, _w, h = line.box
                return max(0, x - 40), max(0, y - 6), 400, h + 190
        return None

    def read(self, img, box):
        x, y, w, h = box
        crop = img[y:y + h, x:x + w]
        if not crop.size:
            return None
        lines = [l.text for l in self.ocr.recognize(crop, 2.0)]
        return parse(lines) if any(TITLE in t for t in lines) else None

    def observe(self, hwnd, img, now=None):
        """캡처 한 장 → 받아들인 상태(없으면 None)."""
        now = time.monotonic() if now is None else now
        state = self.read(img, self.boxes[hwnd]) if hwnd in self.boxes else None
        if state is None and now - self._located_at.get(hwnd, -LOCATE_EVERY) >= LOCATE_EVERY:
            self._located_at[hwnd] = now
            box = self.locate(img)
            if box:
                self.boxes[hwnd] = box
                state = self.read(img, box)
            else:
                self.boxes.pop(hwnd, None)
        if state is None:
            if hwnd not in self.boxes:      # 창이 닫힘(입장했거나 취소)
                self.states.pop(hwnd, None)
                self._raw.pop(hwnd, None)
            return self.states.get(hwnd)
        if state['ready'] or self._raw.get(hwnd) == state:      # 입장 가능은 바로, 순위는 두 번 같게 읽히면
            self.states[hwnd] = state
        self._raw[hwnd] = state
        return self.states.get(hwnd)

    def _capture(self, win):
        from live.wgc import WGCCapture
        cap = self._caps.get(win.hwnd)
        if cap is None:
            cap = self._caps[win.hwnd] = WGCCapture(win.hwnd, 200).start()
            time.sleep(0.3)
        img, _t = cap.client_frame(win)
        return img

    def tick(self):
        if self.active and not self.active():
            return
        wins = game_windows(self.process_names)
        alive = {w.hwnd for w in wins}
        for hwnd in list(self._caps):                     # 닫힌 게임 창 정리
            if hwnd not in alive:
                self._caps.pop(hwnd).stop()
                if self.states.pop(hwnd, None) is not None:
                    self.on_update(hwnd, None)
        for win in wins:
            img = self._capture(win)
            if img is None:
                continue
            before = self.states.get(win.hwnd)
            after = self.observe(win.hwnd, img)
            if after != before:
                self.on_update(win.hwnd, after)
