"""노점 검색 창(창을 띄워 둔 채 게임 가능). 위: 서버·분류·이름 검색(공식 요청), 가운데: 받은 결과를 요청 없이 거르기·정렬,
검색 결과는 카드형으로 비교하며 카드·인챈트·위치를 순차 자동 조회한다(2.5초 간격·30분 캐시).
구매·게임 입력은 하지 않는다 — 가격·상점 이름은 복사만."""
import datetime
import html

from PySide6.QtCore import Qt, QStringListModel, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from services import gnjoy_market as G
from services import popular_option as PO
from services import vend_search as VS
from services.deal_watch import MAX_WATCH_ITEMS, validate_watchlist
from services.equipment_details import item_description, option_summary, spec_text
from ui.equipment_details import EquipmentOptions
from ui.economy_tasks import BackgroundTask
from ui.vend_result_cards import VendResultCards
from ui.widgets import Col, Table, comma

RECENT_MAX = 8


def _when(t):
    return datetime.datetime.fromtimestamp(t).strftime('%H:%M') if t else '-'


def _vs(v):
    if v is None:
        return '-'
    return ('▼ %.0f%%' if v < 0 else '▲ +%.0f%%') % v


class VendSearchDialog(QDialog):
    def __init__(self, parent, settings, item_db, store, client=None, map_name=None, on_watch_changed=None):
        super().__init__(parent)
        self.s, self.item_db, self.store = settings, item_db, store
        self.map_name = map_name or (lambda code: code)
        self.on_watch_changed = on_watch_changed
        self.engine = VS.VendSearch(client)
        self.task = BackgroundTask(self, self._done, self._error)
        self.auto_task = BackgroundTask(self, self._auto_done, self._auto_error)
        self._auto_generation, self._auto_queue, self._auto_key = 0, [], None
        self._auto_stopped, self._closed = False, False
        self.auto_retry = QTimer(self, interval=15000, timeout=self._pump_auto)
        self.result, self.rows, self.current = None, [], None
        self.setWindowTitle('노점 검색 (공식 아이템 거래현황)')
        self.resize(1400, 760)
        cfg = settings.get('vend_search') or {}
        outer = QVBoxLayout(self)

        # 1줄: 공식 요청 조건
        top = QHBoxLayout()
        self.server = QComboBox()
        self.server.setToolTip('공식 사이트에 이 서버로 물어봅니다. 바포메트 서버 매물만 보려면 바포메트를 고르세요.')
        for sid, name in VS.SERVERS:
            self.server.addItem(name, sid)
        self.server.setCurrentIndex(max(0, self.server.findData(cfg.get('server', '129'))))
        self.category = QComboBox()
        for key, name in VS.CATEGORIES:
            self.category.addItem(name, key)
        self.query = QLineEdit(placeholderText='아이템 이름 일부 — 예) 스펠 스톤, +10 일루시온, 카드')
        self.query.returnPressed.connect(self.search)
        self.completer = QCompleter(QStringListModel(self), self)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setMaxVisibleItems(15)
        self.query.setCompleter(self.completer)
        self.all_words = QCheckBox('띄어쓴 단어 모두 포함')
        self.all_words.setChecked(cfg.get('all_words', True))
        self.all_words.setToolTip("'스펠 스톤' → '스펠%스톤': 두 단어가 순서대로 들어간 이름을 모두 찾습니다(공식 검색의 % 사용).")
        self.order = QComboBox()
        for key, name in VS.ORDERS:
            self.order.addItem('받기: ' + name, key)
        self.order.setToolTip('공식 사이트가 이 순서로 10건씩 줍니다. 비싼 매물(높은 제련)을 보려면 높은순으로 받으세요.')
        self.pages = QSpinBox(minimum=1, maximum=VS.MAX_PAGES, value=int(cfg.get('pages', 2)), suffix=' 쪽')
        self.pages.setToolTip('한 쪽 = 10건 = 공식 요청 1번(2.5초 간격)')
        self.btn_search = QPushButton('검색')
        self.btn_search.setDefault(True)
        self.btn_search.clicked.connect(self.search)
        self.btn_more = QPushButton('더 받기')
        self.btn_more.setToolTip('공식 사이트는 가격 낮은 순으로 10건씩 줍니다. 뒤쪽(비싼 매물·높은 제련)을 이어서 받습니다.')
        self.btn_more.setEnabled(False)
        self.btn_more.clicked.connect(lambda: self.search_more())      # clicked 의 checked 인자가 step 으로 가지 않게
        self.btn_stop = QPushButton('중지')
        self.btn_stop.clicked.connect(self.stop)
        top.addWidget(QLabel('서버'))
        for w in (self.server, self.category):
            top.addWidget(w)
        top.addWidget(self.query, 1)
        for w in (self.all_words, self.order, self.pages, self.btn_search, self.btn_more, self.btn_stop):
            top.addWidget(w)
        outer.addLayout(top)

        # 2줄: 받은 결과 거르기(요청 없음)
        flt = QHBoxLayout()
        self.sale, self.buy = QCheckBox('판매'), QCheckBox('구매')
        self.sale.setChecked(True)
        self.buy.setChecked(True)
        self.refine = QSpinBox(minimum=0, maximum=20, prefix='제련 ', suffix=' 이상')
        self.pmin = QLineEdit(placeholderText='최소 예) 500만')
        self.pmax = QLineEdit(placeholderText='최대 예) 1.5억')
        for w in (self.pmin, self.pmax):
            w.setFixedWidth(130)
            w.textChanged.connect(self._apply)
        self.sort = QComboBox()
        for key, name in VS.SORTS:
            self.sort.addItem('보기: ' + name, key)
        self.only_item = QCheckBox('선택한 아이템만')
        self.only_item.setToolTip('비슷한 이름(오리데오콘 / 오리데오콘 원석)을 빼고 선택한 줄과 같은 아이템 번호만')
        # 장비 검색이면 받은 결과에서 가장 많이 올라온 구성(국민 옵션)만 기본으로 보인다 — 끄면 전체
        self.only_popular = QCheckBox('국민 옵션만')
        self.only_popular.setChecked(False)
        self.only_popular.setToolTip('받은 판매 매물 중 가장 많은 제련·등급·슬롯 구성만 봅니다(추가 요청 없음). 끄면 전체.')
        self.only_popular.hide()
        self.popular = None
        for w in (self.sale, self.buy, self.refine, self.sort, self.only_item, self.only_popular):
            (w.toggled if isinstance(w, QCheckBox) else
             w.valueChanged if isinstance(w, QSpinBox) else w.currentIndexChanged).connect(self._apply)
        flt.addWidget(QLabel('거르기:'))
        for w in (self.sale, self.buy, self.refine, QLabel('가격'), self.pmin, QLabel('~'), self.pmax, self.sort,
                  self.only_item, self.only_popular):
            flt.addWidget(w)
        flt.addStretch(1)
        outer.addLayout(flt)

        # 3줄: 최근 검색
        self.recent_row = QHBoxLayout()
        outer.addLayout(self.recent_row)
        self._render_recent()

        self.summary = QLabel('서버와 이름을 넣고 검색하세요. 공식 사이트는 노점 개설 3분 뒤부터 보이고, 위치(좌표)는 알려 주지 않습니다.')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        outer.addWidget(self.summary)

        auto_row = QHBoxLayout()
        self.auto_status = QLabel('검색 후 카드·인챈트 자동 표시 · 저장된 상세는 즉시 표시')
        self.auto_status.setWordWrap(True)
        auto_row.addWidget(self.auto_status, 1)
        self.btn_auto = QPushButton('자동 조회 다시 시작')
        self.btn_auto.setAutoDefault(False)
        self.btn_auto.clicked.connect(self._begin_auto)
        auto_row.addWidget(self.btn_auto)
        self.btn_view = QPushButton('표로 보기')
        self.btn_view.setAutoDefault(False)
        self.btn_view.clicked.connect(self._switch_view)
        auto_row.addWidget(self.btn_view)
        outer.addLayout(auto_row)

        split = QSplitter(Qt.Horizontal)
        self.result_view = QStackedWidget()
        self.cards = VendResultCards(item_db, self.map_name, self._card_action)
        self.result_view.addWidget(self.cards)
        self.result_view.addWidget(split)
        outer.addWidget(self.result_view, 1)
        self.table = Table([
            Col('구분', lambda r: '판매' if r['kind'] == 'sale' else '구매', width=46),
            Col('서버', lambda r: r['server'], width=70),
            Col('아이템 / 장비 사양', lambda r: r['name'] + '\n' + spec_text(r), width=340,
                tooltip=lambda r: '%s\n%s\n아이템 번호 %d' % (r['name'], spec_text(r), r['item_id'])),
            Col('제련', lambda r: r['refine'] or None, lambda v: '' if v is None else '+%d' % v, numeric=True, width=48),
            Col('슬롯', lambda r: r['slots'], numeric=True, width=40),
            Col('수량', lambda r: r['qty'], comma, width=55),
            Col('가격', lambda r: r['price'], comma, width=115),
            Col('7일 평균 대비', lambda r: r.get('vs'), _vs, numeric=True, width=110,
                tooltip=lambda r: '7일 평균 %s Z' % comma(int(r['avg7'])) if r.get('avg7') else
                '저장된 시세 없음 — 오른쪽 [시세 받기]'),
            Col('카드·인챈트', lambda r: option_summary(r.get('_detail')), width=230,
                tooltip=lambda r: option_summary(r.get('_detail'))),
            Col('노점 이름', lambda r: r['shop'] or '이름 없음', tooltip=lambda r: r['shop'])], sort_col=None)
        self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.setWordWrap(False)
        self.table.selectionModel().selectionChanged.connect(self._selected)
        self.table.doubleClicked.connect(lambda idx: self.fetch_detail())
        split.addWidget(self.table)

        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(8, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(390)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.detail = QLabel('검색하면 모든 매물의 카드·인챈트가 자동으로 표시됩니다.')
        self.detail.setTextFormat(Qt.RichText)
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content_layout.addWidget(self.detail)
        self.base_options = QTextBrowser()
        self.base_options.setOpenLinks(False)
        self.base_options.setOpenExternalLinks(False)
        self.base_options.setMinimumHeight(180)
        self.base_options.setMaximumHeight(240)
        self.base_options.hide()
        content_layout.addWidget(self.base_options)
        self.equipment_options = EquipmentOptions(item_db)
        content_layout.addWidget(self.equipment_options)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        sv.addWidget(scroll, 1)
        self.buttons = {}
        request_buttons, copy_buttons = QHBoxLayout(), QHBoxLayout()
        sv.addLayout(request_buttons)
        sv.addLayout(copy_buttons)
        for key, text, slot in (('detail', '카드·인챈트 상세 받기', self.fetch_detail),
                                ('history', '7일 시세 받기', self.fetch_history),
                                ('price', '가격 복사', lambda: self._copy('price')),
                                ('shop', '노점 이름 복사', lambda: self._copy('shop')),
                                ('watch', '관심 매물 알림에 추가', self.add_watch)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setEnabled(False)
            self.buttons[key] = b
            (request_buttons if key in ('detail', 'history') else copy_buttons if key in ('price', 'shop') else sv).addWidget(b)
        split.addWidget(side)
        split.setSizes([950, 450])

        self.status = QLabel()
        outer.addWidget(self.status)
        self._busy(False)

    def showEvent(self, e):
        if self._closed:
            self._closed = False
            self._begin_auto()
        # 아이템 DB는 앱 시작 뒤에 읽힐 수 있어, 자동완성 목록이 비었으면 보일 때 다시 채운다
        model = self.completer.model()
        if not model.stringList():
            model.setStringList(sorted({i['name'] for i in (getattr(self.item_db, 'items', {}) or {}).values()
                                        if isinstance(i, dict) and i.get('name')}))
        super().showEvent(e)

    # ------------------------------------------------------------ 요청
    def _busy(self, busy, text=''):
        self.btn_search.setEnabled(not busy)
        self.btn_stop.setEnabled(busy or self.auto_task.future is not None or bool(self._auto_queue))
        self.btn_auto.setEnabled(not busy and bool(self.rows))
        for k in ('detail', 'history'):
            self.buttons[k].setEnabled(not busy and self.current is not None)
        self.status.setText(text)

    def _switch_view(self):
        index = 1 - self.result_view.currentIndex()
        self.result_view.setCurrentIndex(index)
        self.btn_view.setText('카드로 보기' if index else '표로 보기')

    def _card_action(self, row, key):
        for i, candidate in enumerate(self.table.model_.rows):
            if candidate is row:
                index = self.table.proxy.mapFromSource(self.table.model_.index(i, 0))
                self.table.selectRow(index.row())
                break
        if key == 'compare':
            from ui.gear_comparison_dialog import GearComparisonDialog
            dialog = GearComparisonDialog(self.parent(), candidate=row, item_db=self.item_db)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            self.comparison_dialog = dialog
            dialog.destroyed.connect(lambda: self._clear_comparison(dialog))
            dialog.show()
        elif key == 'history':
            self.fetch_history()
        elif key == 'watch':
            self.add_watch()
        else:
            self._copy('price')

    def _clear_comparison(self, dialog):
        if getattr(self, 'comparison_dialog', None) is dialog:
            self.comparison_dialog = None

    # 상세를 한 건씩 받아 GUI 스레드에서 카드에 반영한다.
    def _begin_auto(self):
        if self._closed:
            return
        self._auto_generation += 1
        self.auto_task.stop_event.set()
        self.auto_retry.stop()
        self._auto_stopped = False
        self._auto_queue = []
        for row in self.rows:
            if row.get('_detail') is not None:
                row['_detail_state'] = 'complete'
            elif not row.get('sid') or not row.get('ssi'):
                row['_detail_state'] = 'unavailable'
            else:
                row['_detail_state'] = 'pending'
                self._auto_queue.append(row)
            self.cards.refresh(row)
        self._pump_auto()

    def _update_auto_status(self):
        total = len(self.rows)
        complete = sum(r.get('_detail') is not None for r in self.rows)
        failed = sum(r.get('_detail_state') in ('error', 'unavailable') for r in self.rows)
        wait = self.engine.client.retry_after
        text = '카드·인챈트 자동 표시 %d/%d건' % (complete, total)
        if self._auto_stopped:
            text += ' · 중지됨'
        elif wait and self._auto_queue:
            text += ' · 요청 제한, %d초 뒤 자동 재개' % wait
        elif self._auto_queue or self.auto_task.future is not None:
            text += ' · 상세를 차례로 받는 중(공식 요청 2.5초 간격)'
        else:
            text += ' · 완료'
        if failed:
            text += ' · 미확인 %d건' % failed
        self.auto_status.setText(text)
        self.btn_auto.setEnabled(self.task.future is None and bool(self.rows))
        self.btn_stop.setEnabled(self.task.future is not None or bool(self._auto_queue) or self.auto_task.future is not None)

    def _pump_auto(self):
        if self._closed or self._auto_stopped:
            return
        if self.auto_task.future is not None:
            return
        if not self._auto_queue:
            self.auto_retry.stop()
            self._update_auto_status()
            return
        if self.engine.client.retry_after:
            for row in self._auto_queue:
                row['_detail_state'] = 'rate_limited'
                self.cards.refresh(row)
            self.auto_retry.start()
            self._update_auto_status()
            return
        self.auto_retry.stop()
        row = self._auto_queue.pop(0)
        generation = self._auto_generation
        self._auto_key = generation, row
        row['_detail_state'] = 'loading'
        self.cards.refresh(row)
        self.auto_task.start(lambda stop: (generation, row, self.engine.detail(self.store, row)))
        self._update_auto_status()

    def _auto_done(self, value):
        generation, row, detail = value
        if not self._closed and generation == self._auto_generation:
            row['_detail_state'] = 'complete'
            self._display_detail(row, detail)
        self._pump_auto()

    def _auto_error(self, exc):
        if self._closed:
            return
        generation, row = self._auto_key
        if generation == self._auto_generation:
            if isinstance(exc, G.RateLimited):
                self._auto_queue.insert(0, row)
            else:
                row['_detail_state'], row['_detail_error'] = 'error', str(exc)
                self.cards.refresh(row)
        self._pump_auto()

    def stop(self):
        self.task.stop_event.set()
        self.auto_task.stop_event.set()
        self._auto_stopped = True
        self._auto_generation += 1
        self.auto_retry.stop()
        for row in self.rows:
            if row.get('_detail') is None and row.get('_detail_state') not in ('error', 'unavailable'):
                row['_detail_state'] = 'stopped'
                self.cards.refresh(row)
        self._auto_queue = []
        self._update_auto_status()

    def _display_detail(self, row, detail):
        row['_detail'] = detail
        model = self.table.model_
        model._disp.clear()
        if model.rowCount():
            model.dataChanged.emit(model.index(0, 8), model.index(model.rowCount() - 1, 8))
        self.cards.refresh(row)
        comparison = getattr(self, 'comparison_dialog', None)
        if comparison is not None and comparison.candidate is row:
            comparison._show_worn()
        if self.current is row:
            self._show(row, detail)

    def _start(self, fn, text):
        wait = self.engine.client.retry_after
        if wait:
            self.status.setText('공식 사이트가 요청을 막아 %d초 뒤에 다시 할 수 있습니다(429).' % wait)
            return
        if self.task.start(fn):
            self._busy(True, text)

    def search(self):
        text = self.query.text().strip()
        if not text:
            self.query.setFocus()
            return
        if self.task.future is not None:
            return
        self.stop()
        q = VS.build_query(text, self.all_words.isChecked())
        args = (self.server.currentData(), q, self.category.currentData(), self.order.currentData(), self.pages.value())
        self._remember(text)
        self._start(lambda stop: ('search', args, self.engine.search(*args, stop=stop)),
                    '검색 중… 공식 요청 최대 %d번(2.5초 간격)' % self.pages.value())

    def search_more(self, step=3):
        """뒤쪽을 이어 받는다 — 받은 쪽은 다시 요청하지 않고 다음 쪽부터만 받는다."""
        if self.result is None or self.task.future is not None:
            return
        want = min(VS.MAX_PAGES, self.result['pages'] + step)
        if want <= self.result['pages']:
            return
        self.pages.setValue(want)
        q = VS.build_query(self.query.text().strip(), self.all_words.isChecked())
        args = (self.server.currentData(), q, self.category.currentData(), self.order.currentData(), want)
        self._start(lambda stop: ('search', args, self.engine.search(*args, stop=stop)),
                    '%d쪽까지 이어 받는 중… 공식 요청 최대 %d번' % (want, want - self.result['pages']))

    def fetch_detail(self):
        r = self.current
        if r is None or not r.get('ssi'):
            self.status.setText('이 매물은 상세 보기 번호가 없어 받을 수 없습니다.' if r else '')
            return
        self._start(lambda stop: ('detail', r, self.engine.detail(self.store, r)), '상세 받는 중… (요청 1번)')

    def fetch_history(self):
        # 공식 거래 이력은 슬롯·등급까지 들어간 전체 이름이 정확해야 나온다 → 매물 이름 그대로 받는다(구성별 시세)
        r = self.current
        if r is None:
            return
        market = PO.ExactMarket(self.store, self.engine.client)

        def job(stop):
            _, rows = market.history(r['sid'], r['name'])
            return ('history', r, [h for h in rows if h['min'] or h['avg']])
        self._start(job, '%s 최근 10일 시세 받는 중… (요청 1번, 30분 재사용)' % r['name'])

    def _done(self, value):
        if self._closed:
            return
        kind, key, data = value
        self._busy(False)
        if kind == 'search':
            self.result = data
            self.rows = data['rows']
            self._annotate()
            self._apply()
            self._begin_auto()
        elif kind == 'detail':
            self._display_detail(key, data)
            self.status.setText('상세를 받았습니다.')
        else:
            self._annotate()
            self._apply()
            if not data:
                self.status.setText('공식 거래 기록이 없습니다.')
            elif key.get('avg7') is None:                # 받은 10개 거래일이 모두 7일보다 오래됨
                self.status.setText('시세 %d일치를 받았지만 최근 7일 거래가 없습니다(가장 최근 거래일 %s).'
                                    % (len(data), max(h['day'] for h in data)))
            else:
                self.status.setText('시세 %d일치를 받았습니다.' % len(data))

    def _error(self, exc):
        self._busy(False)
        self.status.setText(str(exc) if isinstance(exc, G.RateLimited) else '받기 실패: %s' % exc)

    # ------------------------------------------------------------ 표시(요청 없음)
    def _item_name(self, r):
        name = self.item_db.name(r['item_id']) if hasattr(self.item_db, 'name') else ''
        return name if name and not str(name).isdigit() else r['base']

    def _annotate(self):
        # 같은 구성(전체 이름)의 저장 시세 → 없고 제련·등급·슬롯이 없는 이름이면 아이템 번호로 받아 둔 시세(요청 없음)
        market, cache = PO.ExactMarket(self.store, self.engine.client), {}
        for r in self.rows:
            r['_detail'] = self.engine.cached_detail(self.store, r)
            k = (r['sid'], r['name'])
            if k not in cache:
                avg = market.avg7(*k)
                if avg is None and not (r['refine'] or r['grade'] or r['slots'] is not None):
                    avg = VS.avg7(self.store, r['sid'], r['item_id'])
                cache[k] = avg
            r['avg7'] = cache[k]
            r['vs'] = VS.vs_avg(r['price'], r['avg7']) if r['kind'] == 'sale' else None
        self._find_popular()

    def _find_popular(self):
        """받은 판매 매물에서 가장 많은 장비의 국민 옵션(요청 없음). 장비가 아니면 숨김."""
        ids = [r['item_id'] for r in self.rows if r['kind'] == 'sale']
        top = max(set(ids), key=ids.count) if ids else None
        self.popular = PO.popular_config(self.rows, top) if top and PO.is_equipment(self.item_db, top) else None
        if self.popular:
            self.popular['item_id'] = top
            # 어떤 구성인지 보이게 이름까지 — '많은 구성만'만 있으면 무엇을 거르는지 알 수 없다
            self.only_popular.setText('많은 구성만: %s (%d/%d건)'
                                      % (self.popular['name'], self.popular['count'], self.popular['total']))
        # 장비 검색 결과는 제련·슬롯이 섞여 가격이 들쭉날쭉하므로 가장 많은 구성만 먼저 보여 준다(끄면 전체)
        self.only_popular.setChecked(bool(self.popular))
        self.only_popular.setVisible(bool(self.popular))

    def _filtered(self):
        kinds = tuple(k for k, box in (('sale', self.sale), ('buy', self.buy)) if box.isChecked())
        only = self.current['item_id'] if self.only_item.isChecked() and self.current else None
        rows = VS.filter_rows(self.rows, kinds, self.refine.value(), VS.parse_zeny(self.pmin.text()),
                              VS.parse_zeny(self.pmax.text()), only)
        p = self.popular
        if p and self.only_popular.isChecked():
            rows = [r for r in rows if r['item_id'] == p['item_id'] and PO.config_of(r['name']) == PO.config_of(p['name'])]
        return VS.sort_rows(rows, self.sort.currentData())

    def _apply(self):
        for w in (self.pmin, self.pmax):
            bad = bool(w.text().strip()) and VS.parse_zeny(w.text()) is None
            w.setStyleSheet('border: 1px solid #FF7A7A;' if bad else '')
        if self.result is None:
            return
        keep = self.current
        rows = self._filtered()
        self.table.set_rows(rows)
        self.cards.set_rows(rows)
        if keep in rows:
            self.table.selectRow(rows.index(keep))
        s = VS.summary(rows)
        res = self.result
        more = res.get('more', res['last'] > res['pages'])
        self.btn_more.setEnabled(bool(more) and res['pages'] < VS.MAX_PAGES and self.task.future is None)
        parts = ['<b>매물 %d건</b> (판매 %d · 구매 %d)' % (s['count'], s['sale'], s['buy'])]
        if s['low']:
            parts.append('판매 최저 <b style="color:#FFD479">%s</b> · 중간값 %s' % (comma(s['low']), comma(s['median'])))
        if s.get('buy_high'):
            parts.append('구매 최고 <b style="color:#8FC2FF">%s</b>(사겠다는 노점)' % comma(s['buy_high']))
        parts.append('%d쪽 받음(%d건)%s' % (
            res['pages'], len(self.rows),
            ' — <span style="color:#F0B541">뒤에 더 있습니다 · [더 받기]를 누르세요(비싼 매물·높은 제련은 뒤쪽에 있습니다)</span>'
            if more else ' · 끝까지 받음'))
        parts.append('%s 받음%s' % (_when(res['fetched_at']), ' · 30분 안에 받은 결과 재사용' if res.get('cached') else ''))
        if len(rows) != len(self.rows):
            parts.append('거르기로 %d건 숨김' % (len(self.rows) - len(rows)))
        self.summary.setText(' │ '.join(parts))

    def _selected(self, *_):
        rows = self.table.selected_rows()
        self.current = rows[0] if rows else None
        for b in self.buttons.values():
            b.setEnabled(self.current is not None and self.task.future is None)
        if self.current is None:
            self.base_options.hide()
            self.detail.setText('매물을 고르면 장비 사양과 카드·인챈트가 보입니다.')
            self.equipment_options.set_detail(None)
            return
        self._show(self.current, self.current.get('_detail'))

    def _show(self, r, det):
        e = html.escape
        lines = ['<p style="font-size:15px"><b>%s</b></p>' % e(r['name']),
                 '<table cellpadding="7" cellspacing="4"><tr>%s</tr></table>' % ''.join(
                     '<td bgcolor="#D7E3F2"><font color="#132034"><b>%s</b></font></td>' % e(part)
                     for part in spec_text(r).split(' · ')),
                 '<p><span style="font-size:18px; color:#FFD479"><b>%s Z</b></span> &nbsp;%s × %s</p>' % (
                     comma(r['price']), '판매' if r['kind'] == 'sale' else '구매', comma(r['qty']))]
        base_item = (getattr(self.item_db, 'items', {}) or {}).get(r['item_id'])
        if base_item and base_item.get('name'):
            lines.append('<p>기본 아이템: %s</p>' % e(base_item['name']))
        if r.get('avg7'):
            lines.append('<p>7일 평균 %s Z · <b>%s</b></p>' % (comma(int(r['avg7'])), _vs(r.get('vs')) if r.get('vs') is not None
                                                                else '구매 노점'))
        else:
            lines.append('<p style="color:#9aa4b2">저장된 7일 시세 없음 — [시세 받기]</p>')
        lines.append('<p>서버 %s<br>노점 %s</p>' % (e(r['server']), e(r['shop'] or '이름 없음')))
        if det is None:
            lines.append('<p style="color:#CAD5E4">카드·인챈트 자동 조회 상태: %s</p>' %
                         e(r.get('_detail_error') or '조회 중 / 대기 중'))
        else:
            where = det.get('map') or ''
            lines.append('<p>캐릭터 %s<br>위치 <b>%s</b>%s</p>' % (
                e(det.get('character') or '-'), e(self.map_name(where) if where else '-'),
                ' (%s)' % e(where) if where and where not in self.map_name(where) else ''))
            for label, key in (('속성', 'element'), ('제조자', 'maker')):
                if det.get(key):
                    lines.append('<p>%s %s</p>' % (label, e(det[key])))
            lines.append('<p style="color:#9aa4b2">공식 사이트는 맵까지만 알려 줍니다(좌표 없음).</p>')
        self.detail.setText(''.join(lines))
        self.base_options.setPlainText('장비 자체 옵션 · 조건 원문\n\n' + item_description(self.item_db, r['item_id']))
        self.base_options.show()
        self.equipment_options.set_detail(det)

    def _copy(self, what):
        r = self.current
        if r is None:
            return
        text = str(r['price']) if what == 'price' else (r['shop'] or '')
        QGuiApplication.clipboard().setText(text)
        self.status.setText('복사했습니다: %s' % (comma(r['price']) if what == 'price' else text))

    def add_watch(self):
        r = self.current
        if r is None:
            return
        cfg = dict(self.s.get('deal_watch') or {})
        items = list(cfg.get('items') or [])
        new = dict(item_id=r['item_id'], name=self._item_name(r), percent=15.0)
        if PO.is_equipment(self.item_db, r['item_id']):
            # 장비는 구성끼리만 비교: 이 아이템의 국민 옵션, 없으면 고른 매물의 구성(관심 매물 알림 창에서 바꿀 수 있음)
            p = self.popular if self.popular and self.popular['item_id'] == r['item_id'] else None
            new['query'] = p['name'] if p else r['name']
        if any(i.get('item_id') == r['item_id'] and i.get('query') == new.get('query') for i in items):
            self.status.setText('이미 관심 매물 알림에 있습니다.')
            return
        if len(items) >= MAX_WATCH_ITEMS:
            QMessageBox.information(self, '관심 매물 알림', '관심 아이템은 최대 %d개입니다.' % MAX_WATCH_ITEMS)
            return
        try:
            cfg['items'] = validate_watchlist(items + [new])
        except ValueError as exc:
            QMessageBox.warning(self, '관심 매물 알림', str(exc))
            return
        self.s['deal_watch'] = cfg
        self.s.save()
        if self.on_watch_changed:
            self.on_watch_changed()
        self.status.setText('%s 을(를) 관심 매물 알림에 추가했습니다(7일 평균보다 15%% 싸면 알림%s).' % (
            new.get('query') or new['name'], '' if cfg.get('enabled') else ' · 알림은 [관심 매물 알림]에서 켜야 동작'))

    # ------------------------------------------------------------ 최근 검색
    def _remember(self, text):
        cfg = dict(self.s.get('vend_search') or {})
        recent = [t for t in cfg.get('recent') or [] if t != text]
        cfg.update(recent=[text] + recent[:RECENT_MAX - 1], server=self.server.currentData(),
                   all_words=self.all_words.isChecked(), pages=self.pages.value())
        self.s['vend_search'] = cfg
        try:
            self.s.save()
        except Exception:
            pass
        self._render_recent()

    def _render_recent(self):
        while self.recent_row.count():
            w = self.recent_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        recent = (self.s.get('vend_search') or {}).get('recent') or []
        if not recent:
            return
        self.recent_row.addWidget(QLabel('최근:'))
        for text in recent:
            b = QPushButton(text)
            b.setFlat(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, t=text: (self.query.setText(t), self.search()))
            self.recent_row.addWidget(b)
        self.recent_row.addStretch(1)

    def closeEvent(self, e):
        self.stop()
        self._closed = True
        self.auto_retry.stop()
        self._auto_generation += 1
        super().closeEvent(e)
