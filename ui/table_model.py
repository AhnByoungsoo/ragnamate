"""아이템 테이블 모델 + 필터/정렬 프록시."""
import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from core.analysis import EQUIP_NOTE, is_sellable

COLS = ['위치', '아이템명', '아이템 ID', '수량', '제련', '카드/옵션', '최저가', '중앙가', '평균가', '노점수',
        '예상금액', '시세 갱신시간']
C_SOURCE, C_NAME, C_ID, C_QTY, C_REFINE, C_CARDS, C_LOW, C_MED, C_AVG, C_CNT, C_VALUE, C_TIME = range(len(COLS))
NUMERIC = {C_ID, C_QTY, C_REFINE, C_LOW, C_MED, C_AVG, C_CNT, C_VALUE}
SORT_ROLE = Qt.UserRole + 1


def comma(v):
    return '' if v is None else '{:,}'.format(v)


class Formatter:
    def __init__(self, item_db):
        self.item_db = item_db

    def card_text(self, e):
        return ', '.join(self.item_db.name(c) for c in e.cards)

    def option_text(self, e):
        return ', '.join('옵션#%d %+d' % (o['id'], o['value']) for o in e.options)

    def extras(self, e):
        return ' / '.join(t for t in (self.card_text(e), self.option_text(e)) if t)


class ItemTableModel(QAbstractTableModel):
    def __init__(self, fmt):
        super().__init__()
        self.fmt = fmt
        self.rows = []
        self._cache = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self._cache = [self._fmt_row(r) for r in rows]
        self.endResetModel()

    def _fmt_row(self, r):
        e, p = r.entry, r.price
        name = ('⚠ ' + e.name) if e.is_equip else e.name
        if e.identified is False:
            name += ' (미감정)'
        t = datetime.datetime.fromtimestamp(p.updated_at).strftime('%m-%d %H:%M') if p and p.updated_at and p.has_price else ''
        disp = [e.source, name, str(e.item_id), comma(e.quantity), ('+%d' % e.refine) if e.refine else '-',
                self.fmt.extras(e) or '-', comma(p and p.lowest_price), comma(p and p.median_price),
                comma(p and p.average_price), comma(p.listing_count) if p and p.has_price else '',
                comma(r.value), t]
        keys = [e.source, e.name, e.item_id, e.quantity, e.refine or 0, disp[C_CARDS],
                p.lowest_price if p and p.lowest_price is not None else -1,
                p.median_price if p and p.median_price is not None else -1,
                p.average_price if p and p.average_price is not None else -1,
                p.listing_count if p else -1, r.value if r.value is not None else -1,
                p.updated_at if p and p.has_price else 0]
        return disp, keys

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if role == Qt.DisplayRole:
            return self._cache[r][0][c]
        if role == SORT_ROLE:
            return self._cache[r][1][c]
        if role == Qt.TextAlignmentRole and c in NUMERIC:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            e = self.rows[r].entry
            tips = []
            if e.is_equip:
                tips.append(EQUIP_NOTE)
            if c == C_CARDS and self._cache[r][0][C_CARDS] != '-':
                tips.append(self._cache[r][0][C_CARDS])
            if c in (C_LOW, C_MED, C_AVG):
                tips.append('더블클릭: 시세 직접 입력')
            p = self.rows[r].price
            if p and p.highest_buy:
                tips.append('구매노점 최고가: %s' % comma(p.highest_buy))
            return '\n'.join(tips) or None
        if role == Qt.ForegroundRole and not self.rows[r].has_price and c >= C_LOW:
            return QColor(128, 128, 128)
        return None


class ItemFilterProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setSortRole(SORT_ROLE)
        self.location = '전체'
        self.price_state = 'all'      # all | priced | unpriced
        self.sellable_only = False
        self.sell_min = 100_000
        self.min_value = 0
        self.text = ''

    def refresh(self):
        self.invalidateFilter()

    def filterAcceptsRow(self, src_row, parent):
        r = self.sourceModel().rows[src_row]
        e = r.entry
        if self.location != '전체':
            if self.location == '창고':
                if not e.source.startswith('창고'):
                    return False
            elif e.source != self.location:
                return False
        if self.price_state == 'priced' and not r.has_price:
            return False
        if self.price_state == 'unpriced' and r.has_price:
            return False
        if self.sellable_only and not is_sellable(r, self.sell_min):
            return False
        if self.min_value and (r.value or 0) < self.min_value:
            return False
        if self.text:
            t = self.text
            if t not in e.name.lower() and t != str(e.item_id) and not str(e.item_id).startswith(t):
                return False
        return True
