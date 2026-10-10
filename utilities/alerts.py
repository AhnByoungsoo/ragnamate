"""공통 AlertManager (Buff Timer / Status HUD / Navigator 등에서 재사용할 알림 기반).

지원: 소리, 팝업(트레이/상태바 콜백), Overlay 강조 콜백, 임계치, 반복, 음소거.
게임에 입력을 보내지 않는다.
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class AlertRule:
    key: str
    message: str
    threshold: Optional[float] = None       # 값이 이 이하일 때 발동(예: HP% 30)
    repeat_sec: float = 0                   # 0이면 1회
    sound: str = ''                         # 파일 경로(비우면 기본 비프)
    popup: bool = True
    highlight: bool = False
    enabled: bool = True
    _last: float = field(default=0, repr=False)
    _active: bool = field(default=False, repr=False)


class AlertManager:
    def __init__(self, notify: Callable[[str], None] = None, highlight: Callable[[str], None] = None):
        self.rules = {}
        self.muted = False
        self.notify = notify or (lambda msg: None)
        self.highlight = highlight or (lambda key: None)
        self._sounds = {}

    def add(self, rule):
        self.rules[rule.key] = rule

    def check(self, key, value, now=None):
        """임계치 규칙 평가. 발동하면 True."""
        r = self.rules.get(key)
        if not r or not r.enabled or r.threshold is None:
            return False
        now = now or time.monotonic()
        if value is not None and value <= r.threshold:
            first = not r._active
            r._active = True
            if first or (r.repeat_sec and now - r._last >= r.repeat_sec):
                self.fire(r, now)
                return True
        else:
            r._active = False
        return False

    def fire(self, r, now=None):
        r._last = now or time.monotonic()
        if r.popup:
            self.notify(r.message)
        if r.highlight:
            self.highlight(r.key)
        if not self.muted:
            self._play(r.sound)

    def _play(self, path):
        if path:
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtMultimedia import QSoundEffect
                eff = self._sounds.get(path)
                if eff is None:
                    eff = QSoundEffect()
                    eff.setSource(QUrl.fromLocalFile(path))
                    self._sounds[path] = eff
                eff.play()
                return
            except Exception:
                pass                      # 파일/QtMultimedia 문제면 기본 비프로 대체
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.beep()
        except Exception:
            pass
