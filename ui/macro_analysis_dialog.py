"""G HUB 매크로 분석 창: 지금 불러온 리플레이 기준으로 매크로의 키 간격 ↔ 스킬 재사용 대기·실패를 나란히 본다(읽기만)."""
import os

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from core import ghub_import as G
from core import macro_analysis as A

COLS = ('매크로', '키', '스킬바 칸', '스킬', '누름 간격', '재사용 대기', '실제 연달아 쓴 간격', '사용', '딜레이 중 실패', '분석')


def _ms(v):
    return '%dms' % v if v else '-'


class MacroAnalysisDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle('G HUB 매크로 분석')
        self.resize(1480, 600)
        v = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        note = QLabel('키 → 칸은 게임 폴더 savedata/UserKeys.lua, 칸 → 스킬은 리플레이의 스킬바(0x0b20), 재사용 대기·실패는 '
                      '리플레이의 서버 기록입니다. 매크로를 바꾸거나 게임에 입력하지 않습니다 — 간격 조정은 G HUB에서 직접 하세요.')
        note.setWordWrap(True)
        note.setObjectName('cardTitle')
        v.addWidget(note)
        h = QHBoxLayout()
        b = QPushButton('다시 분석')
        b.clicked.connect(self.reload)
        h.addStretch(1)
        h.addWidget(b)
        v.addLayout(h)
        self.reload()

    def reload(self):
        self.table.setRowCount(0)
        res = getattr(getattr(self.app, 'replay_page', None), 'result', None)
        pr = getattr(res, 'parsed', None)
        if pr is None:
            self.info.setText('먼저 리플레이를 불러오세요(상단 [최신 Replay 불러오기]). 그 리플레이의 스킬바와 스킬 기록으로 분석합니다.')
            return
        try:
            keys = A.read_userkeys(self.app.s['ro_dir'])
        except OSError as e:
            self.info.setText('게임 폴더의 savedata/UserKeys.lua 를 읽지 못했습니다: %s' % e)
            return
        try:
            macros = [m for m in G.ghub_macros(G.read_settings()) if m['importable']]
        except Exception as e:
            self.info.setText('G HUB 설정을 읽지 못했습니다(G HUB 가 설치돼 있어야 합니다): %s' % e)
            return
        bar, stats = A.hotbar(pr.packets), A.skill_stats(pr.events, pr.player_aid)
        name = self.app.analysis.gamedata.skills.name
        self.info.setText('기준 리플레이: %s · %s · %.1f분 · 스킬바 %s · G HUB 매크로 %d개'
                          % (os.path.basename(pr.path), pr.character or '?', pr.duration_ms / 60000,
                             '%d칸' % len(bar) if bar else '기록 없음(이 리플레이에는 스킬바 패킷이 없음)', len(macros)))
        for m in macros:
            for r in A.analyze(m, keys, bar, stats, name, self.app.item_db.name):
                row = self.table.rowCount()
                self.table.insertRow(row)
                for c, val in enumerate((m['name'], r['key'], r['slot'], r['what'], _ms(r['interval_ms']),
                                         _ms(r['cooldown_ms']), _ms(r['gap_ms']),
                                         '-' if r['uses'] is None else str(r['uses']),
                                         '-' if r['blocked'] is None else str(r['blocked']), r['verdict'])):
                    item = QTableWidgetItem(val)
                    item.setToolTip(val)
                    self.table.setItem(row, c, item)
        self.table.resizeRowsToContents()
