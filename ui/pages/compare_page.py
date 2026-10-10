"""A/B 비교: 기록에서 Replay 2개 이상 선택 → 지표/스킬/버프 비교."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from analyzer.report import compare
from config.settings import BASE_DIR
from services import report_export
from ui.widgets import Col, Table, comma, hms, pct, unit


def _fmt_val(v):
    if v is None:
        return '-'
    return unit(v) if abs(v) >= 10_000 else ('%.2f' % v if isinstance(v, float) else comma(v))


def _fmt_diff(v):
    return '-' if v is None else '%+.1f%%' % v


class ComparePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cmp = None
        v = QVBoxLayout(self)
        sp = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel('비교할 Replay 선택 (2개 이상, 첫 번째가 기준 A)'))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.MultiSelection)
        lv.addWidget(self.list, 1)
        self.labels_edit = QLineEdit(placeholderText='이름 지정: 예) 악비셋, 아크메이지 1셋  (쉼표로 구분, 비우면 빌드 라벨)')
        lv.addWidget(self.labels_edit)
        row = QHBoxLayout()
        for text, slot in (('비교', self.run_compare), ('선택 해제', self.list.clearSelection),
                           ('Excel', self.export_xlsx), ('JSON', self.export_json)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lv.addLayout(row)
        sp.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setObjectName('warnLabel')
        rv.addWidget(self.warn)
        self.tabs = QTabWidget()
        self.metric_tbl = Table([Col('항목', lambda r: r['metric'])])
        self.skill_tbl = Table([Col('스킬', lambda r: r['name'])])
        self.buff_tbl = Table([Col('버프', lambda r: r['name'])])
        self.tabs.addTab(self.metric_tbl, '지표')
        self.tabs.addTab(self.skill_tbl, '스킬별 Damage')
        self.tabs.addTab(self.buff_tbl, '버프 가동률')
        rv.addWidget(self.tabs, 1)
        sp.addWidget(right)
        sp.setSizes([380, 900])
        v.addWidget(sp)

    def refresh(self, sessions):
        selected = {i.data(Qt.UserRole) for i in self.list.selectedItems()}
        self.list.clear()
        for s in sessions:
            text = '%s  %s  %s  %s  DPS %s%s' % (
                (s['start_time'] or '')[:16].replace('T', ' '), s['character'] or '?', s['map'] or '-',
                hms(s['window_ms']), unit(s['combat_dps']), '  [%s]' % s['build_name'] if s['build_name'] else '')
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, s['id'])
            it.setData(Qt.UserRole + 1, s)
            self.list.addItem(it)
            it.setSelected(s['id'] in selected)

    def select_ids(self, ids):
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setSelected(it.data(Qt.UserRole) in ids)

    def run_compare(self):
        items = self.list.selectedItems()
        if len(items) < 2:
            self.app.status('Replay를 2개 이상 선택하세요.')
            return
        repo = self.app.analysis.history
        reports = [repo.load_report(i.data(Qt.UserRole)) for i in items]
        typed = [t.strip() for t in self.labels_edit.text().split(',') if t.strip()]
        labels = []
        for n, it in enumerate(items):
            s = it.data(Qt.UserRole + 1)
            labels.append(typed[n] if n < len(typed) else (s['build_name'] or '세팅 %s' % chr(65 + n)))
        self.cmp = compare(reports, labels)
        self.show_compare(self.cmp)

    def show_compare(self, c):
        labels, n = c['labels'], len(c['labels'])
        self.warn.setText(('⚠ ' + ' / '.join(c['warnings'])) if c['warnings'] else '비교 조건 경고 없음')
        mcols = [Col('항목', lambda r: r['metric'], width=160)]
        for i in range(n):
            mcols.append(Col(labels[i], (lambda i: lambda r: r['values'][i])(i), _fmt_val, True, 120))
        for i in range(1, n):
            mcols.append(Col('차이 %s' % labels[i], (lambda i: lambda r: r['diffs'][i])(i), _fmt_diff, True, 100))
        self._replace(0, '지표', Table(mcols), c['rows'])
        scols = [Col('스킬', lambda r: self.app.analysis.gamedata.skills.name(r['skill_id']), width=210,
                     icon=lambda r: self.app.icons.icon('skill_%s' % r['skill_id']))]
        for i in range(n):
            scols.append(Col('%s 피해' % labels[i], (lambda i: lambda r: r['damage'][i])(i), unit, True, 110))
            scols.append(Col('%s DPS' % labels[i], (lambda i: lambda r: r['dps'][i])(i), unit, True, 90))
        for i in range(1, n):
            scols.append(Col('차이 %s' % labels[i], (lambda i: lambda r: r['diffs'][i])(i), _fmt_diff, True, 90))
        self._replace(1, '스킬별 Damage', Table(scols), c['skill_rows'])
        bcols = [Col('버프', lambda r: self.app.display.status(r['efst']).text, width=230,
                     icon=lambda r: self.app.icons.icon('efst_%s' % r['efst']))] + \
                [Col(labels[i], (lambda i: lambda r: r['uptime'][i])(i), pct, True, 100) for i in range(n)]
        self._replace(2, '버프 가동률', Table(bcols), c['buff_rows'])

    def _replace(self, idx, title, table, rows):
        self.tabs.removeTab(idx)
        self.tabs.insertTab(idx, table, title)
        table.set_rows(rows)
        self.tabs.setCurrentIndex(0)

    def export_xlsx(self):
        if self.cmp:
            path, _ = QFileDialog.getSaveFileName(self, 'A/B 비교 Excel', report_export.default_name(
                BASE_DIR, 'AB비교', 'xlsx'), 'Excel (*.xlsx)')
            if path:
                self.app.safe(lambda: report_export.export_compare_xlsx(path, self.cmp), '저장: %s' % path)

    def export_json(self):
        if self.cmp:
            path, _ = QFileDialog.getSaveFileName(self, 'A/B 비교 JSON', report_export.default_name(
                BASE_DIR, 'AB비교', 'json'), 'JSON (*.json)')
            if path:
                self.app.safe(lambda: report_export.export_json(path, self.cmp), '저장: %s' % path)
