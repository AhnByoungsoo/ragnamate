"""획득 아이템 표: 아이콘 · 노점가 · 상점가(오버차지) · 어디에 팔지 · 가치 막대.

리플레이 분석의 드랍/수익, Live 탭의 현재 세션, 오늘의 결산이 같은 표를 쓴다. 줄은 core.loot_rows 가 만든다.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate

from core.loot_rows import NPC, VEND
from ui.widgets import SORT_ROLE, Col, Table, comma, hms

GOLD, BLUE, GREEN, DIM = QColor('#FFD479'), QColor('#8FC2FF'), QColor('#7FE0B0'), QColor('#8E97A6')
BAR = QColor(90, 169, 255, 70)
ROW_HEIGHT, ICON = 34, 26


def price(v):
    return '-' if v is None else comma(v)


class ItemIcons(QObject):
    """아이템 아이콘 묶음. 받아 둔 것을 바로 주고, 없는 것은 뒤에서 천천히 받아 온 뒤 changed 를 알린다."""
    changed = Signal()

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        from services.item_icons import ItemIconCache
        self.cache = ItemIconCache(Path(directory))
        self._want, self._icons, self._version = set(), {}, self.cache.version
        self._timer = QTimer(self, interval=1000, timeout=self._poll)
        self._timer.start()

    def icon(self, item_id):
        if item_id not in self._icons:
            path = self.cache.path(item_id) if isinstance(item_id, int) else None
            self._icons[item_id] = QIcon(path) if path else None
        return self._icons[item_id]

    def want(self, item_ids):
        """이 아이템들의 아이콘이 필요하다(이미 있으면 아무 요청도 하지 않는다)."""
        new = {i for i in item_ids if isinstance(i, int) and i > 0} - self._want
        if new:
            self._want |= new
            self.cache.request(self._want)

    def _poll(self):
        if self.cache.version != self._version:
            self._version = self.cache.version
            self._icons = {k: v for k, v in self._icons.items() if v is not None}     # 새로 받은 것을 다시 찾게
            self.changed.emit()

    def stop(self):
        self._timer.stop()
        self.cache.stop()


class ValueBar(QStyledItemDelegate):
    """가치 칸 뒤에 '가장 큰 값 대비' 막대를 깐다 — 어느 아이템이 수익의 대부분인지 한눈에."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.top = 0

    def paint(self, painter, option, index):
        value = index.data(SORT_ROLE)
        if self.top and isinstance(value, (int, float)) and value > 0:
            box = option.rect.adjusted(4, 6, -4, -6)
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(BAR)
            painter.drawRoundedRect(box.x(), box.y(), max(3, int(box.width() * min(1.0, value / self.top))),
                                    box.height(), 4, 4)
            painter.restore()
        super().paint(painter, option, index)


class LootTable(Table):
    def __init__(self, settings=None, icons=None, sell=True, last=False, developer=False):
        """sell=False: 소모품처럼 팔 물건이 아닌 표(상점가·추천 칸을 뺀다). last=True: '마지막 획득' 칸을 붙인다."""
        self.s, self.icons = settings or {}, icons
        cols = [Col('아이템', self._name, width=240, icon=self._icon, tooltip=self._tip, color=self._name_color),
                Col('수량', lambda r: r['quantity'], comma, width=64),
                Col('노점가 ○', lambda r: r['vend'], price, numeric=True, width=112,
                    tooltip=lambda r: r.get('vend_basis') or '공식 노점에 지금 매물도 최근 거래도 없음',
                    color=lambda r: BLUE if r['sell_at'] == VEND else None)]
        if sell:
            cols += [Col('상점가 △', lambda r: r['npc'], price, numeric=True, width=104,
                         tooltip=lambda r: self.npc_tip, color=lambda r: GREEN if r['sell_at'] == NPC else None),
                     Col('팔 곳', lambda r: r['sell_at'], width=58,
                         tooltip=lambda r: '노점가와 상점가 중 더 비싼 쪽',
                         color=lambda r: BLUE if r['sell_at'] == VEND else GREEN if r['sell_at'] == NPC else DIM)]
        value_key = 'value' if sell else 'vend_value'
        cols.append(Col('가치', lambda r: r[value_key], price, numeric=True, width=140,
                        color=lambda r: GOLD if self._rare(r) else None))
        self.value_col = len(cols) - 1
        if last:
            cols.append(Col('마지막 획득', lambda r: r.get('last'), hms, True))
        if developer:
            cols.append(Col('번호', lambda r: r['item_id'], width=90))
        super().__init__(cols, sort_col=len(cols) - 1 if last and not developer else self.value_col)
        self.value_key = value_key
        self.npc_tip = '상점에 팔 때 받는 값 · 기준가는 rAthena 자료(국내 서버와 다를 수 있음)'
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.setIconSize(QSize(ICON, ICON))
        self.setShowGrid(False)
        self.setMouseTracking(True)
        self.setStyleSheet('QTableView::item:hover { background: #2A3444; }')
        blank = QPixmap(ICON, ICON)
        blank.fill(Qt.transparent)
        self._blank = QIcon(blank)
        self.bar = ValueBar(self)
        self.setItemDelegateForColumn(self.value_col, self.bar)
        if icons is not None:
            icons.changed.connect(self.viewport().update)

    # ---------------------------------------------------------------- 줄 표시
    def _rare(self, r):
        return (r.get('vend') or 0) >= int(self.s.get('rare_item_value', 1_000_000) or 0) > 0

    def _name(self, r):
        return ('★ ' if self._rare(r) else '') + str(r['name'])

    def _name_color(self, r):
        return GOLD if self._rare(r) else None

    def _icon(self, r):
        """아이콘이 아직 없어도 빈 자리를 둬서 이름이 같은 줄에 맞게 한다."""
        return (self.icons.icon(r.get('item_id')) if self.icons is not None else None) or self._blank

    def _tip(self, r):
        return '%s\n아이템 번호 %s' % (r['name'], r.get('item_id'))

    def set_overcharge(self, rate):
        self.npc_tip = ('상점에 팔 때 받는 값(오버차지 +%d%% 적용) · 기준가는 rAthena 자료라 국내 서버와 다를 수 있음' % rate
                        if rate else '상점에 팔 때 받는 값(오버차지 없음) · 기준가는 rAthena 자료라 국내 서버와 다를 수 있음')

    def set_rows(self, rows):
        self.bar.top = max((r[self.value_key] or 0 for r in rows), default=0)
        if self.icons is not None:
            self.icons.want(r.get('item_id') for r in rows)
        super().set_rows(rows)
