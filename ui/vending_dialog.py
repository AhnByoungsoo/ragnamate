"""노점 시세 비교: 공식 홈페이지의 지금 노점 · 과거 거래 시세를 받아 내 창고 아이템과 비교한다."""
import datetime
import os
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from config.settings import BASE_DIR
from core.models import Listing
from services import gnjoy_market as G
from ui.theme import T
from ui.widgets import Col, Table, comma

try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
    HAS_CHARTS = True
except ImportError:                                                  # pragma: no cover
    HAS_CHARTS = False

PERIODS = [(1, '최근 10일 (빠름)'), (3, '최근 30일'), (13, '최근 120일 (느림)')]
FRESH_SEC = 30 * 60                                                  # 30분 안에 받은 것은 다시 받지 않음


def _pct(v):
    return '-' if v is None else '%+.0f%%' % v


def _when(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M') if ts else '-'


class _Bridge(QObject):
    item_done = Signal(int)
    progress = Signal(str)
    finished = Signal(str)


class VendingDialog(QDialog):
    def __init__(self, app, entries):
        """entries: [(item_id, name, 보유 수량)] — 창고 표에서 넘겨받은 아이템."""
        super().__init__(app)
        self.app = app
        self.setWindowTitle('노점 시세 비교 — 공식 홈페이지 거래 정보')
        self.resize(1250, 760)
        self.store = G.VendingStore(os.path.join(BASE_DIR, 'data', 'vending.db'))
        self.client = G.shared_client()
        self.entries = {iid: dict(item_id=iid, name=name, qty=qty) for iid, name, qty in entries}
        self._stop = threading.Event()
        self._worker = None
        self.bridge = _Bridge()
        self.bridge.item_done.connect(self._item_done)
        self.bridge.progress.connect(lambda t: self.status.setText(t))
        self.bridge.finished.connect(self._finished)

        v = QVBoxLayout(self)
        note = QLabel('출처: ro.gnjoy.com 아이템 거래 정보(노점 판매·구매 목록, 날짜별 노점 거래 최저/평균/최고가). '
                      '홈페이지가 요청이 많으면 막으므로 천천히(약 2.5초 간격) 받습니다. 받은 기록은 이 PC에 계속 쌓입니다.')
        note.setWordWrap(True)
        note.setObjectName('cardTitle')
        v.addWidget(note)
        top = QHBoxLayout()
        self.server = QComboBox()
        for sid, name in G.SERVERS.items():
            self.server.addItem(name, sid)
        self.server.setCurrentIndex(max(0, self.server.findData(self.store.meta('server', '129'))))
        self.server.currentIndexChanged.connect(self._server_changed)
        self.period = QComboBox()
        for pages, label in PERIODS:
            self.period.addItem(label, pages)
        top.addWidget(QLabel('서버'))
        top.addWidget(self.server)
        top.addWidget(QLabel('과거 시세'))
        top.addWidget(self.period)
        self.b_sel = QPushButton('선택한 아이템 받기')
        self.b_sel.setProperty('primary', True)
        self.b_sel.clicked.connect(lambda: self._fetch(self.table.selected_rows()))
        self.b_all = QPushButton('목록 전체 받기')
        self.b_all.clicked.connect(self._fetch_all)
        self.b_stop = QPushButton('멈추기')
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop.set)
        for b in (self.b_sel, self.b_all, self.b_stop):
            top.addWidget(b)
        top.addStretch(1)
        v.addLayout(top)
        self.status = QLabel()
        v.addWidget(self.status)

        sp = QSplitter(Qt.Vertical)
        self.table = Table([
            Col('아이템', lambda r: r['name'], width=200), Col('보유', lambda r: r['qty'], comma, width=60),
            Col('지금 최저가', lambda r: r['cmp'].get('now_min'), comma, width=100),
            Col('판매 노점', lambda r: r['cmp'].get('sellers'), comma, width=70),
            Col('7일 평균 거래가', lambda r: r['cmp'].get('avg7') and int(r['cmp']['avg7']), comma, width=110),
            Col('받은 기간 평균', lambda r: r['cmp'].get('avg120') and int(r['cmp']['avg120']), comma, width=110),
            Col('기록(일)', lambda r: r['cmp'].get('days'), comma, width=60),
            Col('지금 vs 평소', lambda r: r['cmp'].get('diff'), _pct, True, 90),
            Col('판단', lambda r: r['cmp'].get('verdict') or '', width=120),
            Col('참고 판매가', lambda r: r['cmp'].get('suggest'), comma, width=100),
            Col('받은 시각', lambda r: r['fetched'], _when, width=100)], sort_col=7, desc=False)
        self.table.clicked.connect(lambda idx: self._show(self.table.row_at(idx)))
        sp.addWidget(self.table)

        detail = QTabWidget()
        self.chart_view, self._set_chart = self._make_chart()
        detail.addTab(self.chart_view, '과거 시세 그래프')
        self.hist_tbl = Table([Col('날짜', lambda h: h['day'], width=110), Col('최저가', lambda h: h['min'], comma, width=110),
                               Col('평균가', lambda h: h['avg'], comma, width=110),
                               Col('최고가', lambda h: h['max'], comma)], sort_col=0)
        detail.addTab(self.hist_tbl, '날짜별 시세')
        self.list_tbl = Table([Col('구분', lambda l: '판매' if l['kind'] == 'sale' else '구매', width=60),
                               Col('가격', lambda l: l['price'], comma, width=110), Col('수량', lambda l: l['qty'], comma, width=70),
                               Col('노점 이름', lambda l: l['shop'])], sort_col=1, desc=False)
        detail.addTab(self.list_tbl, '지금 노점')
        self.detail_title = QLabel()
        dw = QWidget()
        dl = QVBoxLayout(dw)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(self.detail_title)
        dl.addWidget(detail, 1)
        sp.addWidget(dw)
        sp.setSizes([420, 300])
        v.addWidget(sp, 1)
        self._refresh_rows()
        self.status.setText('아이템 %d종 · 표에서 고른 뒤 [선택한 아이템 받기] (이미 받은 기록은 바로 보입니다)' % len(self.entries))

    # ------------------------------------------------------------ 표
    @property
    def sid(self):
        return self.server.currentData()

    def _row(self, e):
        fetched, listings = self.store.latest_listings(self.sid, e['item_id'])
        cmp = G.compare(self.store.history(self.sid, e['item_id']), listings) if fetched else {}
        return dict(e, cmp=cmp, fetched=fetched, listings=listings)

    def _refresh_rows(self):
        self.rows = {iid: self._row(e) for iid, e in self.entries.items()}
        self.table.set_rows(list(self.rows.values()))

    def _server_changed(self):
        self.store.set_meta('server', self.sid)
        self._refresh_rows()

    def _show(self, r):
        if not r:
            return
        hist = self.store.history(self.sid, r['item_id'])
        c = r['cmp']
        self.detail_title.setText(
            '<b>%s</b> · %s · 지금 판매 최저가 %s · 구매 최고가 %s · 7일 평균 %s · 기록 %s일 · <b>%s %s</b>' % (
                r['name'], G.SERVERS.get(self.sid), comma(c.get('now_min')), comma(c.get('buy_max')),
                comma(c.get('avg7') and int(c['avg7'])), c.get('days', 0), c.get('verdict', '아직 안 받음'),
                _pct(c.get('diff')) if c.get('diff') is not None else ''))
        self.hist_tbl.set_rows(hist)
        self.list_tbl.set_rows(r['listings'])
        self._set_chart(hist)

    def _make_chart(self):
        if not HAS_CHARTS:
            return QLabel('QtCharts 없음 — [날짜별 시세] 표를 보세요'), (lambda *_: None)
        chart = QChart()
        chart.setTheme(QChart.ChartThemeDark)                        # 어두운 화면에 맞춤
        chart.setBackgroundBrush(QColor(T['surface']))
        chart.legend().setVisible(True)
        view = QChartView(chart)
        view.setStyleSheet('background: transparent;')
        view.setRenderHint(QPainter.Antialiasing)

        def set_series(hist):
            chart.removeAllSeries()
            for ax in chart.axes():
                chart.removeAxis(ax)
            if not hist:
                chart.setTitle('기록 없음')
                return
            chart.setTitle('노점 거래 시세 (가로: 오늘부터 며칠 전)')
            today = datetime.date.today()
            ax_x, ax_y = QValueAxis(), QValueAxis()
            ax_x.setLabelFormat('%d')
            ax_x.setTitleText('일 전')
            ax_y.setLabelFormat('%.0f')
            chart.addAxis(ax_x, Qt.AlignBottom)
            chart.addAxis(ax_y, Qt.AlignLeft)
            ymax, xmin = 1, 0
            for key, label, color in (('min', '최저가', T['ok']), ('avg', '평균가', T['accent']), ('max', '최고가', T['warn'])):
                s = QLineSeries()
                s.setName(label)
                s.setColor(QColor(color))
                for h in sorted(hist, key=lambda h: h['day']):
                    x = -(today - datetime.date.fromisoformat(h['day'])).days
                    if h[key]:
                        s.append(x, h[key])
                        ymax, xmin = max(ymax, h[key]), min(xmin, x)
                chart.addSeries(s)
                s.attachAxis(ax_x)
                s.attachAxis(ax_y)
            ax_x.setRange(xmin, 0)
            ax_y.setRange(0, ymax * 1.1)
        return view, set_series

    # ------------------------------------------------------------ 받기
    def _fetch_all(self):
        rows = self.table.visible_rows() if hasattr(self.table, 'visible_rows') else list(self.rows.values())
        todo = [r for r in rows if not (r['fetched'] and time.time() - r['fetched'] < FRESH_SEC)]
        pages = self.period.currentData()
        est = len(todo) * (2 + pages) * G.MIN_INTERVAL / 60
        if todo and QMessageBox.question(self, '목록 전체 받기', '%d종을 받습니다. 약 %.0f분 걸립니다(홈페이지 요청 제한 때문에 천천히).\n'
                                         '진행할까요? 도중에 [멈추기] 할 수 있습니다.' % (len(todo), est)) != QMessageBox.Yes:
            return
        self._fetch(todo, skip_fresh=False)

    def _fetch(self, rows, skip_fresh=True):
        if self._worker and self._worker.is_alive():
            return
        rows = [r for r in rows if not (skip_fresh and r['fetched'] and time.time() - r['fetched'] < FRESH_SEC
                                        and self.period.currentData() == 1)]
        if not rows:
            self.status.setText('받을 아이템을 표에서 고르세요 (30분 안에 받은 것은 그대로 씁니다)')
            return
        self._stop.clear()
        for b in (self.b_sel, self.b_all):
            b.setEnabled(False)
        self.b_stop.setEnabled(True)
        items = [(r['item_id'], r['name']) for r in rows]
        self._worker = threading.Thread(target=self._work, args=(items, self.sid, self.period.currentData()),
                                        daemon=True, name='GnjoyMarket')
        self._worker.start()

    def _work(self, items, sid, pages):
        done, msg = [], ''
        try:
            for n, (iid, name) in enumerate(items, 1):
                if self._stop.is_set():
                    msg = '멈춤'
                    break
                self.bridge.progress.emit('받는 중 %d/%d · %s …' % (n, len(items), name))
                listings = [l for l in self.client.listings(sid, name) if l['item_id'] == iid]   # 비슷한 이름 제외
                hist, _last = self.client.history(sid, name, pages)
                self.store.save_listings(sid, iid, listings)
                self.store.save_history(sid, iid, hist)
                # 창고 표의 최저가·중앙값·평균가·노점수도 이 목록으로 (로컬 시세 DB 교체)
                if listings:
                    self.app.db.add_local_listings([(iid, Listing(l['price'], l['qty'] or 1, l['kind'], l['shop'],
                                                                  time.time())) for l in listings],
                                                   source='gnjoy:%s' % sid)
                else:                                              # 지금 노점이 없으면 예전 스냅샷 가격을 남기지 않는다
                    self.app.db._exec("DELETE FROM local_listings WHERE item_id=? AND variant=''", (iid,))
                done.append(iid)
                self.bridge.item_done.emit(iid)
        except G.RateLimited as e:
            msg = str(e)
        except Exception as e:                                    # 네트워크 오류 등
            msg = '받기 실패: %s' % e
        if done:
            try:
                prices, _failed = self.app.market.fetch({i: self.entries[i]['name'] for i in done}, force=True)
                self._prices = prices
            except Exception:
                self._prices = {}
        self.bridge.finished.emit('%d종 받음%s' % (len(done), (' · ' + msg) if msg else ''))

    def _item_done(self, iid):
        self.rows[iid] = self._row(self.entries[iid])
        self.table.set_rows(list(self.rows.values()))

    def _finished(self, text):
        self.status.setText(text + ' · 창고 표 시세도 갱신했습니다')
        for b in (self.b_sel, self.b_all):
            b.setEnabled(True)
        self.b_stop.setEnabled(False)
        prices = getattr(self, '_prices', {})
        if prices and hasattr(self.app, 'prices'):
            self.app.prices.update(prices)
            self.app._rebuild()

    def closeEvent(self, ev):
        self._stop.set()
        super().closeEvent(ev)
