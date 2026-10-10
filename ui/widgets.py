"""공용 위젯: 가상 테이블 모델(수만 행도 지연 렌더링), KPI 카드, 숫자 표시."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHeaderView, QLabel, QTableView, QVBoxLayout

from core.fmt import comma, hms, pct, sec, unit          # noqa: F401  (기존 import 경로 유지)

try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
    HAS_CHARTS = True
except Exception:          # QtCharts가 없는 환경에서도 동작
    HAS_CHARTS = False

SORT_ROLE = Qt.UserRole + 1


class Col:
    """표 컬럼: 제목, 값 함수, 표시 함수, 숫자 여부"""

    def __init__(self, title, value, fmt=None, numeric=None, width=None, icon=None, tooltip=None, color=None):
        self.title, self.value, self.fmt, self.width = title, value, fmt or (lambda v: '' if v is None else str(v)), width
        self.numeric = numeric if numeric is not None else fmt in (comma, unit, sec, pct)
        self.icon, self.tooltip, self.color = icon, tooltip, color      # color(row) → QColor 또는 None(기본 글자색)


class RowsModel(QAbstractTableModel):
    def __init__(self, cols):
        super().__init__()
        self.cols, self.rows, self._disp = cols, [], {}

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows, self._disp = rows, {}
        self.endResetModel()

    def rowCount(self, p=QModelIndex()):
        return 0 if p.isValid() else len(self.rows)

    def columnCount(self, p=QModelIndex()):
        return len(self.cols)

    def headerData(self, s, o, role=Qt.DisplayRole):
        return self.cols[s].title if o == Qt.Horizontal and role == Qt.DisplayRole else None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        c = self.cols[idx.column()]
        if role == Qt.DecorationRole and c.icon:
            return c.icon(self.rows[idx.row()])
        if role == Qt.ToolTipRole and c.tooltip:
            return c.tooltip(self.rows[idx.row()])
        if role == Qt.ForegroundRole and c.color:
            return c.color(self.rows[idx.row()])
        if role == Qt.DisplayRole:
            k = (idx.row(), idx.column())
            if k not in self._disp:        # 보이는 셀만 계산(지연 렌더링)
                self._disp[k] = c.fmt(c.value(self.rows[idx.row()]))
            return self._disp[k]
        if role == SORT_ROLE:
            v = c.value(self.rows[idx.row()])
            if v is None:
                return float('-inf') if c.numeric else ''
            return v if c.numeric else str(v)
        if role == Qt.TextAlignmentRole and c.numeric:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class Table(QTableView):
    """정렬 가능한 가상 테이블. filter_fn(row)로 행 필터."""

    def __init__(self, cols, sort_col=None, desc=True):
        super().__init__()
        self.model_ = RowsModel(cols)
        self.proxy = _Proxy()
        self.proxy.setSourceModel(self.model_)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.setIconSize(QSize(20, 20))
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(22)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        for i, c in enumerate(cols):
            if c.width:
                self.setColumnWidth(i, c.width)
        self._sort = (sort_col, desc)

    def set_rows(self, rows):
        self.model_.set_rows(rows)
        if self._sort[0] is not None:
            self.sortByColumn(self._sort[0], Qt.DescendingOrder if self._sort[1] else Qt.AscendingOrder)
        else:                              # 원래 순서 유지(헤더 클릭 시에만 정렬)
            self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
            self.proxy.sort(-1)

    def set_filter(self, fn):
        self.proxy.fn = fn
        self.proxy.invalidateFilter()

    def selected_rows(self):
        return [self.model_.rows[self.proxy.mapToSource(i).row()] for i in self.selectionModel().selectedRows()]

    def row_at(self, index):
        return self.model_.rows[self.proxy.mapToSource(index).row()]


class _Proxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setSortRole(SORT_ROLE)
        self.fn = None

    def filterAcceptsRow(self, r, parent):
        return self.fn is None or self.fn(self.sourceModel().rows[r])

    def lessThan(self, a, b):
        va, vb = a.data(SORT_ROLE), b.data(SORT_ROLE)
        try:
            return va < vb
        except TypeError:
            return str(va) < str(vb)


class Kpi(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName('card')
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        self.title = QLabel(title)
        self.title.setObjectName('cardTitle')
        self.value = QLabel('-')
        self.value.setObjectName('cardValue')
        self.sub = QLabel('')
        self.sub.setObjectName('cardTitle')
        for w in (self.title, self.value, self.sub):
            v.addWidget(w)

    def set(self, text, sub=''):
        self.value.setText(text)
        self.sub.setText(sub)


def line_chart(title):
    """(chart_view, set_series(dict name→[(x_sec, y)]), mark(x_sec))"""
    if not HAS_CHARTS:
        lbl = QLabel('QtCharts 없음: 그래프 대신 표를 사용하세요')
        return lbl, (lambda *_: None)
    chart = QChart()
    chart.setTitle(title)
    chart.legend().setVisible(True)
    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    view.setMinimumHeight(220)

    def set_series(series):
        chart.removeAllSeries()
        for ax in chart.axes():
            chart.removeAxis(ax)
        ax_x, ax_y = QValueAxis(), QValueAxis()
        ax_x.setTitleText('초')
        ax_y.setLabelFormat('%.3g')
        chart.addAxis(ax_x, Qt.AlignBottom)
        chart.addAxis(ax_y, Qt.AlignLeft)
        ymax, xmax = 1, 1
        for name, pts in series.items():
            s = QLineSeries()
            s.setName(name)
            for x, y in pts:
                s.append(x, y)
                ymax, xmax = max(ymax, y), max(xmax, x)
            chart.addSeries(s)
            s.attachAxis(ax_x)
            s.attachAxis(ax_y)
        ax_x.setRange(0, xmax)
        ax_y.setRange(0, ymax * 1.1)
    return view, set_series
