"""클릭한 뒤 키를 누르면 그 키로 정해지는 버튼 (목록에서 찾을 필요 없음). Esc = 취소."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QPushButton

from core import macros as M
from ui.theme import T

_QT_KEYS = {getattr(Qt, 'Key_F%d' % i): 'F%d' % i for i in range(1, 13)}
_QT_KEYS.update({Qt.Key_Space: 'Space', Qt.Key_Return: 'Enter', Qt.Key_Enter: 'Enter', Qt.Key_Escape: 'Esc',
                 Qt.Key_Tab: 'Tab', Qt.Key_Backspace: 'Backspace', Qt.Key_Shift: 'Shift', Qt.Key_Control: 'Ctrl',
                 Qt.Key_Alt: 'Alt', Qt.Key_Insert: 'Insert', Qt.Key_Delete: 'Delete', Qt.Key_Home: 'Home',
                 Qt.Key_End: 'End', Qt.Key_PageUp: 'PageUp', Qt.Key_PageDown: 'PageDown', Qt.Key_Up: 'Up',
                 Qt.Key_Down: 'Down', Qt.Key_Left: 'Left', Qt.Key_Right: 'Right', Qt.Key_Minus: '-', Qt.Key_Equal: '=',
                 Qt.Key_Comma: ',', Qt.Key_Period: '.'})
MODS = {'Ctrl': 'ctrl', 'Alt': 'alt', 'Shift': 'shift'}


def key_name(ev):
    """Qt 키 이벤트 → 매크로 키 이름. 한글 입력 상태와 상관없도록 Windows 가상 키를 먼저 본다."""
    name = M.vk_name(ev.nativeVirtualKey())
    if name:
        return name
    k = ev.key()
    if k in _QT_KEYS:
        return _QT_KEYS[k]
    if Qt.Key_A <= k <= Qt.Key_Z or Qt.Key_0 <= k <= Qt.Key_9:
        name = chr(k)
        return ('Num' + name) if ev.modifiers() & Qt.KeypadModifier and name.isdigit() else name
    return None


class KeyCapture(QPushButton):
    keyChanged = Signal(str)            # 새 키 이름
    captured = Signal(str, str)         # 키, 같이 누른 조합키('ctrl'/'alt'/'shift'/'')

    def __init__(self, key='', placeholder='(클릭 후 키 누르기)', parent=None):
        super().__init__(parent)
        self._key, self._placeholder = key, placeholder
        self._capturing, self._pending_mod = False, None
        self.setToolTip('클릭한 다음 원하는 키를 누르세요 (Esc: 취소)')
        self.clicked.connect(self._start)
        self._show()

    # QComboBox 와 같은 이름으로 쓸 수 있게
    def currentText(self):
        return self._key

    def setCurrentText(self, key):
        self._key = key if key in M.KEYS else ''
        self._show()

    def _show(self):
        if self._capturing:
            self.setText('키를 누르세요…')              # Esc 취소는 툴팁에
            self.setStyleSheet('QPushButton { border: 1px solid %s; background: %s; }' % (T['accent'], T['select']))
        else:
            self.setText(('⌨  ' + self._key) if self._key else self._placeholder)
            self.setStyleSheet('')

    def _start(self):
        self._capturing, self._pending_mod = True, None
        self.setFocus()
        self.grabKeyboard()
        self._show()

    def _stop(self):
        self._capturing, self._pending_mod = False, None
        self.releaseKeyboard()
        self._show()

    def _finish(self, key, mod=''):
        changed = key != self._key
        self._key = key
        self._stop()
        if changed:
            self.keyChanged.emit(key)
        self.captured.emit(key, mod)

    def event(self, ev):
        if self._capturing and ev.type() == QEvent.KeyPress:        # Tab 도 포커스 이동 대신 키로 받는다
            self._on_press(ev)
            return True
        if self._capturing and ev.type() == QEvent.KeyRelease:
            self._on_release(ev)
            return True
        return super().event(ev)

    def _on_press(self, ev):
        if ev.isAutoRepeat():
            return
        if ev.key() == Qt.Key_Escape:
            self._stop()
            return
        name = key_name(ev)
        if not name:
            return
        if name in MODS:                                  # Ctrl 등은 뗄 때까지 기다린다 (Ctrl+W 처럼 조합일 수 있음)
            self._pending_mod = name
            return
        mods = ev.modifiers()
        mod = 'ctrl' if mods & Qt.ControlModifier else 'alt' if mods & Qt.AltModifier else \
            'shift' if mods & Qt.ShiftModifier else ''
        self._finish(name, mod)

    def _on_release(self, ev):
        if ev.isAutoRepeat():
            return
        name = key_name(ev)
        if name and name == self._pending_mod:             # 조합키만 눌렀다 뗐으면 그 키 자체
            self._finish(name, '')

    def focusOutEvent(self, ev):
        if self._capturing:
            self._stop()
        super().focusOutEvent(ev)
