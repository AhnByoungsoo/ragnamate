"""업데이트 버튼: 켤 때 새 버전이 있는지 뒤에서 확인하고, 있으면 '업데이트 하겠습니까?'를 묻는다."""
import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from services import updater

IDLE, FOUND = '업데이트 확인', '새 버전 있음 · 업데이트'
SHOWN = 8                                                         # 물어볼 때 보여 줄 변경 제목 수


class UpdateController(QObject):
    checked = Signal(object, bool)                                # 변경 제목들 또는 오류, 직접 눌렀는가
    applied = Signal(object)                                      # 새 버전 또는 오류

    def __init__(self, window, root):
        super().__init__(window)
        self.window, self.root, self.busy = window, root, False
        self.button = QPushButton(IDLE)
        self.button.setToolTip('GitHub에 새 버전이 있는지 확인하고, 있으면 받아서 다시 시작합니다. 설정과 기록은 그대로 둡니다.')
        self.button.clicked.connect(lambda: self.check(manual=True))
        self.button.setVisible(updater.available(root))
        self.checked.connect(self._on_checked)
        self.applied.connect(self._on_applied)

    def _background(self, fn, signal, *extra):
        def work():
            try:
                result = fn(self.root)
            except Exception as e:
                result = e
            signal.emit(result, *extra)
        self.busy = True
        threading.Thread(target=work, daemon=True).start()

    def check(self, manual=False):
        if self.busy or self.button.isHidden():
            return
        if manual:
            self.button.setText('확인하는 중…')
        self._background(updater.check, self.checked, manual)

    def _on_checked(self, changes, manual):
        self.busy = False
        if isinstance(changes, Exception):
            self.button.setText(IDLE)
            if manual:                                            # 켤 때 자동 확인은 실패해도 조용히 넘어간다
                QMessageBox.warning(self.window, '업데이트', '새 버전을 확인하지 못했습니다.\n\n%s' % changes)
            return
        self.button.setText(FOUND if changes else IDLE)
        if not changes:
            if manual:
                QMessageBox.information(self.window, '업데이트', '지금 버전이 최신입니다.')
            return
        lines = ['· ' + c for c in changes[:SHOWN]]
        if len(changes) > SHOWN:
            lines.append('· … 그 밖에 %d건' % (len(changes) - SHOWN))
        text = '새 버전이 있습니다 (변경 %d건).\n\n%s\n\n업데이트 하겠습니까?\n설정과 기록은 그대로 두고, 받은 뒤 프로그램을 다시 시작합니다.' \
               % (len(changes), '\n'.join(lines))
        if QMessageBox.question(self.window, '업데이트', text) == QMessageBox.Yes:
            self.button.setText('받는 중…')
            self.button.setEnabled(False)
            self._background(updater.apply, self.applied)

    def _on_applied(self, result):
        self.busy = False
        self.button.setEnabled(True)
        if isinstance(result, Exception):
            self.button.setText(FOUND)
            QMessageBox.warning(self.window, '업데이트', '업데이트하지 못했습니다. 지금 버전을 그대로 씁니다.\n\n%s' % result)
            return
        self.button.setText(IDLE)
        QMessageBox.information(self.window, '업데이트', '새 버전(%s)을 받았습니다. 프로그램을 다시 시작합니다.' % result)
        self.restart()

    def restart(self):
        # '이미 실행 중' 잠금을 풀어야 새로 켜진다. main.py 를 바로 실행했으면 __main__, 다른 곳에서 불렀으면 main 모듈에 있다
        for mod in {m for m in (sys.modules.get('__main__'), sys.modules.get('main')) if m is not None}:
            handle = getattr(mod, '_INSTANCE', None)
            if handle and os.name == 'nt':
                import ctypes
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
                mod._INSTANCE = None
        subprocess.Popen([sys.executable] + sys.argv, cwd=self.root)
        self.window.close()
        QApplication.quit()
