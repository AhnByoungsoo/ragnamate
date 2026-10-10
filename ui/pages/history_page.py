"""기록: 분석한 Replay 세션 목록 + 사냥터(맵) 누적 통계."""
import os

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from ui.widgets import Col, Table, comma, hms, unit


class HistoryPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('분석 기록 (같은 파일은 한 번만 저장, 다시 분석하면 갱신)'), 1)
        for text, slot in (('열기', self.open_selected), ('A/B 비교로', self.to_compare), ('삭제', self.delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        sp = QSplitter(Qt.Vertical)
        self.tbl = Table([
            Col('녹화', lambda r: (r['start_time'] or '')[:16].replace('T', ' '), width=130),
            Col('파일', lambda r: r['filename'], width=140), Col('캐릭터', lambda r: r['character'], width=120),
            Col('직업', lambda r: r['job'], width=100), Col('Lv', lambda r: r['level'], comma, width=45),
            Col('맵', lambda r: r['map'], width=120), Col('구간', lambda r: r['window_ms'], hms, True, 75),
            Col('전투 DPS', lambda r: r['combat_dps'], unit, width=90), Col('처치', lambda r: r['kills'], comma, width=60),
            Col('경험치/시간', lambda r: r['base_exp_per_hour'], unit, width=100),
            Col('순이익', lambda r: r['profit'], comma, width=100), Col('수익/시간', lambda r: r['profit_per_hour'], unit, width=100),
            Col('빌드', lambda r: r['build_name'], width=110), Col('메모', lambda r: r['build_memo'])], sort_col=0)
        self.tbl.doubleClicked.connect(lambda idx: self.open_row(self.tbl.row_at(idx)))
        sp.addWidget(self.tbl)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(QLabel('사냥터 통계 (사냥 구간만 누적)'))
        self.map_tbl = Table([
            Col('맵', lambda r: self.app.analysis.gamedata.maps.name(r['map']), width=200),
            Col('세션', lambda r: r['sessions'], comma, width=50), Col('총 시간', lambda r: r['total_ms'], hms, True, 80),
            Col('처치/시간', lambda r: r['kills_per_hour'], comma, width=90),
            Col('경험치/시간', lambda r: r['exp_per_hour'], unit, width=100), Col('Zeny/시간', lambda r: r['zeny_per_hour'], unit, width=100),
            Col('수익/시간', lambda r: r['profit_per_hour'], unit, width=100),
            Col('드랍가치', lambda r: r['loot_value'], comma, width=110), Col('소모품', lambda r: r['consumable_cost'], comma)],
            sort_col=2)
        bl.addWidget(self.map_tbl)
        sp.addWidget(box)
        v.addWidget(sp, 1)

    def refresh(self, sessions):
        self.tbl.set_rows(sessions)
        try:
            self.map_tbl.set_rows(self.app.analysis.history.map_stats())
        except Exception:
            self.map_tbl.set_rows([])

    def open_selected(self):
        rows = self.tbl.selected_rows()
        if rows:
            self.open_row(rows[0])

    def open_row(self, r):
        path = r['file_path']
        if path and os.path.exists(path):
            self.app.load_replay(path)          # 원본이 있으면 다시 파싱(타임라인 포함)
        else:
            rep = self.app.analysis.history.load_report(r['id'])
            if rep:
                self.app.show_stored_report(rep)

    def to_compare(self):
        ids = [r['id'] for r in self.tbl.selected_rows()]
        self.app.open_compare(ids)

    def delete(self):
        rows = self.tbl.selected_rows()
        if rows and QMessageBox.question(self, '기록 삭제', '%d개 기록을 삭제할까요? (Replay 파일은 그대로)' % len(rows)) \
                == QMessageBox.Yes:
            for r in rows:
                self.app.analysis.history.delete(r['id'])
            self.app.refresh_history()
