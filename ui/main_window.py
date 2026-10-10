"""메인 화면: 최신 Replay 자동 로드 → 시세 연결 → 돈 되는 아이템 보기."""
import datetime
import logging
import os
import threading
import traceback

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QTableView, QTableWidget,
                               QTableWidgetItem,
                               QTabWidget, QToolButton, QVBoxLayout, QWidget)

from config.settings import APP_NAME, APP_SUBTITLE, APP_TITLE, APP_VERSION, BASE_DIR, PRICE_BASIS
from core import analysis
from core.items import ItemDB
from database.database import Database
from services import export_service, replay_service
from services.market_service import MarketService
from services.replay_analysis_service import STAGES, ReplayAnalysisService
from ui.pages.compare_page import ComparePage
from ui.pages.dashboard_page import DashboardPage
from ui.pages.dev_page import DevPage
from ui.pages.history_page import HistoryPage
from ui.pages.replay_page import ReplayPage
from navigator.guide_service import GuideService
from navigator.importers.csv_importer import apply_aliases
from navigator.player_state import from_analysis
from ui.pages.guide_admin_page import GuideAdminPage
from ui.pages.guide_editor import GuideEditor
from ui.pages.navigator_page import NavigatorPage
from ui.pages.live_page import LivePage
from core.display import DisplayNames
from live.buffs import BuffTimerManager, mmss, replay_rows
from live.learn import BuffOriginLearner
from live.status import StatusMonitor
from utilities.alerts import AlertManager, AlertRule
from ui.settings_dialog import SettingsDialog
from ui.completer import attach_completer, set_words
from ui.table_model import C_AVG, C_LOW, C_NAME, C_QTY, C_VALUE, Formatter, ItemFilterProxy, ItemTableModel, comma

log = logging.getLogger(__name__)
VALUE_FILTERS = [('금액 전체', 0), ('1만 Z 이상', 10_000), ('10만 Z 이상', 100_000), ('100만 Z 이상', 1_000_000),
                 ('1,000만 Z 이상', 10_000_000), ('사용자 지정', -1)]


class ScrollTabs(QTabWidget):
    """각 페이지를 스크롤 영역에 넣어, 내용이 커도 창을 모니터보다 작게 줄일 수 있게 한다.
    addTab/setCurrentWidget/currentWidget/indexOf/widget 은 원래 페이지 기준 그대로 쓴다."""

    def __init__(self):
        super().__init__()
        self._scroll = {}                        # page → QScrollArea

    def addTab(self, page, title):
        sa = QScrollArea()
        sa.setObjectName('pageScroll')
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(page)
        self._scroll[page] = sa
        return super().addTab(sa, title)

    def _outer(self, page):
        return self._scroll.get(page, page)

    def widget(self, i):
        w = super().widget(i)
        return w.widget() if isinstance(w, QScrollArea) and w.objectName() == 'pageScroll' else w

    def currentWidget(self):
        return self.widget(self.currentIndex())

    def setCurrentWidget(self, page):
        super().setCurrentWidget(self._outer(page))

    def indexOf(self, page):
        return super().indexOf(self._outer(page))


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)


class Task(QRunnable):
    """백그라운드 작업. fn(progress_cb) 결과를 done으로 전달."""

    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals = fn, _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.fn(self.signals.progress.emit))
        except Exception as e:
            log.error('작업 실패\n%s', traceback.format_exc())
            self.signals.error.emit(str(e) or e.__class__.__name__)


