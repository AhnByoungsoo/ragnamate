"""Buff Timer 패널: 타이머 등록 · 시작/갱신 · 알림 · Replay에서 확인된 Buff 표시.

지속시간은 사용자가 입력한 값이다(게임에서 읽지 않는다). Replay 행(●)만 패킷에서 온 값이다.
"""
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QSplitter,
                               QToolButton, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from live.buffs import REPLAY, BuffDef, mmss
from live.status import BUFF, KIND_LABEL
from ui.buff_overlay import display_menu
from ui.widgets import Col, Table


class BuffDialog(QDialog):
    def __init__(self, d=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('버프 타이머')
        f = QFormLayout(self)
        self.name = QLineEdit(d.name if d else '')
        self.dur = QSpinBox(maximum=86400, minimum=1, value=d.duration if d else 1800, suffix=' 초')
        self.alert = QSpinBox(maximum=3600, minimum=0, value=d.alert_sec if d else 10, suffix=' 초 전 알림')
        self.sound = QLineEdit(d.sound if d else '')
        pick = QPushButton('소리 파일...')
        pick.clicked.connect(self._pick)
        row = QHBoxLayout()
        row.addWidget(self.sound, 1)
        row.addWidget(pick)
        box = QWidget()
        box.setLayout(row)
        f.addRow('이름', self.name)
        f.addRow('지속시간', self.dur)
        f.addRow('미리 알림', self.alert)
        f.addRow('알림 소리(.wav)', box)
        f.addRow(QLabel('지속시간은 직접 입력한 값으로 계산합니다. 게임에서 실제 남은 시간을 읽어오지 않습니다.'))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(self, '알림 소리 선택', '', '소리 파일 (*.wav)')
        if path:
            self.sound.setText(path)

    def value(self):
        return BuffDef(self.name.text().strip(), self.dur.value(), self.alert.value(), self.sound.text().strip())


class BuffPanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        for text, slot in (('추가', self.add), ('수정', self.edit), ('삭제', self.remove),
                           ('▲', lambda: self.move(-1)), ('▼', lambda: self.move(1)),
                           ('시작 / 갱신', self.start), ('정지', self.stop), ('모두 해제', self.stop_all)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        self.b_pause = QPushButton('일시정지')
        self.b_pause.setCheckable(True)
        self.b_pause.toggled.connect(self.app.pause_buffs)
        top.addWidget(self.b_pause)
        for text, slot in (('이름 지정', self.rename), ('아이콘 지정', self.set_icon), ('★ 고정', self.toggle_fav)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        self.b_display = QToolButton()
        self.b_display.setText('표시 설정')
        self.b_display.setPopupMode(QToolButton.InstantPopup)
        self.b_display.setMenu(self._display_menu())
        top.addWidget(self.b_display)
        self.keep = QCheckBox('만료 후에도 목록에 남기기')
        self.keep.setChecked(bool(app.s.get('buff_keep_expired')))
        self.keep.toggled.connect(self._keep_changed)
        top.addWidget(self.keep)
        top.addStretch(1)
        v.addLayout(top)

        sp = QSplitter(Qt.Horizontal)
        self.defs = Table([Col('단축키', lambda d: self._hotkey_label(d), width=80),
                           Col('이름', lambda d: d.name, width=150), Col('지속(초)', lambda d: d.duration, width=70),
                           Col('미리 알림(초)', lambda d: d.alert_sec, width=90),
                           Col('소리', lambda d: d.sound.rsplit('\\', 1)[-1] if d.sound else '기본 비프')])
        self.defs.doubleClicked.connect(lambda idx: self.start())
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel('등록한 타이머 — 더블클릭하면 시작/갱신. 위 4개는 게임 중에 단축키로 바로 걸 수 있고, '
                            '▲▼로 순서를 바꾸면 단축키도 따라갑니다.'))
        lv.addWidget(self.defs)
        sp.addWidget(left)

        self.running = Table([Col('★', lambda r: '★' if r.get('favorite') else '', width=30),
                              Col('출처', lambda r: r.get('icon', '○'), width=45),
                              Col('이름', lambda r: r['name'], width=230,
                                  icon=lambda r: self.app.icons.icon(r.get('icon_key') or r.get('key', ''))),
                              Col('남은 시간', lambda r: r.get('remaining'), mmss, True, 90),
                              Col('상태', lambda r: r.get('label') or r.get('state', ''), width=90),
                              Col('구분', lambda r: '내부 상태' if r.get('internal') else
                                  '' if r.get('kind', BUFF) == BUFF else KIND_LABEL.get(r.get('kind'), ''), width=150,
                                  tooltip=lambda r: '' if r.get('kind', BUFF) == BUFF and not r.get('internal') else
                                  '버프가 아니어서 버프 타이머(오버레이·HUD)에는 올리지 않습니다. ★ 즐겨찾기하면 올라갑니다.'),
                              Col('비고', lambda r: r.get('suggest') or
                                  ((r.get('raw', '') + ' · ' if r.get('raw') and
                                    self.app.s.get('developer_mode') else '') + r.get('note', '')))])
        self.running.doubleClicked.connect(lambda idx: self.rename(self.running.row_at(idx)))
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel('진행 중 — ○ 내가 시작한 계산 타이머 · ● Replay 패킷에서 확인된 효과. '
                            '이름이 「알 수 없는 효과」면 게임에서 방금 걸린 것을 보고 더블클릭해 이름을 지정하세요. '
                            '[★ 고정]을 누르면 LIVE 상단에 항상 표시됩니다.'))
        rv.addWidget(self.running)
        sp.addWidget(right)
        v.addWidget(sp, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        v.addWidget(self.note)
        self.refresh_defs()

    # ------------------------------------------------------------------ 갱신
    def _hotkey_label(self, d):
        defs = self.app.buffs.defs
        i = next((n for n, x in enumerate(defs) if x.key == d.key), -1)
        return (self.app.s.get('hotkeys') or {}).get('buff%d' % (i + 1), '') if 0 <= i < 4 else ''

    def refresh_defs(self):
        self.defs.set_rows(self.app.buffs.defs)

    def refresh(self, rows, paused):
        self.running.set_rows(rows)
        if self.b_pause.isChecked() != paused:
            self.b_pause.blockSignals(True)
            self.b_pause.setChecked(paused)
            self.b_pause.blockSignals(False)
        warn = [r for r in rows if r['state'] in ('WARN', 'DANGER')]
        self.note.setText(('곧 종료: ' + ', '.join('%s %s' % (r['name'], mmss(r.get('remaining'))) for r in warn))
                          if warn else '화면에서 Buff 아이콘을 읽는 기능은 없습니다. ○ 타이머는 시작 시각 기준 계산값입니다.')

    # ------------------------------------------------------------------ 동작
    def _selected(self):
        rows = self.defs.selected_rows()
        return rows[0] if rows else None

    def _display_menu(self):
        return display_menu(self.app.s, self.b_display, self.app.refresh_buffs,
                            '아이콘: 진행 중 목록에서 줄을 고르고 [아이콘 지정]')

    def _selected_running(self, row=None):
        if isinstance(row, dict):
            return row
        rows = self.running.selected_rows()
        return rows[0] if rows else None

    def rename(self, row=None):
        """Replay Buff(EFST)에 사용자가 확인한 이름을 붙인다. 이름을 지어내지 않고 여기서만 정한다."""
        row = self._selected_running(row)
        if not row:
            QMessageBox.information(self, '이름 지정', '오른쪽 [진행 중] 목록에서 이름을 붙일 줄을 고르세요.')
            return
        efst = row.get('efst') or (int(row['key'].split('_')[-1]) if row.get('key', '').startswith('replay_')
                                   else None)
        if row.get('origin') != REPLAY or efst is None:
            d = self.app.buffs.find(row['key'])
            if d:
                self.defs.selectRow([x.key for x in self.app.buffs.defs].index(d.key))
                self.edit()
            return
        cur = '' if row['name'].startswith('알 수 없는 효과') else row['name']
        prompt = '게임에서 확인한 이름을 그대로 적어 주세요 (%s):' % (row.get('raw') or efst)
        if not cur and row.get('suggest_skill') is not None:
            prompt += '\n' + row.get('suggest', '') + '\n후보와 실제 효과가 같은지 확인해 직접 입력하세요.'
        name, ok = QInputDialog.getText(self, '효과 이름 지정', prompt, text=cur)
        if not ok:
            return
        self.app.analysis.gamedata.buffs.set(efst, name)
        self.app.refresh_buffs()
        self.app.status('효과 이름 저장: %s → %s' % (row.get('raw') or efst, name.strip() or '(지움)'))

    def toggle_fav(self):
        """자주 보는 효과를 LIVE 상단에 고정."""
        row = self._selected_running()
        if not row:
            QMessageBox.information(self, '즐겨찾기', '오른쪽 [진행 중] 목록에서 줄을 고르세요.')
            return
        on = self.app.status_monitor.toggle_favorite(row['key'])
        self.app.refresh_buffs()
        self.app.status('%s %s' % (row['name'], '고정했습니다' if on else '고정 해제'))

    def set_icon(self):
        """버프 한 줄에 쓸 작은 아이콘 이미지를 사용자가 직접 지정(게임 파일에서 추출하지 않는다)."""
        row = self._selected_running()
        if not row:
            QMessageBox.information(self, '아이콘 지정', '오른쪽 [진행 중] 목록에서 줄을 고르세요.')
            return
        path, _ = QFileDialog.getOpenFileName(self, '%s 아이콘 이미지' % row['name'], '',
                                              '이미지 (*.png *.bmp *.jpg *.jpeg *.gif)')
        if path:
            self.app.buff_icons.set(row['key'], path)
            self.app.icons.clear()
            self.app.refresh_buffs()
            self.app.status('아이콘 지정: %s' % row['name'])

    def _keep_changed(self, on):
        self.app.s['buff_keep_expired'] = bool(on)
        self.app.s.save()

    def add(self):
        dlg = BuffDialog(parent=self)
        if dlg.exec():
            d = dlg.value()
            if not d.name:
                QMessageBox.information(self, '버프 타이머', '이름을 입력하세요.')
                return
            self.app.buffs.add_def(d)
            self.refresh_defs()

    def edit(self):
        d = self._selected()
        if not d:
            return
        dlg = BuffDialog(d, self)
        if dlg.exec():
            new = dlg.value()
            if new.key != d.key:
                self.app.buffs.remove_def(d.key)
            self.app.buffs.add_def(new)
            self.refresh_defs()

    def move(self, delta):
        """목록 순서 = 단축키 번호. 선택한 줄을 위/아래로."""
        d = self._selected()
        if not d:
            return
        defs = self.app.buffs.defs
        i = next((n for n, x in enumerate(defs) if x.key == d.key), -1)
        j = i + delta
        if i < 0 or not 0 <= j < len(defs):
            return
        defs[i], defs[j] = defs[j], defs[i]
        self.app.buffs.save_defs(defs)
        self.refresh_defs()
        self.defs.selectRow(j)

    def remove(self):
        d = self._selected()
        if d:
            self.app.buffs.remove_def(d.key)
            self.refresh_defs()

    def start(self):
        d = self._selected()
        if d:
            self.app.buffs.start(d.key)
            self.app.refresh_buffs()
            self.app.status('버프 타이머 시작: %s (%d초)' % (d.name, d.duration))

    def stop(self):
        rows = self.running.selected_rows()
        for r in rows:
            if r['source'] == 'MANUAL':
                self.app.buffs.stop(r['key'])
        if not rows:
            d = self._selected()
            if d:
                self.app.buffs.stop(d.key)
        self.app.refresh_buffs()

    def stop_all(self):
        self.app.buffs.stop_all()
        self.app.refresh_buffs()
