"""오늘의 사냥 결산: 그날 녹화한 리플레이들의 획득 아이템·제니 수입/지출을 한 화면에 모은다.

획득·제니는 리플레이 패킷(●), 아이템 가치는 공식 노점 시세로 계산(○), 상점/노점 판매 구분은 미검증(△)이다.
획득 가치와 판매 제니는 더하지 않는다 — 주운 것을 팔면 양쪽에 다 잡히기 때문이다.
"""
import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout

from analyzer.events import NPC_SALE, OTHER_INCOME, VEND_SALE
from core import ledger as L
from core.fmt import comma, hms
from ui.economy_tasks import BackgroundTask
from ui.loot_table import LootTable
from ui.widgets import Col, Kpi, Table

LEGEND = ('● 리플레이 패킷으로 측정  ·  ○ 측정값 × 시세로 계산(노점가와 상점가 중 더 비싼 쪽)  ·  △ 미검증(상점가 기준가는 '
          'rAthena 자료, 상점/노점 판매 구분은 표본 리플레이가 없어 확인하지 못했습니다 — 판매 금액 자체는 ●)')
NOTE = '획득 가치와 판매 제니는 서로 더하지 않습니다(주운 것을 팔면 양쪽에 다 잡힙니다). 녹화한 구간만 집계됩니다.'


def today():
    return datetime.date.today().isoformat()


