"""G HUB에서 매크로 가져오기: 체크한 것만 라그메이트 매크로로 추가(트리거 키를 정할 때까지 꺼 둔 채로)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QVBoxLayout)

from core import ghub_import as G
from core import macros as M
from ui.theme import CARD_LIST_QSS


def _summary(steps, limit=8):
    parts = []
    for s in steps:
        if s['type'] == 'wait':
            parts.append('%dms' % s['ms'])
        elif s['type'] in ('tap', 'down', 'up'):
            parts.append(s['key'] + {'down': '↓', 'up': '↑'}.get(s['type'], ''))
        else:
            parts.append({'click': '좌클릭', 'rclick': '우클릭'}[s['type']])
    return ' → '.join(parts[:limit]) + (' …' if len(parts) > limit else '')


class GhubImportDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle('G HUB에서 매크로 가져오기')
        self.resize(720, 520)
        v = QVBoxLayout(self)
        info = QLabel('G HUB 설정 파일을 읽기만 합니다(G HUB 설정은 바뀌지 않음). 가져온 매크로는 꺼진 상태로 추가되니, '
                      '트리거 키를 정한 뒤 [사용]을 켜세요. G HUB의 마우스 버튼 배정은 따라오지 않습니다.')
        info.setWordWrap(True)
        info.setObjectName('cardTitle')
        v.addWidget(info)
        self.list = QListWidget()
        self.list.setStyleSheet(CARD_LIST_QSS)
        v.addWidget(self.list, 1)
        self.items = []
        try:
            self.items = G.ghub_macros(G.read_settings())
        except Exception as e:
            self.list.addItem('G HUB 설정을 읽지 못했습니다: %s' % e)
        have = {m['name'] for m in page.macros}
        for g in self.items:
            text = '%s   ·  %s  ·  G HUB 프로필: %s\n%s' % (g['name'], g['kind_label'], g['profile'],
                                                         _summary(g['steps']) if g['importable'] else g['reason'])
            if g['skipped']:
                text += '\n건너뜀: ' + ', '.join(g['skipped'])
            if g['name'] in have:
                text += '\n(같은 이름의 매크로가 이미 있음)'
            it = QListWidgetItem(text)
            if g['importable']:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked if g['name'] in have else Qt.Checked)
            else:
                it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        if not self.items and not self.list.count():
            self.list.addItem('G HUB에 직접 만든 순서 매크로가 없습니다.')
        bb = QDialogButtonBox()
        self.b_ok = bb.addButton('가져오기', QDialogButtonBox.AcceptRole)
        self.b_ok.setProperty('primary', True)
        bb.addButton('취소', QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._import)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def checked(self):
        return [g for i, g in enumerate(self.items)
                if g['importable'] and self.list.item(i).checkState() == Qt.Checked]

    def _import(self):
        picked = self.checked()
        if not picked:
            QMessageBox.information(self, '가져오기', '가져올 매크로를 체크하세요.')
            return
        for g in picked:
            self.page.add_macro(G.to_macro(g))
        QMessageBox.information(self, '가져오기', 'G HUB 매크로 %d개를 가져왔습니다.\n'
                                '각 매크로의 트리거 키를 정하고 [사용]을 켜세요 (지금은 꺼짐).' % len(picked))
        self.accept()
