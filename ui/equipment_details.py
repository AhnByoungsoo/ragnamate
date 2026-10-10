"""카드·인챈트 효과 버튼과 읽기 전용 설명 창. 네트워크 요청·설정 저장 없음."""
import html
import textwrap

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from services.equipment_details import OptionIndex, clean_description


class OptionDescriptionDialog(QDialog):
    def __init__(self, parent, option):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('카드·인챈트 효과 — ' + option['name'])
        self.resize(540, 460)
        layout = QVBoxLayout(self)
        self.body = QTextBrowser()
        self.body.setOpenLinks(False)
        self.body.setOpenExternalLinks(False)
        e = html.escape
        parts = ['<h2>%s</h2>' % e(option['name'])]
        matches = option['matches']
        if not matches:
            parts.append('<p>저장된 아이템 DB에서 정확히 일치하는 이름을 찾지 못했습니다. 효과 미확인.</p>')
        elif len(matches) > 1:
            parts.append('<p><b>동일한 이름의 아이템이 %d개입니다. 적용 효과를 확정할 수 없습니다.</b><br>'
                         '아래는 후보 설명이며, 모두 적용되는 효과가 아닙니다.</p>' % len(matches))
        for item in matches:
            parts.append('<h3>%s · 아이템 번호 %s</h3><p>%s</p>' % (
                e(item['name']), e(str(item['item_id'])),
                e(clean_description(item.get('description')) or '저장된 효과 설명 없음').replace('\n', '<br>')))
        parts.append('<hr><p>출처: 로컬 아이템 DB(게임 클라이언트 설명).<br>'
                     '조건부·세트 효과는 설명의 조건을 충족할 때 적용됩니다.</p>')
        self.body.setHtml(''.join(parts))
        layout.addWidget(self.body)
        close = QPushButton('닫기')
        close.clicked.connect(self.close)
        layout.addWidget(close)


class EquipmentOptions(QWidget):
    def __init__(self, item_db, parent=None, compact=False, index=None):
        super().__init__(parent)
        self.index = index or OptionIndex(item_db)
        self.compact = compact
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.buttons = []
        self.description_dialog = None

    def set_detail(self, detail):
        while self.box.count():
            widget = self.box.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.buttons = []
        if detail is None:
            return
        names = detail.get('cards') or []
        title = QLabel('카드·인챈트 %d개 · 클릭하면 전체 효과' % len(names))
        title.setWordWrap(True)
        self.box.addWidget(title)
        if not names:
            self.box.addWidget(QLabel('공식 상세에 등록된 카드·인챈트 없음'))
        for name in names:
            option = self.index.option(name)
            matches = option['matches']
            state = '효과 보기' if len(matches) == 1 else ('이름 중복 · 확인 필요' if matches else '설명 미확인')
            if self.compact:
                description = clean_description(matches[0].get('description')) if len(matches) == 1 else ''
                first = description.splitlines()[0] if description else state
                preview = first[:34] + ('…' if len(first) > 34 else '')
                text = textwrap.fill('[%s] %s' % (option['kind'], name), 26) + '\n' + textwrap.fill(preview, 28)
            else:
                text = '[%s]  %s\n%s' % (option['kind'], state, textwrap.fill(name, 24))
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName('%s %s 효과 설명' % (option['kind'], name))
            color = '#D4EFDB' if option['kind'] == '카드' else '#E6DDF9' if option['kind'] == '인챈트' else '#E6E9ED'
            button.setStyleSheet('QPushButton { background:%s; color:#172132; text-align:left; '
                                'padding:7px 10px; font-size:13px; border:1px solid #8798AF; border-radius:7px; }'
                                'QPushButton:hover { background:#FFFFFF; }'
                                'QPushButton:focus { border:2px solid #3278CC; }' % color)
            tip = clean_description(matches[0].get('description')) if len(matches) == 1 else state
            button.setToolTip(html.escape(tip or '저장된 효과 설명 없음'))
            button.clicked.connect(lambda checked=False, opt=option: self.open_description(opt))
            self.buttons.append(button)
            self.box.addWidget(button)
        options = detail.get('options') or []
        if options:
            label = QLabel('랜덤옵션 · 공식 상세 원문\n' + '\n'.join('• ' + value for value in options))
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setStyleSheet('padding:10px; background:#243246; color:#EEF4FC; border-radius:7px;')
            self.box.addWidget(label)

    def open_description(self, option):
        dialog = OptionDescriptionDialog(self, option)
        self.description_dialog = dialog
        dialog.show()
