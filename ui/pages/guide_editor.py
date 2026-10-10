"""Guide Editor (Developer Mode): JSON을 직접 고치지 않고 Guide 추가/수정. 저장 위치: data/guides/user/"""
import datetime

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget, QHBoxLayout)

from navigator.models import GUIDE_TYPES, REQ_KINDS, STATUS, Guide, GuideStep, Requirement, Source

REQ_COLS = ['kind', 'description', 'min', 'item_id', 'item_name', 'quantity', 'jobs(,)', 'guide_id', 'optional(0/1)', 'source']
STEP_COLS = ['order', 'title', 'map', 'x', 'y', 'npc', 'action', 'description', 'beginner_tip']
SRC_COLS = ['title', 'url', 'provider', 'checked_at', 'published_at']


def _table(cols, rows):
    t = QTableWidget(len(rows), len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            t.setItem(r, c, QTableWidgetItem('' if val is None else str(val)))
    return t


def _rows(t):
    out = []
    for r in range(t.rowCount()):
        vals = [(t.item(r, c).text().strip() if t.item(r, c) else '') for c in range(t.columnCount())]
        if any(vals):
            out.append(vals)
    return out


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


class GuideEditor(QDialog):
    def __init__(self, repo, guide=None, parent=None):
        super().__init__(parent)
        self.repo = repo
        g = guide or Guide(id='user_%s' % datetime.datetime.now().strftime('%Y%m%d%H%M%S'), type='QUEST', title='')
        self.setWindowTitle('Guide 편집 — %s' % (g.title or '새 Guide'))
        self.resize(1000, 700)
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs)

        basic = QWidget()
        f = QFormLayout(basic)
        self.f_id = QLineEdit(g.id)
        self.f_type = QComboBox()
        for k, label in GUIDE_TYPES.items():
            self.f_type.addItem('%s (%s)' % (label, k), k)
        self.f_type.setCurrentIndex(list(GUIDE_TYPES).index(g.type) if g.type in GUIDE_TYPES else 0)
        self.f_title = QLineEdit(g.title)
        self.f_aliases = QLineEdit(', '.join(g.aliases))
        self.f_desc = QPlainTextEdit(g.description)
        self.f_min = QSpinBox(maximum=300)
        self.f_min.setValue(g.min_level or 0)
        self.f_map, self.f_x, self.f_y, self.f_npc = QLineEdit(g.start_map), QLineEdit(str(g.start_x or '')), \
            QLineEdit(str(g.start_y or '')), QLineEdit(g.start_npc)
        self.f_prereq = QLineEdit(', '.join(g.prerequisites))
        self.f_maps = QLineEdit(', '.join(g.related_maps))
        self.f_rewards = QPlainTextEdit('\n'.join(g.rewards))
        self.f_status = QComboBox()
        for k, label in STATUS.items():
            self.f_status.addItem(label, k)
        self.f_status.setCurrentIndex(list(STATUS).index(g.status) if g.status in STATUS else 1)
        self.f_verified = QLineEdit(g.verified_at or datetime.date.today().isoformat())
        self.f_notes = QPlainTextEdit(g.notes)
        for label, w in (('ID', self.f_id), ('Type', self.f_type), ('Title', self.f_title), ('Aliases(,)', self.f_aliases),
                         ('설명', self.f_desc), ('최소 레벨(0=없음)', self.f_min), ('시작 맵 ID', self.f_map),
                         ('시작 X', self.f_x), ('시작 Y', self.f_y), ('시작 NPC', self.f_npc),
                         ('선행 Guide ID(,)', self.f_prereq), ('관련 맵 ID(,)', self.f_maps), ('보상(줄바꿈)', self.f_rewards),
                         ('상태', self.f_status), ('확인일', self.f_verified), ('메모', self.f_notes)):
            f.addRow(label, w)
        tabs.addTab(basic, '기본')

        self.t_req = _table(REQ_COLS, [[r.kind, r.description, r.min, r.item_id, r.item_name, r.quantity,
                                        ','.join(map(str, r.jobs)), r.guide_id, int(r.optional), r.source]
                                       for r in g.requirements])
        tabs.addTab(self._with_add(self.t_req), '입장 조건 (kind: %s)' % '/'.join(REQ_KINDS[:6]) + '…')
        self.t_steps = _table(STEP_COLS, [[s.order, s.title, s.map, s.x, s.y, s.npc, s.action, s.description,
                                           s.beginner_tip] for s in g.steps])
        tabs.addTab(self._with_add(self.t_steps), '진행 단계')
        self.t_src = _table(SRC_COLS, [[s.title, s.url, s.provider, s.checked_at, s.published_at] for s in g.sources])
        tabs.addTab(self._with_add(self.t_src), '출처 (provider: official/ingame/community_multi/community/user)')
        self.original = g
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _with_add(self, table):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        b_add, b_del = QPushButton('행 추가'), QPushButton('선택 행 삭제')
        b_add.clicked.connect(lambda: table.insertRow(table.rowCount()))
        b_del.clicked.connect(lambda: [table.removeRow(r) for r in sorted({i.row() for i in table.selectedIndexes()}, reverse=True)])
        h.addWidget(b_add)
        h.addWidget(b_del)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(table)
        return w

    def build(self):
        split = lambda s: [x.strip() for x in s.split(',') if x.strip()]
        reqs = []
        for kind, desc, mn, iid, iname, qty, jobs, gid, opt, src in _rows(self.t_req):
            if kind.upper() not in REQ_KINDS:
                raise ValueError('알 수 없는 조건 kind: %s' % kind)
            reqs.append(Requirement(kind.upper(), desc, _int(mn), None, [int(j) for j in split(jobs) if j.isdigit()],
                                    _int(iid), iname, _int(qty) or 1, gid, src, opt in ('1', 'true', 'True')))
        steps = [GuideStep(_int(o) or i + 1, t, desc, m, _int(x), _int(y), npc, act, beginner_tip=tip)
                 for i, (o, t, m, x, y, npc, act, desc, tip) in enumerate(_rows(self.t_steps))]
        sources = [Source(*row) for row in _rows(self.t_src)]
        g = self.original
        return Guide(
            id=self.f_id.text().strip(), type=self.f_type.currentData(), title=self.f_title.text().strip(),
            aliases=split(self.f_aliases.text()), description=self.f_desc.toPlainText().strip(),
            min_level=self.f_min.value() or None, required_jobs=g.required_jobs, next_jobs=g.next_jobs,
            prerequisites=split(self.f_prereq.text()), requirements=reqs, start_map=self.f_map.text().strip(),
            start_x=_int(self.f_x.text()), start_y=_int(self.f_y.text()), start_npc=self.f_npc.text().strip(),
            steps=steps, rewards=[r for r in self.f_rewards.toPlainText().split('\n') if r.strip()],
            strategy=g.strategy, recommended=g.recommended, material_sources=g.material_sources,
            repeat_type=g.repeat_type, cooldown=g.cooldown, related_maps=split(self.f_maps.text()), tags=g.tags,
            sources=sources, status=self.f_status.currentData(), verified_at=self.f_verified.text().strip(),
            game_version=g.game_version, version=g.version + 1, notes=self.f_notes.toPlainText().strip())

    def save(self):
        try:
            g = self.build()
            if not g.id or not g.title:
                raise ValueError('ID와 Title은 필수입니다')
            if not g.sources:
                if QMessageBox.question(self, '출처 없음', '출처 없이 저장하면 "! 검증 필요"로 표시됩니다. 저장할까요?') \
                        != QMessageBox.Yes:
                    return
            self.repo.save_user_guide(g)
            self.saved = g
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, '저장 실패', str(e))
