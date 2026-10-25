"""매크로 탭: 트리거 키를 누르면(누르고 있는 동안) 라그메이트가 직접 순서를 실행한다. 리플레이로 연격 콤보 만들기."""
import copy
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter, QToolButton, QVBoxLayout, QWidget)

from core import macros as M
from ui.key_capture import KeyCapture, key_name
from ui.theme import CARD_LIST_QSS, T

POLICY = ('⚠ 라그나로크 운영정책 4.2: 게임 진행 중 실행되어 직접·간접적으로 도움을 주는 프로그램·기기·장치는 '
          '불법 프로그램으로 보고 영구 이용제한될 수 있습니다. 매크로 사용 책임은 본인에게 있습니다.  '
          '매크로는 트리거 키를 누를 때(누르고 있는 동안)만 동작합니다 — 켜 두면 혼자 도는 반복·자동 사냥 없음.')
INPUT_NOTE = ('입력 방식: Windows SendInput(스캔 코드). 게임이 관리자 권한으로 실행 중이면 라그메이트도 관리자 권한이어야 '
              'Windows 권한 제한을 피할 수 있습니다. 입력 삽입 성공이 게임의 수신을 보장하지는 않습니다. 트리거 키는 게임에도 전달됩니다.')
ADMIN_NOTE = ('⚠ 라그나로크가 관리자 권한으로 실행 중인데 라그메이트는 일반 권한입니다. 이 상태에서는 Windows가 라그메이트의 '
              '입력 삽입을 권한 차이로 차단할 수 있습니다. 관리자 실행만으로 게임 수신이 보장되지는 않습니다.')

def steps_from_events(events):
    """[(시각 초, 키, 눌림?)] → 순서. 바로 누르고 뗀 키는 '누르고 떼기' 하나로, 사이 간격은 대기(10ms 단위)."""
    steps, last_t, i = [], None, 0
    while i < len(events):
        t, key, down = events[i]
        if last_t is not None and t - last_t >= 0.01:
            steps.append(dict(type='wait', ms=int(round((t - last_t) * 100)) * 10))
        nxt = events[i + 1] if i + 1 < len(events) else None
        if down and nxt and nxt[1] == key and not nxt[2]:
            steps.append(dict(type='tap', key=key))
            last_t, i = nxt[0], i + 2
            continue
        steps.append(dict(type='down' if down else 'up', key=key))
        last_t, i = t, i + 1
    return steps


class RecordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('키 녹화')
        self.events, self._down = [], set()
        v = QVBoxLayout(self)
        v.addWidget(QLabel('이 창이 앞에 있을 때 누르는 키를 순서·간격 그대로 기록합니다.\n'
                           '(마우스 클릭은 녹화하지 않습니다. 끝나면 [완료])'))
        self.view = QLabel('대기 중…')
        self.view.setWordWrap(True)
        self.view.setMinimumSize(420, 90)
        self.view.setStyleSheet('background:%s; border:1px dashed %s; border-radius:8px; padding:10px;'
                                % (T['surface2'], T['accent']))
        v.addWidget(self.view)
        bb = QDialogButtonBox()
        bb.addButton('다시', QDialogButtonBox.ResetRole).clicked.connect(self._reset)
        bb.addButton('완료', QDialogButtonBox.AcceptRole)
        bb.addButton('취소', QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        for b in bb.buttons():
            b.setFocusPolicy(Qt.NoFocus)             # Space/Enter 가 버튼을 누르지 않고 녹화되도록
        v.addWidget(bb)
        self.setFocusPolicy(Qt.StrongFocus)

    def _reset(self):
        self.events.clear()
        self._down.clear()
        self.view.setText('대기 중…')

    def _add(self, ev, down):
        if ev.isAutoRepeat():
            return
        key = key_name(ev)
        if not key or (down and key in self._down) or (not down and key not in self._down):
            return
        (self._down.add if down else self._down.discard)(key)
        self.events.append((time.monotonic(), key, down))
        self.view.setText('  →  '.join(M.step_text(s) for s in steps_from_events(self.events)))

    def keyPressEvent(self, ev):
        self._add(ev, True)

    def keyReleaseEvent(self, ev):
        self._add(ev, False)

    def steps(self):
        t = time.monotonic()
        tail = [(t, k, False) for k in self._down]       # 녹화를 끝낼 때 눌려 있던 키는 뗀다
        return steps_from_events(self.events + tail)


class StepDialog(QDialog):
    def __init__(self, step=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('순서 편집')
        f = QFormLayout(self)
        self.kind = QComboBox()
        for k, label in M.STEP_TYPES.items():
            self.kind.addItem(label, k)
        self.key = KeyCapture()
        self.ms = QSpinBox(minimum=0, maximum=60000, singleStep=10, suffix=' ms')
        f.addRow('종류', self.kind)
        f.addRow('키', self.key)
        f.addRow('대기', self.ms)
        self.position = QComboBox()
        for value, label in (('current', '현재 마우스 위치'), ('game', '게임 화면 기준 (창 이동에 따라감)'), ('screen', '전체 화면 기준')):
            self.position.addItem(label, value)
        self.x = QSpinBox(minimum=-100000, maximum=100000)
        self.y = QSpinBox(minimum=-100000, maximum=100000)
        self.capture = QPushButton('3초 후 마우스 위치 가져오기')
        self.capture.clicked.connect(self._capture_later)
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(True)
        self.capture_timer.timeout.connect(self._capture_position)
        self.finished.connect(lambda _: self.capture_timer.stop())
        f.addRow('좌표 기준', self.position)
        f.addRow('X (물리 픽셀)', self.x)
        f.addRow('Y (물리 픽셀)', self.y)
        f.addRow(self.capture)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        step = step or dict(type='tap', key='F1')
        self.kind.setCurrentIndex(max(0, self.kind.findData(step['type'])))
        self.key.setCurrentText(step.get('key', 'F1'))
        self.ms.setValue(int(step.get('ms', 100)))
        self.position.setCurrentIndex(max(0, self.position.findData(step.get('position', 'game' if step['type'] == 'move' else 'current'))))
        self.x.setValue(step.get('x', 0))
        self.y.setValue(step.get('y', 0))
        self.position.currentIndexChanged.connect(self._sync)
        self.kind.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        t = self.kind.currentData()
        self.key.setEnabled(t in ('tap', 'down', 'up'))
        self.ms.setEnabled(t == 'wait')
        mouse = t in ('move', 'click', 'rclick')
        if t == 'move' and self.position.currentData() == 'current':
            self.position.setCurrentIndex(self.position.findData('game'))
        self.position.setEnabled(mouse)
        for w in (self.x, self.y, self.capture):
            w.setEnabled(mouse and self.position.currentData() != 'current')

    def _capture_later(self):
        self.capture.setText('마우스를 이동… 3초 후 위치 저장')
        self.capture.setEnabled(False)
        self.capture_timer.start(3000)

    def _capture_position(self):
        from core.mouse_coords import capture_position
        try:
            x, y = capture_position(self.position.currentData())
            self.x.setValue(x)
            self.y.setValue(y)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '좌표 가져오기', str(exc))
        self.capture.setText('3초 후 마우스 위치 가져오기')
        self._sync()

    def accept(self):
        step = self.step()
        if step['type'] in ('move', 'click', 'rclick'):
            from core.mouse_coords import validate_position
            problem = validate_position(step)
            if problem:
                QMessageBox.warning(self, '좌표 확인', problem)
                return
        super().accept()

    def step(self):
        t = self.kind.currentData()
        if t in ('tap', 'down', 'up'):
            return dict(type=t, key=self.key.currentText())
        if t == 'wait':
            return dict(type='wait', ms=self.ms.value())
        if t in ('move', 'click', 'rclick') and self.position.currentData() != 'current':
            return dict(type=t, position=self.position.currentData(), x=self.x.value(), y=self.y.value())
        return dict(type=t)


class _Bridge(QObject):
    status = Signal(str)          # 매크로 스레드 → 화면


class MacroPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.macros = [M.migrate(copy.deepcopy(m)) for m in (self.app.s.get('macros') or [])]
        self.engine = None
        self._loading = False
        self._bridge = _Bridge()
        self._bridge.status.connect(self._on_run_status)
        v = QVBoxLayout(self)

        warn = QLabel(POLICY)
        warn.setWordWrap(True)
        warn.setStyleSheet('background:#3A2A12; color:#FFD9A0; border:1px solid #8A5A1A; border-radius:8px;'
                           ' padding:8px 10px;')
        v.addWidget(warn)

        # ---- 켜기/끄기 줄
        bar = QFrame()
        bar.setObjectName('card')
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        self.b_power = QPushButton()
        self.b_power.setCheckable(True)
        self.b_power.setMinimumWidth(150)
        self.b_power.toggled.connect(self._toggle)
        bl.addWidget(self.b_power)
        self.game_only = QCheckBox('라그나로크 창이 맨 앞일 때만')
        self.game_only.setChecked(bool(self.app.s.get('macro_game_only', True)))
        self.game_only.toggled.connect(self._game_only_changed)
        bl.addWidget(self.game_only)
        b_combo = QPushButton('리플레이로 연격 콤보 만들기')
        b_combo.setProperty('primary', True)
        b_combo.clicked.connect(self._open_combo)
        bl.addWidget(b_combo)
        b_buff = QPushButton('리플레이로 버프 루틴 만들기')
        b_buff.clicked.connect(self._open_buff_routine)
        bl.addWidget(b_buff)
        b_ghub = QPushButton('G HUB에서 가져오기')
        b_ghub.clicked.connect(self._open_ghub_import)
        bl.addWidget(b_ghub)
        b_ghub_check = QPushButton('G HUB 매크로 분석')
        b_ghub_check.setToolTip('G HUB 매크로의 키 간격을 지금 불러온 리플레이의 스킬 재사용 대기·실패와 비교합니다(읽기만).')
        b_ghub_check.clicked.connect(self._open_macro_analysis)
        bl.addWidget(b_ghub_check)
        bl.addStretch(1)
        self.run_label = QLabel()
        bl.addWidget(self.run_label)
        v.addWidget(bar)
        self.admin_box = QFrame()
        self.admin_box.setStyleSheet('QFrame { background:#3A1818; border:1px solid #A04040; border-radius:8px; }'
                                     ' QLabel { color:#FFC9C9; border:0; background:transparent; }')
        al = QHBoxLayout(self.admin_box)
        al.setContentsMargins(12, 8, 12, 8)
        admin_label = QLabel(ADMIN_NOTE)
        admin_label.setWordWrap(True)
        al.addWidget(admin_label, 1)
        b_admin = QPushButton('관리자 권한으로 다시 실행')
        b_admin.setProperty('primary', True)
        b_admin.clicked.connect(self._relaunch_admin)
        al.addWidget(b_admin)
        self.admin_box.setVisible(False)
        v.addWidget(self.admin_box)
        self.status = QLabel(INPUT_NOTE)
        self.status.setWordWrap(True)
        self.status.setObjectName('cardTitle')
        v.addWidget(self.status)

        sp = QSplitter(Qt.Horizontal)
        v.addWidget(sp, 1)

        # ---- 왼쪽: 매크로 목록
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        title = QLabel('매크로 목록')
        title.setObjectName('cardTitle')
        lv.addWidget(title)
        self.list = QListWidget()
        self.list.setStyleSheet(CARD_LIST_QSS)
        self.list.currentRowChanged.connect(self._select)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)     # 끌기·Ctrl·Shift 로 여러 개 선택
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        QShortcut(QKeySequence.Delete, self.list, self._delete, context=Qt.WidgetShortcut)
        lv.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_new = QToolButton(text='+ 새로 만들기')
        b_new.setPopupMode(QToolButton.MenuButtonPopup)
        b_new.clicked.connect(lambda: self._add(M.new_macro()))
        menu = QMenu(b_new)
        for name, steps in M.TEMPLATES.items():
            menu.addAction('템플릿: ' + name, lambda n=name, s=steps: self._add(dict(M.new_macro(n), steps=copy.deepcopy(s))))
        b_new.setMenu(menu)
        row.addWidget(b_new)
        for text, slot in (('복제', self._duplicate), ('삭제', self._delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lv.addLayout(row)
        sp.addWidget(left)

        # ---- 오른쪽: 편집
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName('card')
        g = QGridLayout(box)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(10)
        self.name = QLineEdit()
        self.enabled = QCheckBox('사용')
        self.mod = QComboBox()
        for k, label in M.MODIFIERS.items():
            self.mod.addItem(label, k)
        self.key = KeyCapture()
        self.key.captured.connect(self._trigger_captured)
        self.mode = QComboBox()
        for k, label in M.MODES.items():
            self.mode.addItem(label, k)
        self.gap = QSpinBox(minimum=M.MIN_GAP_MS, maximum=5000, singleStep=10, suffix=' ms')
        g.addWidget(QLabel('이름'), 0, 0)
        g.addWidget(self.name, 0, 1, 1, 3)
        g.addWidget(self.enabled, 0, 4)
        g.addWidget(QLabel('트리거 키'), 1, 0)
        g.addWidget(self.mod, 1, 1)
        g.addWidget(self.key, 1, 2)
        g.addWidget(QLabel('동작'), 2, 0)
        g.addWidget(self.mode, 2, 1, 1, 2)
        self.lbl_gap = QLabel('반복 간격')
        g.addWidget(self.lbl_gap, 2, 3)
        g.addWidget(self.gap, 2, 4)
        g.setColumnStretch(2, 1)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName('cardTitle')
        g.addWidget(self.hint, 3, 0, 1, 5)
        rv.addWidget(box)
        self.name.textEdited.connect(self._changed)
        self.enabled.toggled.connect(self._changed)
        for w in (self.mod, self.mode):
            w.currentIndexChanged.connect(self._changed)
        self.key.keyChanged.connect(self._changed)
        self.gap.valueChanged.connect(self._changed)

        st = QLabel('실행 순서 (더블클릭으로 고치기 · 끌어서 순서 바꾸기)')
        st.setObjectName('cardTitle')
        rv.addWidget(st)
        self.steps = QListWidget()
        self.steps.setStyleSheet(CARD_LIST_QSS)
        self.steps.setDragDropMode(QAbstractItemView.InternalMove)
        self.steps.model().rowsMoved.connect(self._steps_reordered)
        self.steps.itemDoubleClicked.connect(self._edit_step)
        rv.addWidget(self.steps, 1)
        row = QHBoxLayout()
        b_rec = QPushButton('● 키 녹화')
        b_rec.clicked.connect(self._record)
        row.addWidget(b_rec)
        for text, step in (('+ 키', dict(type='tap', key='F1')), ('+ 대기', dict(type='wait', ms=100)),
                           ('+ 클릭', dict(type='click')), ('+ 좌표 이동', dict(type='move', position='game', x=0, y=0))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=step: self._insert_step(dict(s)))
            row.addWidget(b)
        for text, slot in (('위로', lambda: self._move(-1)), ('아래로', lambda: self._move(1)),
                           ('지우기', self._remove_step)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.summary = QLabel()
        row.addWidget(self.summary)
        rv.addLayout(row)
        sp.addWidget(right)
        sp.setSizes([300, 700])

        self._refresh_list()
        if self.macros:
            self.list.setCurrentRow(0)
        else:
            self._select(-1)
        self._set_power_text(False)
        if self.app.s.get('macro_app_enabled'):
            QTimer.singleShot(0, lambda: self.b_power.setChecked(True))

    # ------------------------------------------------------------ 목록
    def _current(self):
        i = self.list.currentRow()
        return self.macros[i] if 0 <= i < len(self.macros) else None

    def _refresh_list(self):
        cur = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for m in self.macros:
            it = QListWidgetItem('%s%s\n%s · %s · %d단계' % ('' if m.get('enabled', True) else '(꺼짐) ', m['name'],
                                                           M.trigger_text(m), M.MODES.get(m.get('mode'), ''),
                                                           len(m.get('steps', []))))
            if not m.get('enabled', True):
                it.setForeground(Qt.gray)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if 0 <= cur < len(self.macros):
            self.list.setCurrentRow(cur)

    def add_macro(self, m):
        """다른 화면(콤보 만들기)에서 만든 매크로 추가."""
        if m.get('replay_combo'):
            from core.combo import register_macro
            self.macros, index = register_macro(self.macros, m)
            self._save()
            self._refresh_list()
            self.list.setCurrentRow(index)
            return self.macros[index]
        self._add(m)
        return m

    def _add(self, m):
        used = {(x.get('key'), x.get('mod', '')) for x in self.macros if x.get('enabled', True)}
        if (m.get('key'), m.get('mod', '')) in used:           # 트리거가 겹치면 꺼 둔 채로 추가
            m['enabled'] = False
        self.macros.append(m)
        self._save()
        self._refresh_list()
        self.list.setCurrentRow(len(self.macros) - 1)

    def _selected(self):
        """선택한 매크로들 (선택이 없으면 현재 항목)."""
        rows = sorted({i.row() for i in self.list.selectedIndexes()})
        if not rows and self._current() is not None:
            rows = [self.list.currentRow()]
        return [self.macros[r] for r in rows if 0 <= r < len(self.macros)]

    def _list_menu(self, pos):
        it = self.list.itemAt(pos)
        if it is not None and not it.isSelected():          # 선택 밖을 우클릭하면 그 항목만 선택
            self.list.setCurrentItem(it)
        sel = self._selected()
        if not sel:
            return
        menu = QMenu(self.list)
        n = '' if len(sel) == 1 else ' (%d개)' % len(sel)
        menu.addAction('삭제%s\tDel' % n, self._delete)
        menu.addAction('복제%s' % n, self._duplicate)
        menu.addSeparator()
        menu.addAction('사용 켜기%s' % n, lambda: self._set_enabled(True))
        menu.addAction('사용 끄기%s' % n, lambda: self._set_enabled(False))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _set_enabled(self, on):
        for m in self._selected():
            m['enabled'] = on
        self._save()
        self._refresh_list()
        self._select(self.list.currentRow())

    def _duplicate(self):
        for m in self._selected():
            self._add(dict(copy.deepcopy(m), name=m['name'] + ' 복사'))

    def _delete(self):
        sel = self._selected()
        if not sel:
            return
        what = '"%s" 매크로를' % sel[0]['name'] if len(sel) == 1 else '매크로 %d개를' % len(sel)
        if QMessageBox.question(self, '삭제', '%s 지울까요?' % what) != QMessageBox.Yes:
            return
        first = self.macros.index(sel[0])
        for m in sel:
            self.macros.remove(m)
        self._save()
        self._refresh_list()
        self.list.setCurrentRow(min(first, len(self.macros) - 1))
        self._select(self.list.currentRow())

    # ------------------------------------------------------------ 편집
    def _select(self, _i):
        m = self._current()
        self._loading = True
        for w in (self.name, self.enabled, self.mod, self.key, self.mode, self.gap, self.steps):
            w.setEnabled(m is not None)
        if m:
            self.name.setText(m['name'])
            self.enabled.setChecked(m.get('enabled', True))
            self.mod.setCurrentIndex(max(0, self.mod.findData(m.get('mod', ''))))
            self.key.setCurrentText(m.get('key', 'W'))
            self.mode.setCurrentIndex(max(0, self.mode.findData(m.get('mode', 'hold'))))
            self.gap.setValue(int(m.get('gap', M.MIN_GAP_MS)))
        self._loading = False
        self._refresh_steps()
        self._sync_fields()

    def _sync_fields(self):
        hold = self.mode.currentData() == 'hold'
        self.lbl_gap.setVisible(hold)
        self.gap.setVisible(hold)
        m = self._current()
        if not m:
            self.hint.setText('')
            return
        trig = M.trigger_text(m)
        self.hint.setText(('%s 키를 누르고 있는 동안 아래 순서를 되풀이하고, 떼면 멈춥니다.' if hold else
                           '%s 키를 누를 때마다 아래 순서를 한 번 실행합니다.') % trig
                          + ' 트리거 키는 가로채지 않으므로 게임에도 그대로 눌립니다.')
        source = m.get('replay_combo')
        if source:
            import os
            profile = source['profile']
            self.hint.setText(self.hint.text() + '\n근거: %s · %s · Lv.%s · ASPD %s · %s%s' % (
                os.path.basename(source['replay']), profile['character'], source['base_level'],
                profile['aspd'] if profile['aspd'] is not None else '정보 없음',
                '계산 초안 / 게임 입력 미검증', ' · 등록 후 수동 수정' if source.get('edited') else ''))

    def _trigger_captured(self, key, mod):
        """누른 그대로 정한다: Ctrl+W 면 조합 Ctrl, W 만 누르면 조합 없음."""
        if key not in ('Ctrl', 'Alt', 'Shift'):
            self.mod.setCurrentIndex(max(0, self.mod.findData(mod)))
        self._changed()

    def _changed(self, *_):
        if self._loading:
            return
        m = self._current()
        if not m:
            return
        m.update(name=self.name.text().strip() or '이름 없음', enabled=self.enabled.isChecked(),
                 mod=self.mod.currentData(), key=self.key.currentText(), mode=self.mode.currentData(),
                 gap=self.gap.value())
        self._sync_fields()
        self._save()
        self._refresh_list()

    # ------------------------------------------------------------ 순서
    def _refresh_steps(self):
        m = self._current()
        self.steps.clear()
        for i, s in enumerate(m.get('steps', []) if m else []):
            it = QListWidgetItem('%2d.  %s' % (i + 1, M.step_text(s)))
            it.setData(Qt.UserRole, s)
            self.steps.addItem(it)
        self.summary.setText('%d단계 · 대기 합계 %.2f초' % (len(m['steps']), M.total_ms(m) / 1000) if m else '')

    def _steps_changed(self, steps, row=None):
        m = self._current()
        if not m:
            return
        m['steps'] = steps
        if m.get('replay_combo'):
            m['replay_combo']['edited'] = True
        self._save()
        self._refresh_steps()
        self._refresh_list()
        if row is not None:
            self.steps.setCurrentRow(row)

    def _steps_reordered(self, *_):
        steps = [self.steps.item(i).data(Qt.UserRole) for i in range(self.steps.count())]
        QTimer.singleShot(0, lambda: self._steps_changed(steps))

    def _insert_step(self, step):
        m = self._current()
        if not m:
            return
        if step['type'] in M.STEP_TYPES:
            d = StepDialog(step, self)
            if d.exec() != QDialog.Accepted:
                return
            step = d.step()
        at = self.steps.currentRow() + 1 if self.steps.currentRow() >= 0 else len(m['steps'])
        self._steps_changed(m['steps'][:at] + [step] + m['steps'][at:], at)

    def _edit_step(self, item):
        m = self._current()
        i = self.steps.row(item)
        d = StepDialog(m['steps'][i], self)
        if d.exec() == QDialog.Accepted:
            steps = list(m['steps'])
            steps[i] = d.step()
            self._steps_changed(steps, i)

    def _move(self, delta):
        m, i = self._current(), self.steps.currentRow()
        if m and 0 <= i < len(m['steps']) and 0 <= i + delta < len(m['steps']):
            steps = list(m['steps'])
            steps[i], steps[i + delta] = steps[i + delta], steps[i]
            self._steps_changed(steps, i + delta)

    def _remove_step(self):
        m, i = self._current(), self.steps.currentRow()
        if m and 0 <= i < len(m['steps']):
            self._steps_changed(m['steps'][:i] + m['steps'][i + 1:], min(i, len(m['steps']) - 2))

    def _record(self):
        m = self._current()
        if not m:
            return
        d = RecordDialog(self)
        if d.exec() == QDialog.Accepted and d.events:
            steps = d.steps()
            if m['steps'] and QMessageBox.question(self, '녹화', '지금 순서를 녹화한 내용으로 바꿀까요?\n(아니오: 뒤에 붙이기)') \
                    != QMessageBox.Yes:
                steps = m['steps'] + steps
            self._steps_changed(steps)

    def _open_ghub_import(self):
        from ui.ghub_import_dialog import GhubImportDialog
        GhubImportDialog(self).exec()

    def _open_macro_analysis(self):
        from ui.macro_analysis_dialog import MacroAnalysisDialog
        MacroAnalysisDialog(self.app, self).exec()

    def _open_combo(self):
        from ui.combo_dialog import ComboDialog
        ComboDialog(self.app, self).exec()

    def _open_buff_routine(self):
        from ui.buff_routine_dialog import BuffRoutineDialog
        self.buff_routine_dialog = BuffRoutineDialog(self.app, self)
        self.buff_routine_dialog.show()

    # ------------------------------------------------------------ 저장·실행
    def _save(self):
        self.app.s['macros'] = copy.deepcopy(self.macros)
        self.app.s.save()
        if self.engine:                                   # 켜져 있으면 바뀐 내용으로 다시 시작
            self._restart()

    def _set_power_text(self, on):
        self.b_power.setText('■ 매크로 끄기' if on else '▶ 매크로 켜기')
        self.b_power.setProperty('primary', on)
        self.b_power.style().unpolish(self.b_power)
        self.b_power.style().polish(self.b_power)

    def _game_only_changed(self, on):
        self.app.s['macro_game_only'] = bool(on)
        self._save()

    def _toggle(self, on):
        self._stop_engine()
        self.app.s['macro_app_enabled'] = bool(on)
        self.app.s.save()
        if on:
            problems = M.validate(self.macros)
            if problems:
                self.status.setText('고칠 곳: ' + ' / '.join(problems))
                self.b_power.setChecked(False)
                return
            self._start_engine()
        else:
            self.status.setText('매크로 꺼짐 · ' + INPUT_NOTE)
            self.run_label.setText('')
        self._set_power_text(on)
        self._check_admin()

    def _check_admin(self):
        """게임만 관리자 권한이면 경고와 [관리자 권한으로 다시 실행]을 보여 준다."""
        self.admin_box.setVisible(M.game_needs_admin())

    def showEvent(self, ev):
        super().showEvent(ev)
        self._check_admin()

    def _relaunch_admin(self):
        import os
        from config.settings import BASE_DIR
        from PySide6.QtWidgets import QApplication
        if QMessageBox.question(self, '관리자 권한으로 다시 실행',
                                '라그메이트를 닫고 관리자 권한으로 다시 엽니다.\nWindows 확인 창에서 [예]를 눌러 주세요.') \
                != QMessageBox.Yes:
            return
        self.app.s.save()
        if M.relaunch_as_admin(os.path.join(BASE_DIR, 'main.py')):
            self.window().close()                          # 단축키·훅 정리(closeEvent) 후 종료 — 새 창과 겹치지 않게
            QApplication.quit()
        else:
            QMessageBox.warning(self, '다시 실행', '관리자 권한으로 실행하지 못했습니다 (확인 창에서 취소했을 수 있음).\n'
                                '라그메이트_실행.bat 을 우클릭 → [관리자 권한으로 실행] 해도 됩니다.')

    def _start_engine(self):
        self.engine = M.MacroEngine(self.macros, game_only=self.game_only.isChecked(),
                                    on_status=self._bridge.status.emit).start()
        QTimer.singleShot(500, self._report_engine)

    def _stop_engine(self):
        if self.engine:
            self.engine.stop()
            self.engine = None

    def _restart(self):
        self._stop_engine()
        problems = M.validate(self.macros)
        if problems:                                      # 조용히 멈추지 않고 꺼짐으로 보여 준다
            self.b_power.blockSignals(True)
            self.b_power.setChecked(False)
            self.b_power.blockSignals(False)
            self._set_power_text(False)
            self.run_label.setText('')
            self.app.s['macro_app_enabled'] = False
            self.app.s.save()
            self.status.setText('매크로가 꺼졌습니다 — 고칠 곳: ' + ' / '.join(problems))
            return
        self._start_engine()

    def _report_engine(self):
        if not self.engine:
            return
        if self.engine.error:
            self.status.setText(self.engine.error)
            return
        n = len(self.engine.macros)
        self.status.setText('매크로 %d개 켜짐%s · 트리거: %s' % (
            n, ' (라그나로크 창이 맨 앞일 때만)' if self.game_only.isChecked() else '',
            ', '.join(M.trigger_text(m) for m in self.engine.macros)))

    def _on_run_status(self, text):
        self.run_label.setText(text)
        if text.startswith('매크로 실행 실패:'):
            self.b_power.setChecked(False)
            self.status.setText(text)

    def shutdown(self):
        self._stop_engine()
