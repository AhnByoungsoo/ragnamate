"""버프 루틴: 내 버퍼 리플레이에서 '실제로 걸린 버프'와 스킬창 단축키를 읽어 순서를 만들고, 그 순서를 보며 직접 버프를 준다.

라그메이트는 대상(파티원)을 자동으로 고르거나 시전하지 않는다. 파티·자신 버프 구간만 기존 매크로로 내보낼 수 있다.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from core import buff_routine as BR
from core import combo as C
from core import macros as M
from ui.theme import T
from ui.widgets import Col, Table, comma


def _dur(ms):
    """측정된 지속시간. 패킷에 없으면 '모름' — 짐작하지 않는다."""
    return '모름' if not ms else ('%d분' % round(ms / 60000) if ms >= 60000 else '%d초' % round(ms / 1000))


NOTE = ('리플레이에서 <b>직후에 상태가 실제로 걸린 스킬</b>만 버프로 골랐습니다(스킬 이름으로 짐작하지 않습니다). '
        '10초 이상 가는 상태만 버프로 봅니다(공격 스킬이 남기는 0.1초짜리 상태 제외). '
        '단축키는 리플레이의 스킬창 정보와 매크로 탭에서 확인한 키를 씁니다.')
POLICY = ('⚠ 파티원을 자동으로 골라 버프를 주는 기능은 넣지 않았습니다 — 운영정책 4.2에 걸리고, 실제로도 게임이 외부 입력을 '
          '받지 않습니다(2026-09-25·27 확인). 이 창은 <b>순서와 진행만</b> 보여 줍니다. 대상 선택과 시전은 직접 하세요.')


class BuffRoutineDialog(QDialog):
    def __init__(self, app, page=None):
        page = page or getattr(app, 'macro_page', None)
        super().__init__(page)
        self.app, self.page = app, page
        self.setWindowTitle('버프 루틴')
        self.setWindowFlag(Qt.Window)
        self.setModal(False)
        self.resize(1180, 740)
        self.routine = BR.Routine.from_dict(app.s.get('buff_routine') or {})
        self.analysis = dict(party=[], target=[], members=[], unknown=[])
        self.keys, self.key_scope = {}, ''
        self.index = 0

        v = QVBoxLayout(self)
        note = QLabel(NOTE)
        note.setWordWrap(True)
        v.addWidget(note)
        policy = QLabel(POLICY)
        policy.setWordWrap(True)
        policy.setStyleSheet('background:%s; color:#FFD9A0; border-radius:8px; padding:8px;' % T['warn_bg'])
        v.addWidget(policy)

        top = QHBoxLayout()
        self.who = QLabel('리플레이 없음')
        self.who.setWordWrap(True)
        top.addWidget(self.who, 1)
        for text, slot in (('리플레이에서 다시 읽기', self.load_replay), ('순서 다시 만들기', self.rebuild),
                           ('저장', self.save)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        v.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        v.addWidget(split, 1)
        self.found = Table([Col('구분', lambda r: BR.SCOPE_LABEL[r['scope']], width=76),
                            Col('스킬', lambda r: r['name'], width=180),
                            Col('쓴 횟수', lambda r: r['count'], comma, width=68),
                            Col('지속(측정)', lambda r: r.get('remain_ms'), _dur, numeric=True, width=84),
                            Col('단축키', lambda r: self.keys.get(r['skill_id']) or '-', width=62),
                            Col('리플레이에서 받은 사람', lambda r: ', '.join(r.get('targets') or []) or '나')])
        self.found.doubleClicked.connect(lambda idx: self.add_step(self.found.row_at(idx)))
        split.addWidget(self._box('찾은 버프 — 더블클릭하면 순서에 넣습니다', self.found))

        self.members = QListWidget()
        self.members.setSelectionMode(QAbstractItemView.ExtendedSelection)
        mid, mv = self._col()
        mv.addWidget(QLabel('파티원 — 리플레이에서 버프를 받은 사람'))
        mv.addWidget(self.members, 1)
        mv.addLayout(self._buttons((('추가', self.add_member), ('삭제', self.remove_member))))
        split.addWidget(mid)

        self.steps = QListWidget()
        right, rv = self._col()
        rv.addWidget(QLabel('순서 — 파티·자신 버프 먼저, 그다음 사람마다 대상 지정 버프'))
        rv.addWidget(self.steps, 1)
        rv.addLayout(self._buttons((('▲', lambda: self.move(-1)), ('▼', lambda: self.move(1)),
                                    ('삭제', self.remove_step), ('단축키 지정', self.set_hotkey))))
        split.addWidget(right)
        split.setSizes([520, 220, 440])

        self.current = QLabel('순서를 만들고 [시작]을 누르세요.')
        self.current.setStyleSheet('font-size:19px; font-weight:bold; padding:10px; background:%s; border-radius:8px;'
                                   % T['surface'])
        self.current.setWordWrap(True)
        v.addWidget(self.current)

        run = QHBoxLayout()
        for text, slot in (('시작', self.start), ('◀ 이전', lambda: self.step(-1)), ('다음 ▶', lambda: self.step(1)),
                           ('이 사람 건너뛰기', self.skip_member)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            run.addWidget(b)
        self.progress = QLabel('')
        run.addWidget(self.progress, 1)
        run.addWidget(QLabel('매크로 트리거'))
        self.trigger = QComboBox()
        self.trigger.addItems(M.KEY_NAMES)
        self.trigger.setCurrentText('F12')
        run.addWidget(self.trigger)
        b_export = QPushButton('파티 버프만 매크로로')
        b_export.setProperty('primary', True)
        b_export.clicked.connect(self.export_macro)
        run.addWidget(b_export)
        v.addLayout(run)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=lambda: self.step(1))

        self.refresh_steps()
        self.load_replay(quiet=True)

    @staticmethod
    def _col():
        w = QWidget()
        box = QVBoxLayout(w)
        box.setContentsMargins(0, 0, 0, 0)
        return w, box

    def _box(self, title, widget):
        w, box = self._col()
        box.addWidget(QLabel(title))
        box.addWidget(widget, 1)
        return w

    def _buttons(self, pairs):
        row = QHBoxLayout()
        for text, slot in pairs:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        return row

    # ------------------------------------------------------------------ 리플레이 읽기
    def load_replay(self, quiet=False):
        msg = '리플레이 분석 탭에서 버퍼 캐릭터의 리플레이를 먼저 불러오세요.'
        res = getattr(getattr(self.app, 'replay_page', None), 'result', None)
        if res is None:
            self.who.setText(msg)
            if not quiet:
                QMessageBox.information(self, '버프 루틴', msg)
            return
        pr = res.parsed
        self.analysis = BR.analyze(pr, self.app.analysis.gamedata.skills.name)
        self.key_scope = '%s|%s|%s' % (pr.server, pr.character or '?', pr.job_id)
        self.keys = C.merge_keys(self._saved_keys(), C.hotkey_bar(pr.packets))
        rows = self.analysis['party'] + self.analysis['target']
        self.found.set_rows(rows)
        self.members.clear()
        self.members.addItems(self.analysis['members'])
        self.who.setText('%s · 버프로 확인된 스킬 %d개 · 버프 받은 사람 %d명 · 버프인지 확인 못 한 스킬 %d개%s'
                         % (pr.character or '?', len(rows), len(self.analysis['members']),
                            len(self.analysis['unknown']),
                            '' if self.keys else ' · 이 리플레이에는 스킬창 정보가 없어 단축키를 직접 적어야 합니다'))
        if not self.routine.steps:
            self.rebuild()

    def _saved_keys(self):
        """매크로 탭(연격 콤보)에서 사용자가 확인한 캐릭터별 키를 그대로 쓴다."""
        all_keys = self.app.s.get('macro_skill_keys') or {}
        if not all(isinstance(x, dict) for x in all_keys.values()):
            return {}
        return {int(k): x for k, x in (all_keys.get(self.key_scope) or {}).items()}

    def member_names(self):
        return [self.members.item(i).text() for i in range(self.members.count())]

    def rebuild(self):
        self.routine = BR.build(self.analysis, self.member_names(), self.keys)
        self.refresh_steps()

    # ------------------------------------------------------------------ 순서 편집
    def refresh_steps(self):
        self.steps.clear()
        self.steps.addItems(['%s · %s' % (BR.SCOPE_LABEL[s.scope], s.label) for s in self.routine.steps])
        self.show_current()

    def add_step(self, row):
        target = ''
        if row['scope'] == BR.TARGET:
            names = self.member_names()
            if not names:
                QMessageBox.information(self, '대상', '대상 지정 버프입니다. 먼저 파티원을 추가하세요.')
                return
            target, ok = QInputDialog.getItem(self, '대상', '누구에게 줄 버프인가요?', names, 0, False)
            if not ok:
                return
        self.routine.steps.append(BR.Step(row['skill_id'], row['name'], row['scope'], target,
                                          self.keys.get(row['skill_id'], '')))
        self.refresh_steps()

    def move(self, delta):
        i = self.steps.currentRow()
        j = i + delta
        if i < 0 or not 0 <= j < len(self.routine.steps):
            return
        self.routine.steps[i], self.routine.steps[j] = self.routine.steps[j], self.routine.steps[i]
        self.refresh_steps()
        self.steps.setCurrentRow(j)

    def remove_step(self):
        i = self.steps.currentRow()
        if 0 <= i < len(self.routine.steps):
            del self.routine.steps[i]
            self.refresh_steps()

    def set_hotkey(self):
        i = self.steps.currentRow()
        if not 0 <= i < len(self.routine.steps):
            return
        s = self.routine.steps[i]
        names = [''] + M.KEY_NAMES
        key, ok = QInputDialog.getItem(self, '단축키', '게임에서 "%s"에 걸어 둔 키:' % s.name, names,
                                       names.index(s.hotkey) if s.hotkey in names else 0, False)
        if not ok:
            return
        for step in self.routine.steps:                       # 같은 스킬은 같은 키
            if step.skill_id == s.skill_id:
                step.hotkey = key
        if key:
            self.keys[s.skill_id] = key
        self.refresh_steps()

    def add_member(self):
        name, ok = QInputDialog.getText(self, '파티원 추가', '이름:')
        if ok and name.strip():
            self.members.addItem(name.strip())

    def remove_member(self):
        for it in self.members.selectedItems():
            self.members.takeItem(self.members.row(it))

    # ------------------------------------------------------------------ 진행
    def start(self):
        self.index = 0
        self.show_current()

    def step(self, delta):
        if self.routine.steps:
            self.index = max(0, min(len(self.routine.steps), self.index + delta))
            self.show_current()

    def skip_member(self):
        """지금 사람의 남은 버프를 건너뛰고 다음 사람으로."""
        steps = self.routine.steps
        if not 0 <= self.index < len(steps):
            return
        who = steps[self.index].target
        while self.index < len(steps) and steps[self.index].target == who:
            self.index += 1
        self.show_current()

    def show_current(self):
        total = len(self.routine.steps)
        if self.index >= total:
            self.current.setText('다 돌았습니다. 다시 시작하려면 [시작].' if total else '순서가 비어 있습니다.')
            self.progress.setText('%d / %d' % (total, total))
            return
        s = self.routine.steps[self.index]
        nxt = self.routine.steps[self.index + 1] if self.index + 1 < total else None
        self.current.setText('지금: %s%s' % (s.label, ('        다음: ' + nxt.label) if nxt else ''))
        self.progress.setText('%d / %d  (Space = 다음)' % (self.index + 1, total))
        self.steps.setCurrentRow(self.index)

    # ------------------------------------------------------------------ 저장 · 매크로
    def save(self):
        self.app.s['buff_routine'] = self.routine.to_dict()
        self.app.s.save()
        self.app.status('버프 루틴을 저장했습니다 (%d단계)' % len(self.routine.steps))

    def export_macro(self):
        """대상을 고르지 않아도 되는 파티·자신 버프만 매크로로. 대상 지정 버프는 내보내지 않는다."""
        if self.page is None:
            return
        unknown = sorted({s.hotkey for s in BR.party_only(self.routine) if s.hotkey not in M.KEYS})
        steps = BR.macro_steps(BR.Routine(self.routine.name,
                                          [s for s in self.routine.steps if s.hotkey in M.KEYS]))
        if not steps:
            QMessageBox.information(self, '파티 버프만 매크로로',
                                    '매크로로 보낼 파티·자신 버프가 없습니다.\n'
                                    '순서에서 버프를 고르고 [단축키 지정]으로 게임에서 쓰는 키를 적어 주세요.')
            return
        m = dict(M.new_macro('파티 버프'), key=self.trigger.currentText(), mod='', mode='once', steps=steps)
        registered = self.page.add_macro(m)
        QMessageBox.information(self, '파티 버프만 매크로로',
                                '매크로 탭에 "파티 버프"(트리거 %s)로 넣었습니다 — %d단계%s.\n\n'
                                '• 대상 지정 버프는 넣지 않았습니다. 파티원 선택은 직접 하세요.\n'
                                '• 트리거 키를 누르고 있는 동안에만 동작하며, 게임이 외부 입력을 받지 않을 수 있습니다'
                                ' (매크로 탭 안내 참고).%s'
                                % (M.trigger_text(m), len(steps),
                                   '' if registered.get('enabled', True) else ' · 트리거가 겹쳐 꺼진 채로 저장',
                                   ('\n• 키 %s 은(는) 매크로가 보낼 수 없어 제외했습니다.' % ', '.join(unknown)) if unknown else ''))
