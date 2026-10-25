"""대기 중인 게임 창 위쪽 가운데에 크게 띄우는 대기열 표시('대기 13 · 곧 Alt+0' / 'Alt+0 — 지금!' / '입장 가능').

표시만 한다(마우스는 그대로 게임에 간다). 게임에 입력을 보내지 않는다.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

COLORS = {'wait': ('#14171D', '#9ECBFF'), 'soon': ('#3A2E10', '#FFD479'), 'now': ('#5A1414', '#FFFFFF'),
          'ready': ('#123A26', '#7FE0B0')}


def banner_text(state, threshold, key, now_mode):
    """(글, 색 종류). state: dict(position, ready, name)."""
    if state['ready']:
        return '메모리얼 던전 입장 가능' + (' · %s' % state['name'] if state['name'] else ''), 'ready'
    p = state['position']
    if now_mode:
        return ('%s — 지금!  (대기 %d)' % (key, p) if key else '대기열 %d — 지금!' % p), 'now'
    if p <= threshold + 5:
        return '대기 %d · 곧%s' % (p, ' %s' % key if key else ''), 'soon'
    return '대기 %d' % p, 'wait'


class QueueBanner(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.label)
        self.kind = None

    def show_on(self, client, text, kind):
        """client: 게임 창 클라이언트 (x, y, w, h) 화면 좌표."""
        if kind != self.kind:
            bg, fg = COLORS[kind]
            self.label.setStyleSheet('QLabel { background: %s; color: %s; border: 2px solid %s; border-radius: 10px;'
                                     ' padding: 8px 22px; font-size: 26px; font-weight: 800; }' % (bg, fg, fg))
            self.kind = kind
        self.label.setText(text)
        self.adjustSize()
        x, y, w, _h = client
        self.move(x + (w - self.width()) // 2, y + 70)
        if not self.isVisible():
            self.show()