class Card(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName('card')
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        t = QLabel(title)
        t.setObjectName('cardTitle')
        self.value = QLabel('-')
        self.value.setObjectName('cardValue')
        v.addWidget(t)
        v.addWidget(self.value)


class MainWindow(QMainWindow):
    def __init__(self, settings, initial_replay=None):
        super().__init__()
        self.s = settings
        self.initial_replay = initial_replay
        self.setWindowTitle(APP_TITLE)
        # 처음 크기는 모니터 작업 영역의 90%를 넘지 않게
        avail = self.screen().availableGeometry()
        self.resize(min(1400, int(avail.width() * 0.9)), min(860, int(avail.height() * 0.9)))
        self._ui_scale = 1.0
        self._ui_zoom = float(settings.get('ui_zoom') or 1.0)
        self._scale_timer = QTimer(self, singleShot=True, interval=150, timeout=self._apply_scale)
        self.pool = QThreadPool.globalInstance()
        self.item_db = ItemDB(settings['ro_dir'], os.path.join(BASE_DIR, 'data', 'item_db.json'))
        try:
            self.db = Database(settings.abs_path('db_path'))
        except Exception:
            log.exception('DB 열기 실패, 임시 메모리 DB 사용')
            self.db = Database(':memory:')
            QTimer.singleShot(500, lambda: self._error('DB 오류', 'DB 파일을 열 수 없어 임시 DB로 실행합니다.\n'
                                                                  '(시세 캐시가 저장되지 않음)'))
        self.market = MarketService(self.db, settings)
        self.analysis = ReplayAnalysisService(settings, self.item_db, self.db, self.market,
                                              os.path.join(BASE_DIR, 'data'))
        self._stage_names = None
        self._watch_seen = None
        self.guides = GuideService(os.path.join(BASE_DIR, 'data', 'guides'), self.db, self.market,
                                   self.analysis.history, self.item_db, self.analysis.gamedata.maps, settings)
        apply_aliases(self.guides.repo)
        self.guides.search_engine.build()
        self.nav_overlay = None
        self.live = None
        self.hud = None
        self._last_state = None
        self.loot_alert = None
        self.settlement_dialog = None
        self.alerts = AlertManager(notify=self._notify)
        self.buffs = BuffTimerManager(settings, alert=self._buff_alert)
        self.display = DisplayNames(self.analysis.gamedata, self.item_db)
        self.learner = BuffOriginLearner(os.path.join(BASE_DIR, 'data', 'buff_origins.json'),
                                         is_internal=lambda efst: self.display.status(efst).internal or efst == 46)
        self.status_monitor = StatusMonitor(settings, alert=self._status_alert)
        self.status_rows = []
        from ui.buff_overlay import IconMap
        from ui.icons import IconResolver
        self.buff_icons = IconMap(os.path.join(BASE_DIR, 'data', 'buff_icons.json'))
        self.icons = IconResolver(self.buff_icons, os.path.join(BASE_DIR, 'data', 'icons'))
        self.buff_overlay = None
        self.storage_overlay = None
        self.storage_grid_overlay = None
        for key, msg in (('hp', '⚠ LOW HP'), ('sp', '⚠ LOW SP'), ('weight', '⚠ WEIGHT')):
            self.alerts.add(AlertRule(key, msg, threshold=None, repeat_sec=settings.get('alert_cooldown', 30)))
        self._apply_alert_thresholds()
        self.fmt = Formatter(self.item_db)
        self.replay = None
        self.entries = []
        self.prices = {}
        self.rows = []
        self.busy = False
        self.cancel = threading.Event()
        self._build()
        self._wrap_long_labels()
        self.main_tabs.currentChanged.connect(lambda _i: self._wrap_long_labels())
        QTimer.singleShot(3000, self._wrap_long_labels)          # 데이터가 채워진 뒤 길어진 문구
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self._watch_tick)
        self._apply_watch()
        self.buff_timer = QTimer(self, interval=500, timeout=self.refresh_buffs)
        self.buff_timer.start()
        from ui.economy_controller import install_economy_tools
        install_economy_tools(self)
        QTimer.singleShot(0, self.startup)

    # ------------------------------------------------------------------ UI
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # 상단 바: 같은 기능을 여러 버튼으로 늘어놓지 않고 [Replay] [시세] [오버레이] 세 갈래로 묶는다.
        top = QHBoxLayout()
        top.setSpacing(8)
        self.replay_label = QLabel('최근 Replay: -')
        self.replay_label.setObjectName('replayLabel')
        self.replay_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.replay_label, 1)
        replay_menu = QMenu(self)
        replay_menu.addAction('Replay 파일 선택...', self.choose_file)
        replay_menu.addAction('현재 Replay 새로고침', self.reload_replay)
        replay_menu.addSeparator()
        replay_menu.addAction('Replay 폴더 변경...', self.change_folder)
        replay_menu.addSeparator()
        replay_menu.addAction('오늘의 사냥 결산...', self.open_settlement)
        self.btn_latest = self._menu_button('최신 Replay 불러오기', self.load_latest, replay_menu, top)
        self.btn_latest.setProperty('primary', True)          # 화면당 주 버튼 하나(파랑)
        self.btn_reload = self.btn_latest
        price_menu = QMenu(self)
        price_menu.addAction('선택한 아이템만 새로고침', lambda: self.refresh_prices(selected=True))
        price_menu.addAction('노점 시세 비교 (공식 홈페이지)...', self.open_vending)
        price_menu.addSeparator()
        price_menu.addAction('시세 CSV 가져오기...', self.import_csv)
        price_menu.addAction('엑셀로 내보내기...', self.export)
        self.btn_price = self._menu_button('시세 새로고침', lambda: self.refresh_prices(selected=False),
                                           price_menu, top)
        self.btn_bulk = QPushButton('전체 시세 받기')
        self.btn_bulk.setToolTip('가진 아이템 전부의 공식 노점 시세를 뒤에서 차례로 받습니다. 받는 대로 표에 채워지고, '
                                 '멈췄다가 다시 누르면 이어서 받습니다.')
        self.btn_bulk.clicked.connect(self.toggle_bulk_prices)
        top.addWidget(self.btn_bulk)
        self.btn_overlay = QToolButton()
        self.btn_overlay.setText('오버레이')
        self.btn_overlay.setPopupMode(QToolButton.InstantPopup)
        self.btn_overlay.setMenu(self.overlay_menu(self.btn_overlay))
        top.addWidget(self.btn_overlay)
        self._button('설정', self.open_settings, top)
        from ui.update_controller import UpdateController
        self.updates = UpdateController(self, BASE_DIR)
        top.addWidget(self.updates.button)
        self._button('정보', self.show_about, top)
        v.addLayout(top)

        # 메인 탭: Replay 분석 플랫폼 + 기존 창고 분석
        self.main_tabs = ScrollTabs()
        self.main_tabs.setObjectName('mainTabs')                # 주 탭은 한 단계 큰 색 버튼(ui/theme.py)
        v.addWidget(self.main_tabs, 1)
        self.dashboard_page = DashboardPage(self)
        self.replay_page = ReplayPage(self)
        self.compare_page = ComparePage(self)
        self.history_page = HistoryPage(self)
        self.dev_page = DevPage(self)
        self.navigator_page = NavigatorPage(self)
        self.live_page = LivePage(self)
        self.guide_admin_page = GuideAdminPage(self)
        from ui.pages.macro_page import MacroPage
        self.macro_page = MacroPage(self)
        self.storage_page = QWidget()
        sv = QVBoxLayout(self.storage_page)
        sv.setContentsMargins(0, 0, 0, 0)
        for page, title in ((self.dashboard_page, '홈'), (self.live_page, 'LIVE'),
                            (self.navigator_page, '네비게이션'),
                            (self.replay_page, '리플레이 분석'),
                            (self.compare_page, '빌드 비교'), (self.history_page, '기록'),
                            (self.storage_page, '창고 · 시세'), (self.macro_page, '매크로')):
            self.main_tabs.addTab(page, title)
        self._toggle_dev_tab()
        self.main_tabs.setCurrentWidget(self.replay_page)

        # 요약 카드
        cards = QGridLayout()
        self.cards = {}
        for i, (key, title) in enumerate([('kinds', '전체 아이템 종류'), ('quantity', '전체 아이템 수량'),
                                          ('priced', '시세 조회된 아이템'), ('total', '예상 총 판매금액'),
                                          ('창고', '창고 예상 자산'), ('인벤토리', '인벤토리 예상 자산'),
                                          ('카트', '카트 예상 자산')]):
            self.cards[key] = Card(title)
            cards.addWidget(self.cards[key], 0, i)
        storage_note = QLabel('리플레이에서 읽은 보유 아이템과 저장된 시세를 함께 봅니다. 시세가 없는 것은 0이 아니라 - 로 둡니다.')
        storage_note.setObjectName('cardTitle')
        sv.addWidget(storage_note)
        sv.addLayout(cards)

        self.tabs = QTabWidget()
        sv.addWidget(self.tabs, 1)

        # 탭1: 아이템 목록
        t1 = QWidget()
        tv = QVBoxLayout(t1)
        fb = QHBoxLayout()
        self.loc = QComboBox()
        self.loc.currentTextChanged.connect(self._apply_filters)
        fb.addWidget(self.loc)
        self.price_state = QComboBox()
        for label, key in (('시세 전체', 'all'), ('시세 있는 것만', 'priced'), ('시세 없는 것만', 'unpriced')):
            self.price_state.addItem(label, key)
        self.price_state.currentIndexChanged.connect(self._apply_filters)
        fb.addWidget(self.price_state)
        self.sellable = QPushButton('💰 팔 만한 것만')
        self.sellable.setCheckable(True)
        self.sellable.setObjectName('sellable')
        self.sellable.toggled.connect(self._apply_filters)
        fb.addWidget(self.sellable)
        self.value_filter = QComboBox()
        for label, val in VALUE_FILTERS:
            self.value_filter.addItem(label, val)
        self.value_filter.currentIndexChanged.connect(self._apply_filters)
        fb.addWidget(self.value_filter)
        self.custom_value = QSpinBox(minimum=0, maximum=2_000_000_000, singleStep=10_000, suffix=' Z')
        self.custom_value.setGroupSeparatorShown(True)
        self.custom_value.setEnabled(False)
        self.custom_value.valueChanged.connect(self._apply_filters)
        fb.addWidget(self.custom_value)
        self.search = QLineEdit(placeholderText='아이템명 또는 아이템 ID 검색')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filters)
        fb.addWidget(self.search, 1)
        self.basis = QComboBox()
        for k, label in PRICE_BASIS.items():
            self.basis.addItem(label, k)
        self.basis.setCurrentIndex(list(PRICE_BASIS).index(self.s['price_basis']))
        self.basis.currentIndexChanged.connect(self._basis_changed)
        fb.addWidget(self.basis)
        tv.addLayout(fb)

        self.model = ItemTableModel(self.fmt)
        self.search_completer = attach_completer(self.search)          # 표에 있는 아이템 이름 자동완성
        self.model.modelReset.connect(lambda: set_words(self.search_completer, [r.entry.name for r in self.model.rows]))
        self.proxy = ItemFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.doubleClicked.connect(self._double_clicked)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        for c, w in enumerate([80, 280, 100, 80, 64, 240, 110, 110, 110, 76, 140, 130]):   # 글자 15px 기준
            self.table.setColumnWidth(c, w)
        hh.setStretchLastSection(True)
        tv.addWidget(self.table, 1)
        self.count_label = QLabel()
        tv.addWidget(self.count_label)
        self.tabs.addTab(t1, '아이템 목록')

        # 탭2: 자산 분석
        t2 = QWidget()
        av = QVBoxLayout(t2)
        self.asset_label = QLabel()
        self.asset_label.setObjectName('assetLabel')
        av.addWidget(self.asset_label)
        av.addWidget(QLabel('예상 자산 TOP 20 (위치 합산)'))
        self.top_table = QTableWidget(0, 5)
        self.top_table.setHorizontalHeaderLabels(['순위', '아이템명', '수량', '예상금액', '위치'])
        self.top_table.verticalHeader().setVisible(False)
        self.top_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.top_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        av.addWidget(self.top_table, 1)
        self.tabs.addTab(t2, '자산 분석')

        # 상태바
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.provider_label = QLabel()
        self.statusBar().addPermanentWidget(self.provider_label)
        self._update_provider_label()

        # 공통 색·탭·버튼·표는 ui/theme.py(앱 전체). 여기는 이 창의 이름 붙은 요소만(배율은 _apply_scale).
        for keys, step in ((('Ctrl+-',), -0.1), (('Ctrl+=', 'Ctrl++'), 0.1), (('Ctrl+0',), 0)):
            for k in keys:
                QShortcut(QKeySequence(k), self, activated=lambda s=step: self._zoom_by(s))
        self._local_qss = ("""
            QLabel#cardTitle { color: #B4BBC7; font-size: 14px; }
            QLabel#cardValue { font-size: 27px; font-weight: bold; }
            QLabel#replayLabel { font-size: 15px; }
            QLabel#assetLabel { font-size: 16px; padding: 8px; }
            QPushButton#sellable:checked { background: #16301F; color: #43C08A; border-color: #43C08A; font-weight: bold; }
            QLabel#replayInfo { font-size: 15px; padding: 4px; }
            QLabel#warnLabel { color: #F0B541; font-weight: bold; }
            QLabel#profitLabel { font-size: 15px; }
            QLabel#liveMode { font-size: 18px; font-weight: bold; padding: 4px 14px;
                              border: 1px solid #39414F; border-radius: 16px; }
            QLabel#liveMode[state="live"] { color: #43C08A; border-color: #43C08A; background: #16301F; }
            QLabel#liveMode[state="paused"] { color: #F0B541; border-color: #F0B541; background: #3A2A12; }
            QLabel#liveMode[state="off"] { color: #8E97A6; }
        """)
        self.setStyleSheet(self._local_qss)

    # ------------------------------------------------------------- 화면 배율(창 크기 + Ctrl +/-)
    def _wrap_long_labels(self, limit=400):
        """한 줄로 긴 안내 문구가 창 최소 폭을 모니터보다 넓게 만들던 문제(LIVE 탭 2,371px) 방지:
        폭이 limit을 넘는 설명 글(상단 Replay 표시 포함)은 줄바꿈. 카드 숫자·LIVE 표시는 제외."""
        for lab in self.findChildren(QLabel):
            if lab.objectName() in ('cardValue', 'liveMode') or lab.wordWrap():
                continue
            if lab.minimumSizeHint().width() > limit:
                lab.setWordWrap(True)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._scale_timer.start()                      # 끌어서 크기 바꾸는 동안은 기다렸다가 한 번만

    def _zoom_by(self, step):
        """Ctrl+- / Ctrl+= 는 10%씩, Ctrl+0 은 100%로. 설정에 저장."""
        from ui.theme import MAX_SCALE, MIN_SCALE
        self._ui_zoom = 1.0 if step == 0 else min(MAX_SCALE, max(MIN_SCALE, round(self._ui_zoom + step, 2)))
        self.s['ui_zoom'] = self._ui_zoom
        self._save_settings()
        self._apply_scale()

    def _apply_scale(self):
        """창이 기준(1400×860)보다 작으면 비례해서 줄이고(최소 75% = 글자 11px), 사용자 배율을 곱한다. 5% 단위."""
        from ui.theme import MAX_SCALE, MIN_SCALE, scale_qss, set_scale
        self._wrap_long_labels()
        auto = max(0.75, min(1.0, self.width() / 1400, self.height() / 860))
        scale = round(min(MAX_SCALE, max(MIN_SCALE, auto * self._ui_zoom)) * 20) / 20
        if abs(scale - self._ui_scale) < 0.01:
            return
        self._ui_scale = scale
        set_scale(QApplication.instance(), scale)
        self.setStyleSheet(scale_qss(self._local_qss, scale))
        self.status('화면 크기 %d%%  (Ctrl + - / Ctrl + = / Ctrl + 0)' % round(scale * 100), 3000)

    def _menu_button(self, text, slot, menu, layout):
        """누르면 기본 동작, ▾로 나머지 동작."""
        b = QToolButton()
        b.setText(text)
        b.setPopupMode(QToolButton.MenuButtonPopup)
        b.clicked.connect(slot)
        b.setMenu(menu)
        layout.addWidget(b)
        return b

    def overlay_menu(self, parent=None):
        """오버레이 메뉴 한 곳에서 만들어 상단 바·Live 탭·트레이가 같이 쓴다."""
        hk = self.s.get('hotkeys') or {}
        m = QMenu(parent or self)
        for label, key, slot in (('Live HUD', 'hud', self.toggle_hud),
                                 ('버프 타이머', 'buff', self.toggle_buff_overlay),
                                 ('창고 시세', 'storage', self.toggle_storage_overlay),
                                 ('Navigator', 'nav', lambda: self.toggle_overlay(self.navigator_page))):
            m.addAction('%s  (%s)' % (label, hk.get(key, '-')), slot)
        m.addSeparator()
        m.addAction('창고 슬롯 시세 켜기/끄기  (%s)' % hk.get('storage_grid', 'Ctrl+Alt+G'), self.toggle_storage_grid)
        m.addAction('창고 슬롯 배치 설정…', self.configure_storage_grid)
        m.addAction('노점 개설 추천가 켜기/끄기', self.toggle_vend_assist)
        m.addAction('버프 타이머 전체 일시정지  (%s)' % hk.get('pause', '-'),
                    lambda: self.pause_buffs(not self.buffs.paused))
        return m

    def _button(self, text, slot, layout):
        b = QPushButton(text)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _update_provider_label(self):
        p = self.market.provider
        self.provider_label.setText('시세 출처: %s%s' % (p.label, '  ⚠ 실제 가격 아님' if p.key == 'mock' else ''))

    # ------------------------------------------------------------- 노점 개설 추천가
    def start_vend_assist(self):
        """게임 '노점 개설' 창을 화면에서 찾아 옆에 추천 판매가 패널을 띄운다(리플레이 없어도 됨)."""
        if getattr(self, 'vend_watch', None):
            return
        from live.vend_watch import VendWatcher
        from ui.vend_panel import VendPanel
        self.vend_panel = VendPanel(self)
        self.vend_watch = VendWatcher(self.vend_panel.bridge.state.emit,
                                      self.s.get('live_process_names') or ('Ragexe.exe', 'Ragnarok.exe')).start()
        self.vend_panel.refresh_cb = self.vend_watch.force
        if self.vend_watch.error:
            self.status('노점 추천가: ' + self.vend_watch.error, 8000)

    def stop_vend_assist(self):
        if getattr(self, 'vend_watch', None):
            self.vend_watch.stop()
            self.vend_watch = None
        if getattr(self, 'vend_panel', None):
            self.vend_panel.close()
            self.vend_panel = None

    def toggle_vend_assist(self):
        on = not getattr(self, 'vend_watch', None)
        (self.start_vend_assist if on else self.stop_vend_assist)()
        self.s['vend_assist'] = on
        self.s.save()
        self.status('노점 개설 추천가 %s' % ('켜짐 — 게임에서 노점 개설 창을 열면 옆에 표시됩니다' if on else '꺼짐'), 6000)

    # ------------------------------------------------------------- 흐름
    def startup(self):
        QTimer.singleShot(4000, self.updates.check)             # 새 버전이 있으면 받을지 묻는다
        from services.catalog_icons import CatalogIconFetcher
        self.catalog_icons = CatalogIconFetcher(os.path.join(BASE_DIR, 'data'))
        self.catalog_icons.start()                              # 없는 스킬·상태 아이콘을 뒤에서 받는다
        if self.s.get('vend_assist', True):
            QTimer.singleShot(3000, self.start_vend_assist)
        folder = self.s['replay_dir']
        if not self.s['replay_dir_confirmed'] or not os.path.isdir(folder):
            if os.path.isdir(folder):
                ans = QMessageBox.question(self, 'Replay 폴더', 'Replay 폴더로 아래 경로를 사용할까요?\n\n%s\n\n'
                                           '[No]를 누르면 다른 폴더를 선택합니다.' % folder)
                if ans != QMessageBox.Yes:
                    folder = QFileDialog.getExistingDirectory(self, 'Replay 폴더 선택', folder) or folder
            else:
                folder = QFileDialog.getExistingDirectory(self, 'Replay 폴더 선택 (예: C:\\Gravity\\Ragnarok\\Replay)',
                                                          os.path.dirname(folder)) or folder
            self.s.update(replay_dir=folder, replay_dir_confirmed=True)
            self._save_settings()
        self.setup_tray()
        self.setup_hotkeys()
        if self.s.get('live_enabled'):
            self.start_live()
        if self.initial_replay and os.path.isfile(self.initial_replay):
            self._load(self.initial_replay)
        else:
            self.load_latest()

    def _save_settings(self):
        try:
            self.s.save()
        except Exception:
            log.exception('설정 저장 실패')
            self.statusBar().showMessage('설정 저장 실패 (logs 참고)', 5000)

    def _run(self, fn, on_done, label):
        if self.busy:
            self.statusBar().showMessage('이전 작업이 진행 중입니다.', 3000)
            return
        self.busy = True
        self.statusBar().showMessage(label)
        self.progress.setRange(0, 0)
        self.progress.show()
        t = Task(fn)
        t.signals.progress.connect(self._on_progress)
        t.signals.done.connect(lambda r: (self._finish(), on_done(r)))
        t.signals.error.connect(lambda msg: (self._finish(), self._error(label + ' 실패', msg)))
        t.setAutoDelete(False)
        self._task = t   # 작업이 끝날 때까지 시그널 객체 유지
        self.pool.start(t)

    def _finish(self):
        self.busy = False
        self._stage_names = None
        self.progress.hide()
        self.statusBar().clearMessage()

    def _on_progress(self, done, total):
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            if self._stage_names:
                self.progress.setFormat(self._stage_names[min(done, len(self._stage_names) - 1)])
            else:
                self.progress.setFormat('시세 %d/%d' % (done, total))

    def _error(self, title, msg):
        self.statusBar().showMessage('%s: %s' % (title, msg), 10000)
        QMessageBox.warning(self, title, '%s\n\n자세한 내용은 logs/app.log를 확인하세요.' % msg)

    def load_latest(self):
        folder = self.s['replay_dir']
        try:
            latest = replay_service.find_latest(folder) if os.path.isdir(folder) else None
        except Exception as e:
            log.exception('Replay 검색 실패')
            latest = None
        if not latest:
            self.replay_label.setText('최근 Replay: 없음  (%s)' % folder)
            self._error('Replay 파일 없음', '폴더에 .rrf 파일이 없습니다:\n%s\n\n게임에서 리플레이 녹화 후 창고를 열어 주세요.' % folder)
            return
        self._load(latest[0])

    def reload_replay(self):
        if self.replay:
            self._load(self.replay.path)
        else:
            self.load_latest()

    def _load(self, path):
        def work(progress):
            if not self.item_db.items:
                self.item_db.load()
            return self.analysis.analyze_file(path, stage=lambda i, _label: progress(i, len(STAGES) - 1))
        self._run(work, self._on_analysis, 'Replay 분석 중: %s' % os.path.basename(path))
        if self.busy:
            self._stage_names = STAGES

    def _on_analysis(self, res):
        """한 번의 파싱 결과로 창고 분석 + Replay 분석 화면을 모두 갱신."""
        try:                                   # 내가 쓴 스킬 직후에 생긴 효과를 후보로 모은다
            recording = '%s|%s' % (res.parsed.recorded_at, res.parsed.character)
            found = self.learner.observe_events(res.parsed.events, res.parsed.player_aid,
                                               recording_id=recording, context=res.parsed.server or 'unknown')
            if found:
                self.status('효과 이름 후보 %d건을 새로 찾았습니다 (Live 탭 > 버프 타이머에서 확인)' % found, 8000)
        except Exception:
            log.exception('효과 후보 학습 실패')
        self._learn_from_replay(res.parsed)
        if os.path.exists(res.path):
            self._watch_seen = (res.path, os.path.getmtime(res.path))
        self.replay_page.set_result(res)
        self.dashboard_page.set_latest(res.report)
        self.dev_page.set_parsed(res.parsed)
        self.refresh_history()
        if self.main_tabs.currentWidget() is not self.storage_page:
            self.main_tabs.setCurrentWidget(self.replay_page)
        self._on_replay(res.storage)
        self.apply_player_state(res)

    def _on_replay(self, res):
        self.replay = res
        mtime = datetime.datetime.fromtimestamp(res.modified).strftime('%Y-%m-%d %H:%M:%S')
        rec = res.recorded_at.strftime('%m-%d %H:%M') if res.recorded_at else '-'
        self.replay_label.setText('최근 Replay:  <b>%s</b>   수정시간 %s   (녹화 시작 %s · 서버 %s · 창고 %d개)'
                                  % (os.path.basename(res.path), mtime, rec, res.server or '?', res.storage_count))
        self.entries = analysis.merge_entries(res.entries) if self.s['merge_stackables'] else res.entries
        locs = ['전체', '창고'] + sorted({e.source for e in self.entries if e.source.startswith('창고')},
                                         key=lambda s: int(s[2:])) + ['인벤토리', '카트']
        cur = self.loc.currentText()
        self.loc.blockSignals(True)
        self.loc.clear()
        self.loc.addItems(locs)
        self.loc.setCurrentText(cur if cur in locs else '전체')
        self.loc.blockSignals(False)
        self.prices = self.market.cached(e.item_id for e in self.entries)
        self._merge_official()
        self._rebuild(sort=True)
        if res.warnings:
            self.statusBar().showMessage(' / '.join(res.warnings), 10000)
        self.refresh_prices(selected=False, force=False)

    def open_vending(self):
        """창고 표에서 고른 아이템(없으면 전체)을 노점 시세 비교 창으로. 같은 아이템은 수량을 합친다."""
        from ui.vending_dialog import VendingDialog
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        entries = [self.model.rows[r].entry for r in rows] or list(self.entries)
        if not entries:
            QMessageBox.information(self, '노점 시세 비교', '먼저 Replay를 불러와 창고 아이템을 읽으세요.')
            return
        merged = {}
        for e in entries:
            m = merged.setdefault(e.item_id, [e.item_id, e.name, 0])
            m[2] += e.quantity
        VendingDialog(self, [tuple(m) for m in merged.values()]).exec()

    def refresh_prices(self, selected=False, force=True):
        if not self.entries:
            return
        if selected:
            rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
            items = {self.model.rows[r].entry.item_id: self.model.rows[r].entry.name for r in rows}
            if not items:
                QMessageBox.information(self, '시세 새로고침', '표에서 아이템을 먼저 선택하세요.')
                return
        else:
            items = {e.item_id: e.name for e in self.entries}

        def work(progress):
            return self.market.fetch(items, force=force, progress=progress, cancel=self.cancel)

        def done(res):
            prices, failed = res
            self.prices.update(prices)
            self._merge_official()
            self._rebuild()
            msg = '시세 %d종 반영' % len(prices)
            if failed:
                msg += ' / 실패 %d종 (logs 참고)' % len(failed)
            self.statusBar().showMessage(msg, 6000)
        self._run(work, done, '시세 조회 중')

    def _merge_official(self):
        """공식 노점 시세(노점 검색·전체 시세 받기·게임 안 검색으로 쌓인 것)를 표의 시세에 합친다.
        같은 아이템에 로컬 시세가 있으면 더 최근에 받은 쪽을 쓴다. 반환: 바꾼 아이템 수."""
        pricer = self._loot_pricer()
        if pricer is None:
            return 0
        added = 0
        for item_id in {e.item_id for e in self.entries}:
            info = pricer.price_info(item_id)
            cur = self.prices.get(item_id)
            if info and (cur is None or not cur.has_price or (info.updated_at or 0) > (cur.updated_at or 0)):
                self.prices[item_id] = info
                added += 1
        return added

    def toggle_bulk_prices(self):
        """전체 시세 받기 / 멈추기."""
        pricer = self._loot_pricer()
        if pricer is None or not self.entries:
            QMessageBox.information(self, '전체 시세 받기', '먼저 Replay를 불러와 창고 아이템을 읽으세요.')
            return
        if getattr(self, 'bulk_prices', None) is None:
            from ui.bulk_prices import BulkPriceFetcher
            self.bulk_prices = BulkPriceFetcher(pricer, self)
            self.bulk_prices.progress.connect(self._bulk_progress)
            self.bulk_prices.waiting.connect(lambda msg: self.btn_bulk.setText('요청 제한 — 기다리는 중 · 멈추기'))
            self.bulk_prices.finished.connect(self._bulk_finished)
        if self.bulk_prices.running:
            self.bulk_prices.stop()
            self.btn_bulk.setEnabled(False)
            return
        from ui.bulk_prices import order
        items = order(self.entries, self.npc_price)
        todo = sum(1 for i, _ in items if not pricer.fresh(i))
        if not todo:
            self._merge_official()
            self._rebuild()
            self.status('모든 아이템 시세가 30분 안에 받은 것입니다.')
            return
        text = ('아이템 %d종의 공식 노점 시세를 받습니다 (요청 약 %d번, %d분쯤).' % (todo, todo * 2, max(1, todo * 2 * 13 // 600)),
                '', '· 받는 동안 노점 검색·추천가 같은 다른 시세 기능이 느려집니다.',
                '· 언제든 멈출 수 있고, 다음에 누르면 이어서 받습니다.', '', '시작할까요?')
        if QMessageBox.question(self, '전체 시세 받기', chr(10).join(text)) != QMessageBox.Yes:
            return
        self.bulk_prices.start(items)
        self.btn_bulk.setText('받는 중… · 멈추기')

    def _bulk_progress(self, done, total, name):
        self.btn_bulk.setText('받는 중 %d / %d · 멈추기' % (done, total))
        if done % 10 == 0:                                # 받는 대로 표에 채운다(10종마다)
            self._merge_official()
            self._rebuild()

    def _bulk_finished(self, fetched, skipped, complete):
        self._merge_official()
        self._rebuild()
        self.btn_bulk.setEnabled(True)
        self.btn_bulk.setText('전체 시세 받기')
        self.status('시세 %d종을 새로 받았습니다%s%s. 그래도 비어 있는 것은 지금 노점도 최근 거래도 없는 아이템입니다.' % (
            fetched, ' (최근에 받은 %d종은 건너뜀)' % skipped if skipped else '',
            '' if complete else ' — 멈췄습니다. 다시 누르면 이어서 받습니다'), 12000)

    def _rebuild(self, sort=False):
        basis = self.basis.currentData()
        self.rows = analysis.build_rows(self.entries, self.prices, basis)
        cfg=self.s.get('storage_grid') or {}
        if cfg.get('mode')=='auto' and cfg.get('enabled') and self.storage_grid_overlay is None:
            self.toggle_storage_grid()
        header = self.table.horizontalHeader()
        col, order = header.sortIndicatorSection(), header.sortIndicatorOrder()
        self.model.set_rows(self.rows)
        if sort or col < 0:
            col, order = {'name_asc': (C_NAME, Qt.AscendingOrder),
                          'quantity_desc': (C_QTY, Qt.DescendingOrder)}.get(self.s['default_sort'],
                                                                           (C_VALUE, Qt.DescendingOrder))
        self.table.sortByColumn(col, order)
        self._apply_filters()
        self._update_summary()
        self.update_storage_overlay()

    def _basis_changed(self):
        self.s['price_basis'] = self.basis.currentData()
        self._save_settings()
        self._rebuild()

    def _apply_filters(self, *_):
        p = self.proxy
        p.location = self.loc.currentText() or '전체'
        p.price_state = self.price_state.currentData()
        p.sellable_only = self.sellable.isChecked()
        p.sell_min = int(self.s['sell_min_value'])
        v = self.value_filter.currentData()
        self.custom_value.setEnabled(v == -1)
        p.min_value = self.custom_value.value() if v == -1 else v
        p.text = self.search.text().strip().lower()
        p.refresh()
        shown = [self.model.rows[p.mapToSource(p.index(i, 0)).row()] for i in range(p.rowCount())]
        self.count_label.setText('표시 %s행 / 전체 %s행   ·   표시 중 예상금액 합계 %s Z   ·   ⚠ %s'
                                 % (comma(len(shown)), comma(len(self.rows)),
                                    comma(sum(r.value or 0 for r in shown)), analysis.EQUIP_NOTE))

    def _update_summary(self):
        sm = analysis.summarize(self.rows)
        self.cards['kinds'].value.setText(comma(sm['kinds']))
        self.cards['quantity'].value.setText(comma(sm['quantity']))
        self.cards['priced'].value.setText('%s / %s' % (comma(sm['priced_kinds']), comma(sm['kinds'])))
        self.cards['total'].value.setText(comma(sm['total_value']) + ' Z')
        for k in ('창고', '인벤토리', '카트'):
            self.cards[k].value.setText(comma(sm['assets'].get(k, 0)) + ' Z')
        self.asset_label.setText('창고 %s Z   |   인벤토리 %s Z   |   카트 %s Z   |   <b>전체 %s Z</b>   (%s)'
                                 % tuple([comma(sm['assets'].get(k, 0)) for k in ('창고', '인벤토리', '카트')]
                                         + [comma(sm['total_value']), self.basis.currentText()]))
        top = analysis.top_items(self.rows, 20)
        self.top_table.setRowCount(len(top))
        for i, a in enumerate(top):
            vals = [str(i + 1), a['name'], comma(a['quantity']), comma(a['value']) + ' Z',
                    ', '.join(sorted(a['sources']))]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c in (0, 2, 3):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.top_table.setItem(i, c, it)

    # ------------------------------------------------------------- 기타 동작
    def _double_clicked(self, index):
        if index.column() not in range(C_LOW, C_AVG + 1):
            return
        e = self.model.rows[self.proxy.mapToSource(index).row()].entry
        prov = self.market.provider
        if not hasattr(prov, 'set_manual_price'):
            QMessageBox.information(self, '시세 입력', '현재 시세 Provider는 직접 입력을 지원하지 않습니다.')
            return
        price, ok = QInputDialog.getInt(self, '시세 직접 입력', '%s (ID %d)\n1개당 판매가(Z):' % (e.name, e.item_id),
                                        0, 0, 2_000_000_000, 1000)
        if ok and price > 0:
            try:
                prov.set_manual_price(e.item_id, price)
                self.market.invalidate([e.item_id])
                self.prices.update(self.market.fetch({e.item_id: e.name}, force=True)[0])
                self._rebuild()
            except Exception as ex:
                log.exception('수동 시세 저장 실패')
                self._error('시세 저장 실패', str(ex))

    def import_csv(self):
        prov = self.market.provider
        if not hasattr(prov, 'import_csv'):
            QMessageBox.information(self, 'CSV 가져오기', '로컬 시세 DB Provider에서만 사용할 수 있습니다.')
            return
        path, _ = QFileDialog.getOpenFileName(self, '시세 CSV (item_id,price[,quantity][,sale|buy])', BASE_DIR,
                                              'CSV (*.csv)')
        if not path:
            return
        try:
            ids, skipped = prov.import_csv(path)
            self.market.invalidate(ids)
            QMessageBox.information(self, 'CSV 가져오기', '%d종 가져옴 (건너뛴 줄 %d)' % (len(ids), skipped))
            self.refresh_prices(selected=False, force=False)
        except Exception as ex:
            log.exception('CSV 가져오기 실패')
            self._error('CSV 가져오기 실패', str(ex))

    def change_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Replay 폴더 선택', self.s['replay_dir'])
        if folder:
            self.s.update(replay_dir=folder, replay_dir_confirmed=True)
            self._save_settings()
            self.load_latest()

    def export(self):
        if not self.rows:
            QMessageBox.information(self, '내보내기', '먼저 Replay를 불러오세요.')
            return
        path, _ = QFileDialog.getSaveFileName(self, '분석 결과 내보내기', export_service.default_filename(BASE_DIR),
                                              'Excel (*.xlsx);;CSV (*.csv)')
        if not path:
            return
        p = self.proxy
        shown = [self.model.rows[p.mapToSource(p.index(i, 0)).row()] for i in range(p.rowCount())]
        meta = {'Replay': os.path.basename(self.replay.path) if self.replay else '',
                '서버': (self.replay.server if self.replay else '') or '',
                '시세 출처': self.market.provider.label, '가격 기준': self.basis.currentText(),
                '내보낸 시각': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                '필터': '%s / %s%s' % (p.location, self.price_state.currentText(),
                                      ' / 팔 만한 것만' if p.sellable_only else '')}
        try:
            export_service.export(path, shown, self.fmt, analysis.summarize(shown), meta)
            self.statusBar().showMessage('저장: %s' % path, 8000)
            if QMessageBox.question(self, '내보내기 완료', '%s\n\n파일을 열까요?' % path) == QMessageBox.Yes:
                os.startfile(path)
        except PermissionError:
            self._error('내보내기 실패', '파일이 다른 프로그램(엑셀 등)에서 열려 있습니다.')
        except Exception as ex:
            log.exception('내보내기 실패')
            self._error('내보내기 실패', str(ex))

    def show_about(self):
        QMessageBox.information(self, '%s 정보' % APP_NAME,
                                '<b>%s</b> — %s<br>버전 %s<br><br>'
                                '리플레이 분석 · LIVE 상태 모니터 · 창고와 시세 · 네비게이션을 한 프로그램에서 봅니다.<br>'
                                '게임 클라이언트에는 접근하지 않습니다(창 탐지 · 해당 창 캡처 · OCR · 리플레이 파일 읽기만 사용).'
                                % (APP_NAME, APP_SUBTITLE, APP_VERSION))

    def open_settings(self):
        d = SettingsDialog(self.s, self)
        if d.exec():
            old_ro, old_provider = self.item_db.lub_path, self.market.provider.key
            d.apply()
            if self.s['provider'] != old_provider:
                self.market.set_provider(self.s['provider'])
                self.prices = {}
                self._update_provider_label()
            self.basis.blockSignals(True)
            self.basis.setCurrentIndex(list(PRICE_BASIS).index(self.s['price_basis']))
            self.basis.blockSignals(False)
            self.item_db = ItemDB(self.s['ro_dir'], self.item_db.cache_path) \
                if os.path.join(self.s['ro_dir'], 'System', 'itemInfo_true.lub') != old_ro else self.item_db
            self.fmt.item_db = self.item_db
            self.analysis.gamedata.items = self.item_db
            self._toggle_dev_tab()
            self.apply_overlay_styles()
            self._apply_watch()
            self._apply_alert_thresholds()
            self.live_page.buff_panel.keep.setChecked(bool(self.s['buff_keep_expired']))
            if self.hud:
                self.hud.preset = self.s['hud_preset']
            self.live_page._toggle_inspector()
            self.load_latest()

    # ------------------------------------------------------------- 페이지 공용 기능
    def run(self, fn, on_done, label):
        self._run(fn, on_done, label)

    def status(self, msg, ms=6000):
        self.statusBar().showMessage(msg, ms)

    def safe(self, fn, ok_msg):
        try:
            fn()
            self.status(ok_msg)
        except PermissionError:
            self._error('저장 실패', '파일이 다른 프로그램(엑셀 등)에서 열려 있습니다.')
        except Exception as ex:
            log.exception('작업 실패')
            self._error('작업 실패', str(ex))

    def go_storage(self):
        self.main_tabs.setCurrentWidget(self.storage_page)

    def load_replay(self, path):
        self._load(path)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Replay 파일 선택', self.s['replay_dir'], 'Replay (*.rrf)')
        if path:
            self._load(path)

    def show_stored_report(self, rep):
        self.replay_page.result = None
        self.replay_page.show_report(rep)
        self.main_tabs.setCurrentWidget(self.replay_page)
        self.status('원본 Replay 파일이 없어 저장된 분석 결과를 표시합니다(타임라인 제외).')

    def open_compare(self, ids):
        self.compare_page.select_ids(set(ids))
        self.main_tabs.setCurrentWidget(self.compare_page)
        if len(ids) >= 2:
            self.compare_page.run_compare()

    def refresh_history(self):
        try:
            sessions = self.analysis.history.list()
        except Exception:
            log.exception('기록 조회 실패')
            sessions = []
        self.history_page.refresh(sessions)
        self.compare_page.refresh(sessions)
        self.dashboard_page.refresh()

    def _toggle_dev_tab(self):
        for page, title in ((self.dev_page, '개발자: 패킷'), (self.guide_admin_page, '개발자: Guide')):
            idx = self.main_tabs.indexOf(page)
            if self.s.get('developer_mode') and idx < 0:
                self.main_tabs.addTab(page, title)
            elif not self.s.get('developer_mode') and idx >= 0:
                self.main_tabs.removeTab(idx)

    # ------------------------------------------------------------- RO Navigator 연동
    def apply_player_state(self, res=None):
        """Replay 분석 결과 → Navigator PlayerState(직업/레벨/맵/인벤토리/카트/창고)."""
        res = res or self.replay_page.result
        if res is None:
            return
        try:
            self.guides.state = from_analysis(res, self.analysis.gamedata.job_name)
        except Exception:
            log.exception('PlayerState 생성 실패')
        self.navigator_page.refresh_home()
        if self.navigator_page.gid:
            self.navigator_page.open_guide(self.navigator_page.gid, record=False)

    def open_navigator_from_live(self):
        """LIVE 화면 상황을 네비게이션으로 넘긴다: 현재 맵 · 캐릭터 · 직업 · 레벨 · 보유 아이템."""
        snap = self.live.snapshot() if self.live else {}
        rp = (snap.get('replay_live') or {}) if isinstance(snap, dict) else {}
        map_id = rp.get('map') or (snap.get('map') if isinstance(snap, dict) else None)
        context = dict(map=map_id, character=rp.get('character'), job_id=rp.get('job_id'), level=rp.get('level'))
        self.main_tabs.setCurrentWidget(self.navigator_page)
        self.navigator_page.apply_live_context(context)
        where = self.display.map(map_id).text if map_id else '위치 확인 안 됨'
        self.status('네비게이션 열기 — 현재 위치: %s' % where)

    def show_navigator(self):
        self.main_tabs.setCurrentWidget(self.navigator_page)

    def edit_guide(self, guide):
        dlg = GuideEditor(self.guides.repo, guide, self)
        if dlg.exec():
            self.guides.search_engine.build()
            self.guide_admin_page.refresh()
            self.show_navigator()
            self.navigator_page.open_guide(dlg.saved.id)

    def show_item_in_storage(self, item_id):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.go_storage()
        self.tabs.setCurrentIndex(0)
        self.loc.setCurrentText('전체')
        self.search.setText(str(item_id))

    def apply_overlay_styles(self):
        """설정 창에서 오버레이 모양을 바꾸면 떠 있는 오버레이에 바로 반영(버프·HUD는 다음 갱신 때 반영)."""
        for w in (self.nav_overlay, getattr(self, 'vend_panel', None), self.storage_overlay):
            if w is not None and hasattr(w, 'apply_style'):
                w.apply_style()

    def toggle_overlay(self, page):
        from ui.overlay import NavOverlay
        if self.nav_overlay is None:
            self.nav_overlay = NavOverlay(self.s, self.analysis.gamedata.maps.name)
            self.nav_overlay.step_changed.connect(lambda i: page._goto_step(i))
            self.nav_overlay.completed.connect(lambda i: page.complete_step(i))
            self.nav_overlay.tracker.connect(lambda qs: self._on_quest_tracker(page, qs))
            from live.quest_watch import QuestWatcher
            ov = self.nav_overlay
            self.quest_watch = QuestWatcher(ov.tracker.emit,
                                            self.s.get('live_process_names') or ('Ragexe.exe', 'Ragnarok.exe'),
                                            active=lambda: ov.isVisible() and ov.auto).start()
        if self.nav_overlay.isVisible():
            self.nav_overlay.hide()
        else:
            if not page.detail:                        # 아직 연 Guide가 없으면 최근에 본 Guide
                recent = self.guides.repo.recent()
                if recent:
                    page.open_guide(recent[0], record=False)
            self.update_overlay(page)
            self.nav_overlay.show()

    def _on_quest_tracker(self, page, quests):
        """게임 퀘스트 알림창에서 읽은 퀘스트 → 맞는 챕터 STEP 으로 옮기고 '지금 할 일' 강조."""
        from core import quest_tracker as Q
        ov = self.nav_overlay
        if ov is None or not ov.auto:
            return
        guides = [g for g in self.guides.repo.all() if g.type == 'CHAPTER']
        if page.detail and page.detail['guide'].type != 'CHAPTER':
            guides.append(page.detail['guide'])         # 지금 보고 있는 일반 Guide 도 후보(다른 일반 퀘스트로는 안 넘어감)
        if getattr(self, 'quest_info', None) is None:
            from navigator.quest_info import QuestInfoDB
            self.quest_info = QuestInfoDB(self.s['ro_dir']).load()
        m = Q.match(quests, guides, (page.gid, page.step_index) if page.detail else None)
        if m:
            g, i, quest, _score = m
            if page.gid != g.id:
                page.open_guide(g.id, record=False)
            if page.step_index != i:
                page._goto_step(i)
            target = g.steps[i].map
        else:                                            # 가이드에 없는 퀘스트: 알림창 첫 퀘스트의 공식 정보만
            quest, target = (quests[0] if quests else None), ''
        if quest is None:
            ov.set_now(None)
            return
        # 공식 퀘스트 정보(게임 데이터): 알림창 제목 + 첫 목표 줄로 찾는다 → 지금 가야 할 NPC·좌표
        official = self.quest_info.find(quest.title, quest.objectives[0].text if quest.objectives else '')
        if not m and official is None:
            ov.set_now(None)
            return
        if official and official.goal:
            target = official.goal[1]
        live, hint = getattr(page, 'live_map', None), ''
        if target and live:
            hint = ('✓ 목표 맵에 있습니다' if live == target else
                    '→ 먼저 이동: %s' % self.analysis.gamedata.maps.name(target))
        ov.set_now(quest, hint, official)

    def update_overlay(self, page):
        if self.nav_overlay is not None and page.detail and page.detail['guide'].steps:
            self.nav_overlay.show_step(page.detail['guide'], page.step_index, page.detail['progress'][1])

    def _apply_watch(self):
        if self.s.get('auto_watch'):
            self.watch_timer.start(5000)
        else:
            self.watch_timer.stop()

    def _watch_tick(self):
        """Replay 폴더 감시: 새 파일/변경 감지 (게임 프로세스는 건드리지 않고 파일만 확인)."""
        try:
            latest = self.analysis.watch_candidates()
        except Exception:
            return
        if not latest or latest == self._watch_seen:
            return
        first = self._watch_seen is None
        self._watch_seen = latest
        if first:
            return
        self.status('새 Replay 감지: %s' % os.path.basename(latest[0]), 10000)
        if self.s.get('auto_analyze') and not self.busy:
            self._pending_watch = latest[0]          # 녹화가 끝난 뒤에 분석한다
            QTimer.singleShot(0, self._analyze_when_stable)

    def _analyze_when_stable(self):
        """녹화 중인 파일을 바로 열면 패킷 컨테이너가 없어 실패한다. 크기가 멈출 때까지 기다렸다 분석."""
        path = getattr(self, '_pending_watch', None)
        if not path or self.busy:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            self._pending_watch = None
            return
        if size and size == getattr(self, '_pending_size', None):
            self._pending_watch = self._pending_size = None
            self._load(path)
            return
        self._pending_size = size
        self.status('녹화가 끝나기를 기다리는 중: %s' % os.path.basename(path), 6000)
        QTimer.singleShot(5000, self._analyze_when_stable)

    # ------------------------------------------------------------- Buff Timer
    def _buff_rows(self):
        """수동 타이머 + Replay Buff를 상태 모니터가 쓰는 공통 형식으로."""
        rows = self.buffs.rows() + replay_rows(self.live_page.last_replay_snap, display=self.display)
        context = (self.live_page.last_replay_snap or {}).get('server') or 'unknown'
        for r in rows:                          # 이름을 모르는 효과에는 관측된 후보를 덧붙인다
            if r.get('efst') is not None and r['name'].startswith('알 수 없는 효과'):
                hint = self.learner.suggestion(r['efst'], self._skill_label, context=context)
                if hint:
                    r['suggest'] = hint
                    r['suggest_skill'] = self.learner.top(r['efst'], context=context)[0]
        rows.sort(key=lambda r: (r.get('remaining') is None, r.get('remaining') or 0))
        return rows

    def _skill_label(self, skill_id):
        """스킬 이름이 없으면 번호라도 알려 준다(게임에서 찾아볼 수 있게)."""
        name = self.display.skill(skill_id)
        return name.text if name.confidence != 'UNKNOWN' else '알 수 없는 스킬(번호 %s)' % skill_id

    def refresh_buffs(self):
        """수동 타이머 진행/알림 + Replay에서 확인된 효과를 상태 모니터·패널·오버레이에 반영."""
        self.buffs.tick()
        items = self.status_monitor.sync(self._buff_rows())
        self.status_rows = [i.to_row() for i in items]
        shown = [i.to_row() for i in self.status_monitor.visible(items)]
        self.live_page.buff_panel.refresh(self.status_rows, self.buffs.paused)
        self.live_page.update_status_strip(shown)
        if self.buff_overlay and self.buff_overlay.isVisible():
            self.buff_overlay.update_rows(shown, self.buffs.paused)

    def _status_alert(self, key, stage, text):
        """효과 종료 임박·종료·재사용 알림. 같은 효과가 계속 울리지 않게 AlertManager가 조절한다."""
        cfg = (self.s.get('status_alerts') or {}).get(key, {})
        rule = AlertRule('status_%s_%s' % (key, stage), text, sound=cfg.get('sound', ''),
                         popup=cfg.get('popup', bool(self.s.get('buff_windows_popup', False))))
        self.alerts.fire(rule)

    def toggle_buff_overlay(self):
        from ui.buff_overlay import BuffOverlay
        if self.buff_overlay is None:
            self.buff_overlay = BuffOverlay(self.s, self.buff_icons)
            self.buff_overlay.on_settings_changed = self.refresh_buffs
        self.buff_overlay.setVisible(not self.buff_overlay.isVisible())
        if self.buff_overlay.isVisible():
            self.refresh_buffs()

    def pause_buffs(self, on):
        self.buffs.set_paused(bool(on))
        self.status('버프 타이머 일시정지' if on else '버프 타이머 재개')
        self.refresh_buffs()

    def _buff_alert(self, key, msg, sound):
        self.alerts.fire(AlertRule(key, msg, sound=sound, popup=bool(self.s.get('buff_windows_popup', False))))

    # ------------------------------------------------------------- 창고 시세 Overlay
    def _storage_grid_context(self):
        replay = self.replay
        return '%s|%s|%s' % (replay.path, replay.server, replay.modified) if replay else ''

    def configure_storage_grid(self):
        from ui.storage_grid import StorageGridDialog, StorageGridOverlay
        if self.storage_grid_overlay:
            self.storage_grid_overlay.stop()
        if not any(r.entry.location_group == '창고' for r in self.rows):
            QMessageBox.information(self, '창고 슬롯 시세', '게임에서 창고를 연 기록이 포함된 Replay를 먼저 불러오세요.')
            return
        dialog = StorageGridDialog(self.s, self.rows, self._storage_grid_context(), self)
        if dialog.exec():
            if self.storage_grid_overlay:
                self.storage_grid_overlay.close()
                self.storage_grid_overlay.deleteLater()
            self.storage_grid_overlay = StorageGridOverlay(lambda: self.rows, self.s['storage_grid'], self._storage_grid_context,
                server_provider=lambda:getattr(self.replay,'server',None),status_callback=lambda msg:self.status(msg,5000))
            self.storage_grid_overlay.start()
            self.status('창고 시세 표시 준비됨. 게임 창으로 돌아가세요. 자동 모드는 창고를 다시 찾으며, 수동 모드는 배치를 확인해야 합니다.', 12000)

    def toggle_storage_grid(self):
        from core.storage_grid import labels
        from ui.storage_grid import StorageGridOverlay
        if self.storage_grid_overlay and self.storage_grid_overlay.enabled:
            self.storage_grid_overlay.stop()
            if (self.s.get('storage_grid') or {}).get('mode')=='auto':
                self.s['storage_grid']['enabled']=False;self.s.save()
            return
        cfg = self.s.get('storage_grid') or {}
        if cfg.get('mode')!='auto' and labels(self.rows, cfg, self._storage_grid_context()) is None:
            self.configure_storage_grid()
            return
        if self.storage_grid_overlay is None:
            self.storage_grid_overlay = StorageGridOverlay(lambda: self.rows, cfg, self._storage_grid_context,
                server_provider=lambda:getattr(self.replay,'server',None),status_callback=lambda msg:self.status(msg,5000))
        if cfg.get('mode')=='auto':cfg['enabled']=True;self.s.save()
        self.storage_grid_overlay.start()

    def toggle_storage_overlay(self):
        from ui.storage_overlay import StorageOverlay
        if self.storage_overlay is None:
            self.storage_overlay = StorageOverlay(self.s, on_refresh=lambda: self.refresh_prices(selected=False),
                                                  on_item=self.show_item_in_storage)
            self.storage_overlay.bind(self.update_storage_overlay)
        self.storage_overlay.setVisible(not self.storage_overlay.isVisible())
        if self.storage_overlay.isVisible():
            self.update_storage_overlay()

    def update_storage_overlay(self):
        ov = self.storage_overlay
        if ov is None or not ov.isVisible():
            return
        rows = analysis.overlay_rows(self.rows, ov.query(), ov.only_sellable(), int(self.s['sell_min_value']))
        total = sum(r.value or 0 for r in self.rows)
        note = '' if self.rows else 'Replay를 먼저 불러오세요'
        unpriced = sum(1 for r in self.rows if not r.has_price)
        ov.set_rows(rows, total, note or ('시세 없음 %d종 제외' % unpriced if unpriced else ''))

    # ------------------------------------------------------------- Passive Live
    def start_live(self):
        if self.live is not None:
            return
        from live.engine import LiveEngine
        try:
            self.live = LiveEngine(self.s, self.db, self.analysis.price_lookup, self.item_db, self.analysis.gamedata,
                                   os.path.join(BASE_DIR, 'data'), self.alerts)
            self.live.start()
            self.live_page.refresh_history()
        except Exception as ex:
            log.exception('Live 시작 실패')
            self.live = None
            self.status('Live 모드 시작 실패: %s' % ex)

    def stop_live(self):
        if self.live is not None:
            self.live.stop()
            self.live = None
            self.live_page.refresh_history()
        if self.hud:
            self.hud.hide()

    def toggle_live(self):
        (self.stop_live if self.live else self.start_live)()

    def toggle_hud(self):
        from ui.live_hud import LiveHud
        if self.hud is None:
            self.hud = LiveHud(self.s)
        self.hud.setVisible(not self.hud.isVisible())
        if self.live and self.hud.isVisible():
            self.update_hud(self.live.snapshot())

    def update_hud(self, snap):
        state = snap['state']
        if state != self._last_state:
            if state == 'WAITING' and self._last_state not in (None, 'WAITING'):
                self.live_page.refresh_history()              # 게임 종료 → 세션 저장됨
                if self.hud:
                    self.hud.hide()
            elif self._last_state in (None, 'WAITING') and state != 'WAITING':
                if self.s.get('live_auto_overlay') and (self.hud is None or not self.hud.isVisible()):
                    self.toggle_hud()
                if self.s.get('buff_overlay_auto') and (self.buff_overlay is None or not self.buff_overlay.isVisible()):
                    self.toggle_buff_overlay()
                if self.s.get('storage_overlay_auto') and (self.storage_overlay is None
                                                           or not self.storage_overlay.isVisible()):
                    self.toggle_storage_overlay()
            self._last_state = state
        vals = snap.get('values', {})
        rp = snap.get('replay_live') or {}
        self._feed_loot(rp)
        self._check_queue(rp)
        if rp.get('stage') == 'CONFIRMED':
            vals = dict(vals)
            for key in ('HP', 'SP', 'AP'):
                resource = rp.get('resources', {}).get(key, {})
                if resource.get('value') is not None and resource.get('max_value') and not resource.get('stale'):
                    vals[key + '_REGION'] = resource['value'] * 100 / resource['max_value']
        self.alerts.check('hp', vals.get('HP_REGION'))
        self.alerts.check('sp', vals.get('SP_REGION'))
        w = vals.get('WEIGHT_REGION')
        self.alerts.check('weight', -w if w is not None else None)      # 무게는 '이상'일 때 경고
        if self.hud and self.hud.isVisible():
            items = self.status_monitor.sync(self._buff_rows())
            self.hud.update_from(snap, [i.to_row() for i in self.status_monitor.visible(items)])
            if snap.get('window'):
                self.hud.follow(snap['window']['client'])
        if self.buff_overlay and self.buff_overlay.isVisible() and snap.get('window'):
            self.buff_overlay.follow(snap['window']['client'])
        if self.storage_overlay and self.storage_overlay.isVisible() and snap.get('window'):
            self.storage_overlay.follow(snap['window']['client'])

    # ------------------------------------------------------------- 득템 알림 · 오늘의 결산
    def _loot_pricer(self):
        """공식 노점 자료로 아이템 시세를 보는 도구(노점 도구가 준비된 뒤에만 있다)."""
        economy = getattr(self, 'economy', None)
        if economy is None:
            return None
        if getattr(self, '_pricer', None) is None:
            from services.loot_value import LootValue
            self._pricer = LootValue(economy.store, economy.client, lambda: economy.store.meta('server', '129'))
        return self._pricer

    def _learn_from_replay(self, pr):
        """리플레이에서 바로 쓸 수 있는 것: 게임 안 노점 검색 결과 → 시세, 스킬 목록 → 오버챠지 레벨."""
        try:
            economy = getattr(self, 'economy', None)
            if economy is not None:
                from services import ingame_market
                from services.hunting_profit import server_id
                sid = server_id(pr.server) or economy.store.meta('server', '129')
                saved = ingame_market.ingest(economy.store, sid, pr.events, pr.recorded_at, self.item_db.name)
                if saved:
                    self.status('게임 안 노점 검색 결과 %d종을 시세로 저장했습니다 (%s)' % (
                        len(saved), ', '.join(self.item_db.name(i) for i in saved[:3])), 8000)
            from core import loot_rows as LR
            level = LR.overcharge_level(pr.events)
            if level:
                seen = dict(self.s.get('overcharge_seen') or {})
                if seen.get(pr.character) != level:
                    seen[pr.character] = level
                    self.s['overcharge_seen'] = seen
                    if self.s.get('overcharge_auto', True):
                        self.s['overcharge_rate'] = LR.overcharge_rate(max(seen.values()))
                    self._save_settings()
        except Exception:
            log.exception('리플레이에서 시세·스킬 읽기 실패')

    def loot_icons(self):
        """획득 아이템 표들이 같이 쓰는 아이콘 묶음(없는 아이콘은 뒤에서 천천히 받아 온다)."""
        if getattr(self, '_loot_icons', None) is None:
            from ui.loot_table import ItemIcons
            self._loot_icons = ItemIcons(os.path.join(BASE_DIR, 'data', 'item_icons'), self)
        return self._loot_icons

    def npc_price(self, item_id):
        """상점에 팔 때 받는 값(설정한 오버차지 % 적용). 기준가를 모르면 None."""
        if getattr(self, '_npc_prices', None) is None:
            from core.loot_rows import default_npc_prices
            self._npc_prices = default_npc_prices(BASE_DIR)
        return self._npc_prices.sell(item_id, self.s.get('overcharge_rate', 24))

    def vend_price(self, item_id):
        """(노점 시세, 근거) — 저장된 공식 노점 자료만 본다. 모르면 (None, '')."""
        pricer = self._loot_pricer()
        return pricer.price(item_id) if pricer else (None, '')

    def _feed_loot(self, rp):
        if not rp or not (rp.get('gains') or rp.get('drops')):
            return
        if self.loot_alert is None:
            pricer = self._loot_pricer()
            if pricer is None:
                return
            from ui.loot_alert import LootAlert
            self.loot_alert = LootAlert(self.s, pricer, self.item_db.name, self._jackpot, self)
        self.loot_alert.feed(rp)

    def _check_queue(self, rp):
        """인던 대기 순번이 기준 이하로 내려오면 한 번 알린다(소리 + 음성)."""
        if getattr(self, 'queue_watch', None) is None:
            from core.queue_watch import QueueWatch
            self.queue_watch = QueueWatch()
        threshold = int(self.s['queue_alert_at'])
        text = self.queue_watch.check(rp.get('queue') if self.s['queue_alert'] else None, threshold)
        if text:
            self.alerts.fire(AlertRule('queue', text, sound=self._queue_sound(threshold)))

    def _queue_sound(self, threshold):
        """"대기열이 N 이하입니다" 음성 파일. 없으면 한 번 만들고, 못 만들면 기본 알림음('')."""
        from core.queue_watch import message
        path = os.path.join(BASE_DIR, 'data', 'sounds', 'queue_fast_%d.wav' % threshold)
        if not os.path.isfile(path):
            try:
                from utilities import voice
                voice.speak_to_wav(message(threshold), path, rate=4)       # 순번이 빨리 줄어든다 — 빠르게 읽는다
            except Exception as e:
                log.warning('대기열 음성을 만들지 못했습니다: %s', e)
                return ''
        return path

    def _jackpot(self, text, sound):
        self.alerts.fire(AlertRule('jackpot', text, sound=sound))

    def open_settlement(self):
        if self.settlement_dialog is None:
            from ui.settlement_dialog import SettlementDialog
            self.settlement_dialog = SettlementDialog(self, self.analysis.ledger, self._loot_pricer(), self)
        self.settlement_dialog.reload()
        self.settlement_dialog.show()
        self.settlement_dialog.raise_()
        self.settlement_dialog.activateWindow()

    def _apply_alert_thresholds(self):
        self.alerts.rules['hp'].threshold = self.s.get('alert_hp') or None
        self.alerts.rules['sp'].threshold = self.s.get('alert_sp') or None
        wt = self.s.get('alert_weight')
        self.alerts.rules['weight'].threshold = -wt if wt else None
        for r in self.alerts.rules.values():
            r.repeat_sec = self.s.get('alert_cooldown', 30)

    def _notify(self, msg):
        self.status(msg, 8000)
        if getattr(self, 'tray', None):
            self.tray.showMessage(APP_NAME, msg)
        if self.hud and self.hud.isVisible():
            self.hud.alert.setText(msg)
            self.hud.alert.setVisible(True)

    def setup_tray(self):
        from PySide6.QtWidgets import QSystemTrayIcon
        from ui.theme import app_icon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        m = QMenu(self)
        m.addAction('%s 열기' % APP_NAME, lambda: (self.showNormal(), self.activateWindow()))
        overlays = m.addMenu('오버레이')
        overlays.addActions(self.overlay_menu(m).actions())
        m.addAction('Live 켜기/끄기', self.toggle_live)
        m.addAction('오늘의 사냥 결산', self.open_settlement)
        m.addSeparator()
        m.addAction('종료', self.close)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: (self.showNormal(), self.activateWindow()) if r == QSystemTrayIcon.DoubleClick else None)
        self.tray.setToolTip(APP_TITLE)
        self.tray.show()

    def setup_hotkeys(self):
        """전역 단축키: HUD 표시/숨김, HUD 프리셋 전환(Compact), Navigator 오버레이. 게임 입력 없음."""
        from utilities.hotkeys import HotkeyManager
        self._hotkey_queue = []
        binds = self.s.get('hotkeys') or {'hud': 'Ctrl+Alt+H', 'compact': 'Ctrl+Alt+C', 'nav': 'Ctrl+Alt+N'}
        try:
            self.hotkeys = HotkeyManager(binds, self._hotkey_queue.append).start()
        except Exception:
            log.exception('전역 단축키 등록 실패')
            self.hotkeys = None
        self._hk_timer = QTimer(self, interval=150, timeout=self._drain_hotkeys)
        self._hk_timer.start()
        QTimer.singleShot(1500, self._report_hotkey_failures)      # 등록은 단축키 스레드에서 끝난다

    def _report_hotkey_failures(self):
        failed = getattr(self.hotkeys, 'failed', None)
        if failed:
            self.status('다른 프로그램이 쓰고 있어 등록하지 못한 단축키: %s (설정 파일 hotkeys에서 변경)'
                        % ', '.join(failed), 12000)

    def _drain_hotkeys(self):
        while self._hotkey_queue:
            self._hotkey(self._hotkey_queue.pop(0))

    def _hotkey(self, name):
        if name == 'hud':
            self.toggle_hud()
        elif name == 'compact' and self.hud:
            self.hud.preset = 'compact' if self.hud.preset != 'compact' else self.s.get('hud_preset', 'hunt')
        elif name == 'nav' and self.navigator_page.detail:
            self.toggle_overlay(self.navigator_page)
        elif name == 'buff':
            self.toggle_buff_overlay()
        elif name == 'storage':
            self.toggle_storage_overlay()
        elif name == 'storage_grid':
            self.toggle_storage_grid()
        elif name == 'pause':
            self.pause_buffs(not self.buffs.paused)
        elif name.startswith('buff') and name[4:].isdigit():
            self.start_buff_timer(int(name[4:]) - 1)

    def start_buff_timer(self, index):
        """목록 N번째 버프 타이머 시작/갱신 (게임 화면을 보면서 단축키로)."""
        defs = self.buffs.defs
        if not 0 <= index < len(defs):
            return
        d = defs[index]
        self.buffs.start(d.key)
        self.refresh_buffs()
        msg = '⏱ %s 시작 (%s)' % (d.name, mmss(d.duration))
        self.status(msg)
        if self.hud and self.hud.isVisible():          # 게임 화면에서 보이는 유일한 확인 수단
            self.hud.alert.setText(msg)
            self.hud.alert.setVisible(True)

    def changeEvent(self, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.WindowStateChange and self.isMinimized() and self.s.get('minimize_to_tray') \
                and getattr(self, 'tray', None):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(ev)

    def closeEvent(self, ev):
        if getattr(self, 'hotkeys', None):
            self.hotkeys.stop()
        self.macro_page.shutdown()
        self.stop_vend_assist()
        self.stop_live()
        if self.loot_alert is not None:
            self.loot_alert.stop()
        if getattr(self, 'bulk_prices', None) is not None:
            self.bulk_prices.stop()
        if getattr(self, 'catalog_icons', None) is not None:
            self.catalog_icons.stop()
        if getattr(self, '_loot_icons', None) is not None:
            self._loot_icons.stop()
        if self.settlement_dialog is not None:
            self.settlement_dialog.task.close()
        if getattr(self, 'tray', None):
            self.tray.hide()
        if getattr(self, 'quest_watch', None):
            self.quest_watch.stop()
        if self.nav_overlay is not None:
            self.nav_overlay.close()
        if self.buff_overlay is not None:
            self.buff_overlay.close()
        if self.storage_overlay is not None:
            self.storage_overlay.close()
        if self.storage_grid_overlay is not None:
            self.storage_grid_overlay.close()
        self.cancel.set()
        self.pool.waitForDone(3000)
        super().closeEvent(ev)