class SettlementDialog(QDialog):
    def __init__(self, app, service, pricer, parent=None):
        """service: LedgerService · pricer: LootValue(없으면 가치를 계산하지 않는다)."""
        super().__init__(parent)
        self.app, self.service, self.pricer = app, service, pricer
        self.setWindowTitle('오늘의 사냥 결산')
        self.setWindowFlag(Qt.Window)
        self.setModal(False)
        self.resize(1180, 760)
        self.entries, self.result = [], None
        self.task = BackgroundTask(self, self._task_done, self._task_error)

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel('날짜'))
        self.day = QComboBox()
        self.day.setMinimumWidth(140)
        self.day.currentIndexChanged.connect(lambda _: self.reload())
        top.addWidget(self.day)
        self.b_refresh = QPushButton('리플레이 폴더 다시 읽기')
        self.b_refresh.setToolTip('그날 녹화한 리플레이 중 아직 읽지 않았거나 바뀐 파일을 읽습니다(공식 사이트 요청 없음).')
        self.b_refresh.clicked.connect(self.refresh)
        top.addWidget(self.b_refresh)
        self.b_prices = QPushButton('시세 받기')
        self.b_prices.setToolTip('노점 시세를 모르는 획득 아이템을 공식 노점 시세로 채웁니다(아이템 1종에 요청 2번).')
        self.b_prices.clicked.connect(self.fetch_prices)
        top.addWidget(self.b_prices)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        top.addWidget(self.status, 1)
        v.addLayout(top)

        grid = QGridLayout()
        self.kpi = {}
        for i, (key, title) in enumerate((('loot', '획득 아이템 가치 ○'), ('npc', '상점 판매 △'), ('vend', '노점 판매 △'),
                                          ('other', '기타 제니 수입 ●'), ('spent', '제니 지출 ●'),
                                          ('net', '제니 순증감 ●'))):
            self.kpi[key] = Kpi(title)
            grid.addWidget(self.kpi[key], 0, i)
        v.addLayout(grid)

        split = QSplitter(Qt.Vertical)
        self.replays = Table([
            Col('캐릭터', lambda r: r['character'] or '?', width=150),
            Col('리플레이', lambda r: r['file'], width=150),
            Col('시작', lambda r: (r['recorded_at'] or '')[11:16] + (' · 녹화 중' if r.get('live') else ''), width=110),
            Col('길이', lambda r: r['duration_ms'], hms, numeric=True, width=80),
            Col('획득(종/개)', lambda r: '%d / %d' % (len(r['entry']['items']), sum(r['entry']['items'].values())),
                width=90),
            Col('상점 판매 △', lambda r: r['entry']['income'].get(NPC_SALE, 0), comma, width=110),
            Col('노점 판매 △', lambda r: r['entry']['income'].get(VEND_SALE, 0), comma, width=110),
            Col('기타 수입 ●', lambda r: r['entry']['income'].get(OTHER_INCOME, 0), comma, width=110),
            Col('지출 ●', lambda r: r['entry']['spent'], comma)])
        split.addWidget(self.replays)
        self.items = LootTable(app.s, app.loot_icons() if hasattr(app, 'loot_icons') else None)
        split.addWidget(self.items)
        split.setSizes([220, 420])
        v.addWidget(split, 1)
        for text in (NOTE, LEGEND):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setObjectName('cardTitle')
            v.addWidget(label)
        self._fill_days()

    # ---------------------------------------------------------------- 자료
    def _fill_days(self):
        days = sorted(set(self.service.days()) | {today()}, reverse=True)
        current = self.day.currentData() or today()
        self.day.blockSignals(True)
        self.day.clear()
        for d in days:
            self.day.addItem('오늘 (%s)' % d if d == today() else d, d)
        self.day.setCurrentIndex(max(0, self.day.findData(current)))
        self.day.blockSignals(False)
        self.reload()

    def _live_row(self):
        """지금 녹화 중인 리플레이의 누계(아직 파일로 읽을 수 없으므로 LIVE 스냅샷에서 가져온다)."""
        rp = getattr(getattr(self.app, 'live_page', None), 'last_replay_snap', None) or {}
        if rp.get('state') == 'FINISHED' or not rp.get('ledger') or not rp.get('recorded_at'):
            return None
        led = rp['ledger']
        return dict(path='', file=rp.get('file') or '', recorded_at=rp['recorded_at'], character=rp.get('character'),
                    server=rp.get('server'), job_id=rp.get('job_id'), duration_ms=rp.get('duration_ms'), live=True,
                    entry=dict(items={int(k): q for k, q in (led.get('items') or {}).items()},
                               income=dict(led.get('income') or {}), spent=led.get('spent') or 0))

    def reload(self):
        day = self.day.currentData() or today()
        rows = self.service.day(day)
        live = self._live_row()
        if live and live['recorded_at'][:10] == day:
            rows = [r for r in rows if (r['file'], r['recorded_at']) != (live['file'], live['recorded_at'])] + [live]
        self.entries = rows
        self.replays.set_rows(rows)
        self._settle()

    def _vend(self, item_id):
        return self.pricer.price(item_id) if self.pricer else (None, '')

    def _settle(self):
        total = L.merge(r['entry'] for r in self.entries)
        res = self.result = L.settle(total, self._vend, self.app.item_db.name,
                                     int(self.app.s.get('jackpot_min_price', 10_000_000) or 0),
                                     getattr(self.app, 'npc_price', None))
        self.items.set_overcharge(int(self.app.s.get('overcharge_rate', 24) or 0))
        self.items.set_rows(res['rows'])
        income = res['income']
        money = lambda v: '모름' if v is None else comma(v)                            # noqa: E731
        # 값을 하나도 모르면 0이 아니라 '모름'이다
        self.kpi['loot'].set(money(res['loot_value']) if res['rows'] else '-',
                             ('노점 %s · 상점 %s%s' % (money(res['vend_value']), money(res['npc_value']),
                                                   ' · 값 모름 %d종 제외' % res['unpriced'] if res['unpriced'] else ''))
                             if res['rows'] else '획득 기록 없음')
        self.kpi['npc'].set(comma(income[NPC_SALE]))
        self.kpi['vend'].set(comma(income[VEND_SALE]))
        self.kpi['other'].set(comma(income[OTHER_INCOME]))
        self.kpi['spent'].set(comma(res['spent']))
        self.kpi['net'].set(('+' if res['zeny_net'] > 0 else '') + comma(res['zeny_net']))
        self.no_vend = [(r['item_id'], r['name']) for r in res['rows'] if r['vend'] is None]
        self.b_prices.setEnabled(bool(self.no_vend) and self.pricer is not None and self.task.future is None)
        self.b_prices.setText('시세 받기 (%d종)' % len(self.no_vend) if self.no_vend else '시세 받기')
        if not self.entries:
            self.status.setText('이 날 녹화한 리플레이가 없습니다. [리플레이 폴더 다시 읽기]를 눌러 보세요.')
        elif res['jackpots']:
            self.status.setText('★ 기준 이상 득템 %d종: %s' % (
                len(res['jackpots']), ', '.join(r['name'] for r in res['jackpots'][:5])))
        else:
            self.status.setText('리플레이 %d개' % len(self.entries))

    # ---------------------------------------------------------------- 작업
    def refresh(self):
        day = self.day.currentData() or today()
        if self.task.start(lambda stop: ('refresh', self.service.refresh(day, stop))):
            self._busy('리플레이를 읽는 중…')

    def fetch_prices(self):
        if not self.result or not self.pricer:
            return
        todo = list(self.no_vend)

        def job(stop):
            done = 0
            for item_id, name in todo:
                if stop.is_set():
                    break
                self.pricer.ensure(item_id, name)
                done += 1
            return 'prices', done
        if todo and self.task.start(job):
            self._busy('시세 받는 중… 아이템 %d종 (요청 최대 %d번)' % (len(todo), len(todo) * 2))

    def _busy(self, text):
        self.status.setText(text)
        self.b_refresh.setEnabled(False)
        self.b_prices.setEnabled(False)

    def _task_done(self, value):
        kind, data = value
        self.b_refresh.setEnabled(True)
        if kind == 'refresh':
            self._fill_days()
            done, failed = data
            self.status.setText('리플레이 %d개를 새로 읽었습니다.%s' % (
                done, ' 읽지 못함(녹화 중이거나 깨짐): ' + ', '.join(failed[:4]) if failed else ''))
        else:
            self._settle()
            self.status.setText('시세 %d종을 받았습니다. 그래도 "모름"인 것은 지금 노점도 최근 거래도 없는 아이템입니다.' % data)

    def _task_error(self, exc):
        self.b_refresh.setEnabled(True)
        self._settle()
        self.status.setText('작업 실패: %s' % exc)

    def closeEvent(self, ev):
        self.task.stop_event.set()
        super().closeEvent(ev)
