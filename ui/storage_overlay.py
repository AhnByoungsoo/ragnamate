"""창고 시세 Overlay: 지금 들고 있는 것과 그 시세를 게임 옆에 띄운다 (표시 전용).

가격은 기존 로컬 시세 DB/PriceProvider 값을 그대로 쓴다. 게임 메모리나 노점 창을 읽지 않는다.
시세가 없는 아이템은 0원이 아니라 '-'로 둔다.
"""
import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout

from ui import overlay_styles
from ui.overlay import OverlayWindow
from ui.widgets import Col, Table, comma, unit
from ui.completer import attach_completer, set_words

PRICE_COLS = {'sale': '판매 최저가', 'average': '거래 평균가'}


def ago(ts):
    if not ts:
        return '-'
    days = (datetime.datetime.now() - datetime.datetime.fromtimestamp(ts)).days
    return '오늘' if days <= 0 else '%d일 전' % days


class StorageOverlay(OverlayWindow):
    def __init__(self, settings, on_refresh=None, on_item=None):
        super().__init__(settings, 'storage', '창고 시세')
        self.on_refresh = on_refresh or (lambda: None)
        self.on_item = on_item or (lambda item_id: None)
        self.setStyleSheet('QWidget{background:#151a22;color:#e6e6e6;font-family:"Malgun Gothic";}'
                           'QLabel#title{color:#9ecbff;font-weight:bold;} QLineEdit{background:#1f2430;border:1px solid #333;}'
                           'QTableView{background:#151a22;gridline-color:#2c3440;}')
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 6, 10, 8)
        head = QHBoxLayout()
        self.title = QLabel('창고 시세')
        self.title.setObjectName('title')
        head.addWidget(self.title, 1)
        b = QPushButton('시세 새로고침')
        b.clicked.connect(lambda: self.on_refresh())
        head.addWidget(b)
        v.addLayout(head)
        self.total = QLabel()                  # 합계 먼저: 표시한 아이템 예상 금액 합계를 크게
        self.total.setStyleSheet('font-size: 20px; font-weight: 700; color: #ffd479;')
        v.addWidget(self.total)
        self.total.setVisible(overlay_styles.get(settings, 'storage') == 'summary')
        row = QHBoxLayout()
        self.search = QLineEdit(placeholderText='아이템 검색')
        row.addWidget(self.search, 1)
        self.only = QCheckBox('팔 만한 것만')
        self.only.setChecked(bool(settings.get('storage_overlay_only_sellable', True)))
        row.addWidget(self.only)
        v.addLayout(row)
        self.table = Table(self._cols(), sort_col=None)
        self.table.setMinimumHeight(220)
        self.table.setToolTip('더블클릭하면 창고 분석 탭에서 그 아이템을 찾아 줍니다.')
        self.table.doubleClicked.connect(lambda idx: self.on_item(self.table.row_at(idx)['item_id']))
        v.addWidget(self.table, 1)
        self.foot = QLabel()
        v.addWidget(self.foot)
        self.rows = []
        self.search.textChanged.connect(self._changed)
        self.search_completer = attach_completer(self.search)
        self.only.toggled.connect(self._only_changed)
        self.resize(int(430 * float(settings.get('hud_scale', 1.0))), 340)
        self._reload = lambda: None
        self._fit_width()

    def _cols(self):
        if overlay_styles.get(self.s, 'storage') == 'summary':        # 합계 먼저: 아이템·수량·예상금액만
            return [Col('아이템', lambda r: r['name'], width=190), Col('수량', lambda r: r['quantity'], comma, width=45),
                    Col('예상금액', lambda r: r['value'], unit, width=90)]
        prices = self.s.get('storage_overlay_prices') or ['sale']
        cols = [Col('아이템', lambda r: r['name'], width=140), Col('수량', lambda r: r['quantity'], comma, width=45),
                Col('위치', lambda r: r['locations'], width=60)]
        if 'sale' in prices:
            cols.append(Col(PRICE_COLS['sale'], lambda r: r['unit'], comma, width=85))
        if 'average' in prices:
            cols.append(Col(PRICE_COLS['average'], lambda r: r['average'], comma, width=85))
        cols += [Col('예상금액', lambda r: r['value'], unit, width=80), Col('시세 시각', lambda r: r['updated_at'], ago, width=55)]
        return cols

    def _fit_width(self):
        """컬럼이 잘리지 않을 만큼만 넓힌다(사용자가 더 키운 크기는 그대로 둔다)."""
        need = sum(c.width or 80 for c in self.table.model_.cols) + 36
        if self.width() < need:
            self.resize(need, self.height())

    # ------------------------------------------------------------------ 표시
    def set_rows(self, rows, total=None, note=''):
        self.rows = rows
        set_words(self.search_completer, [r['name'] for r in rows])
        self.table.set_rows(rows)
        shown = sum(r['value'] or 0 for r in rows)
        self.total.setText('%s Z' % comma(shown))
        self.total.setVisible(overlay_styles.get(self.s, 'storage') == 'summary')
        self.foot.setText('표시 %d종 %s Z%s' % (len(rows), comma(shown),
                                              (' · 전체 %s Z' % comma(total)) if total is not None else '')
                          + (' · ' + note if note else ''))

    def query(self):
        return self.search.text()

    def only_sellable(self):
        return self.only.isChecked()

    def bind(self, reload_fn):
        """검색어/체크박스가 바뀌면 다시 계산하도록 연결."""
        self._reload = reload_fn

    def _changed(self, *_):
        self._reload()

    def _only_changed(self, on):
        self.s['storage_overlay_only_sellable'] = bool(on)
        self._save()
        self._reload()

    # ------------------------------------------------------------------ 오른쪽 클릭 메뉴
    def contextMenuEvent(self, e):
        m = QMenu(self)
        overlay_styles.add_menu(m, self.s, 'storage', self.apply_style)
        price = m.addMenu('표시할 가격')
        current = list(self.s.get('storage_overlay_prices') or ['sale'])
        for key, label in PRICE_COLS.items():
            a = price.addAction(label, lambda k=key: self._toggle_price(k))
            a.setCheckable(True)
            a.setChecked(key in current)
        op = m.addMenu('투명도')
        for v in (1.0, 0.9, 0.8, 0.7):
            op.addAction('%d%%' % (v * 100), lambda v=v: self.set_opacity(v))
        m.addSeparator()
        a = m.addAction('위치 고정', lambda: self.set_locked(not self.locked))
        a.setCheckable(True)
        a.setChecked(self.locked)
        m.addAction('숨기기', self.hide)
        m.exec(e.globalPos())

    def apply_style(self):
        """모양이 바뀌면 열을 다시 만든다(표 / 합계 먼저)."""
        self._rebuild()

    def _toggle_price(self, key):
        cur = list(self.s.get('storage_overlay_prices') or ['sale'])
        cur = [k for k in cur if k != key] if key in cur else cur + [key]
        self.s['storage_overlay_prices'] = cur or ['sale']
        self._save()
        self._rebuild()

    def _rebuild(self):
        self.table.model_.cols = self._cols()
        self.table.model_.set_rows(self.rows)
        for i, c in enumerate(self.table.model_.cols):
            if c.width:
                self.table.setColumnWidth(i, c.width)
        self._fit_width()
        self._reload()

    def _save(self, **kv):
        if kv:                                   # 위치·고정·투명도는 공용 오버레이 저장(덮어써서 '위치 고정'이 오류 났었다)
            return super()._save(**kv)
        try:
            self.s.save()
        except Exception:
            pass

    def follow(self, client):
        """게임 클라이언트 오른쪽 아래(사용자가 옮긴 뒤에는 그 위치 유지)."""
        if (self.s.get('overlays') or {}).get(self.key, {}).get('pos') or not client:
            return
        x, y, w, h = client
        self.move(x + w - self.width() - 12, y + h - self.height() - 12)

    def mousePressEvent(self, e):
        if self.childAt(e.position().toPoint()) is None:     # 빈 곳을 잡아야 창이 끌린다
            super().mousePressEvent(e)
