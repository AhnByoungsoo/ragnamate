"""검색된 매물들을 카드·인챈트와 함께 펼쳐 비교하는 화면."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextBrowser, QVBoxLayout, QWidget

from services.equipment_details import OptionIndex, item_description, spec_text
from ui.equipment_details import EquipmentOptions
from ui.widgets import comma


def label(text, style=''):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if style:
        widget.setStyleSheet(style)
    return widget


class VendResultCard(QFrame):
    def __init__(self, row, item_db, index, map_name, action, parent=None):
        super().__init__(parent)
        self.row, self.map_name = row, map_name
        self.setObjectName('vendResultCard')
        self.setStyleSheet('QFrame#vendResultCard { background:#1B2431; border:1px solid #42516A; border-radius:10px; }')
        self.setMinimumWidth(370)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        top = QHBoxLayout()
        icon = label('아이템', 'background:#2C3E56; color:#CFE3FF; font-size:11px; border-radius:8px;')
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(42, 42)
        path = Path(__file__).resolve().parents[1]/'data/item_icons'/('%s.png' % row['item_id'])
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                icon.setPixmap(pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        top.addWidget(icon)
        self.title = label(row['name'], 'color:#F4F7FC; font-size:15px; font-weight:600;')
        top.addWidget(self.title, 1)
        layout.addLayout(top)
        specs = QHBoxLayout()
        for text in spec_text(row).split(' · '):
            badge = label(text, 'background:#D7E3F2; color:#132034; padding:5px 7px; border-radius:5px; font-size:12px;')
            specs.addWidget(badge)
        specs.addStretch(1)
        layout.addLayout(specs)
        self.price = label('%s Z' % comma(row['price']), 'color:#FFD479; font-size:23px; font-weight:700;')
        layout.addWidget(self.price)
        self.market = label('', 'color:#C1CEDE; font-size:12px;')
        layout.addWidget(self.market)
        self.location = label('', 'color:#C1CEDE; font-size:12px;')
        layout.addWidget(self.location)
        layout.addWidget(label('장비 자체 옵션 · 제련·등급·세트 조건 원문', 'color:#AECBFA; font-weight:600; font-size:12px;'))
        self.base_options = QTextBrowser()
        self.base_options.setOpenLinks(False)
        self.base_options.setOpenExternalLinks(False)
        self.base_options.setPlainText(item_description(item_db, row['item_id']))
        self.base_options.setMinimumHeight(180)
        self.base_options.setMaximumHeight(220)
        self.base_options.setStyleSheet('background:#101A28; color:#EAF1FD; border:1px solid #415977; padding:8px; font-size:13px;')
        self.base_options.setAccessibleName('장비 자체 옵션 전체 설명')
        layout.addWidget(self.base_options)
        self.loading = label('', 'color:#8FC7FF; padding:10px; background:#243246; border-radius:6px;')
        layout.addWidget(self.loading)
        self.options = EquipmentOptions(item_db, compact=True, index=index)
        layout.addWidget(self.options)
        layout.addStretch(1)
        buttons = QGridLayout()
        for i, (name, text) in enumerate((('compare', '내 장비·피해 비교'), ('history', '시세 조회'),
                                         ('price', '가격 복사'), ('watch', '관심 추가'))):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, key=name: action(row, key))
            buttons.addWidget(button, i // 2, i % 2)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        row, detail = self.row, self.row.get('_detail')
        sale = row['kind'] == 'sale'
        text = '%s · %s · 수량 %s' % (row['server'], '판매' if sale else '구매(사겠다는 노점)', comma(row['qty']))
        if row.get('avg7'):
            text += '\n7일 평균 %s Z' % comma(int(row['avg7']))
            if row.get('vs') is not None:
                text += ' · 평균 대비 %+.1f%%' % row['vs']
        else:
            text += '\n저장된 7일 시세 없음'
        self.market.setText(text)
        where = (detail or {}).get('map') or ''
        shop = row.get('shop') or '상점 이름 없음'
        self.location.setText('상점: %s\n위치: %s' % (shop, self.map_name(where) if where else '자동 조회 중'))
        if detail is not None:
            self.loading.hide()
            self.options.set_detail(detail)
            self.options.show()
        else:
            state = row.get('_detail_state', 'pending')
            messages = {'pending': '카드·인챈트 자동 조회 대기 중…', 'loading': '카드·인챈트 자동 조회 중…',
                        'unavailable': '상세 번호가 없어 카드·인챈트를 확인할 수 없습니다.',
                        'stopped': '자동 조회 중지됨 · 상단에서 다시 시작할 수 있습니다.',
                        'error': '상세 확인 실패: ' + row.get('_detail_error', ''),
                        'rate_limited': '공식 요청 제한 · 5분 대기 후 자동 재개합니다.'}
            self.loading.setText(messages.get(state, messages['pending']))
            self.loading.show()
            self.options.hide()
            if state in ('unavailable', 'error', 'stopped', 'rate_limited'):
                self.location.setText('상점: %s\n위치: 미확인' % shop)


class VendResultCards(QScrollArea):
    def __init__(self, item_db, map_name, action, parent=None):
        super().__init__(parent)
        self.item_db, self.map_name, self.action = item_db, map_name, action
        self.index = OptionIndex(item_db)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(6, 6, 6, 6)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignTop)
        self.setWidget(self.content)
        self.cards, self.columns = [], 0
        self.empty = label('검색하면 모든 매물의 카드·인챈트를 자동으로 보여 줍니다.')
        self.grid.addWidget(self.empty, 0, 0)

    def set_rows(self, rows):
        for card in self.cards:
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = [VendResultCard(row, self.item_db, self.index, self.map_name, self.action) for row in rows]
        self.empty.setVisible(not rows)
        self.reflow()

    def reflow(self):
        columns = max(1, min(3, (self.viewport().width() - 12) // 390))
        for column in range(max(columns, self.columns)):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        self.columns = columns
        for i, card in enumerate(self.cards):
            self.grid.removeWidget(card)
            self.grid.addWidget(card, i // columns, i % columns, Qt.AlignTop)

    def refresh(self, row):
        for card in self.cards:
            if card.row is row:
                card.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'cards'):
            self.reflow()
