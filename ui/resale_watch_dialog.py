"""고가 매물 구매 추천 설정 및 결과. 구매/게임 조작 기능은 없다."""
import datetime
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox,
    QCheckBox, QPushButton, QLineEdit, QListWidget, QMessageBox)
from services import popular_option as PO
from services import resale_watch as R
from services.gnjoy_market import SERVERS, RateLimited
from ui.completer import attach_completer
from ui.economy_tasks import BackgroundTask
from ui.widgets import Table, Col, comma


def when(value):
    return '-' if value is None else datetime.datetime.fromtimestamp(value).strftime('%m-%d %H:%M')


class ResaleWatchDialog(QDialog):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle('고가 매물 구매 추천 · 기준가 1억 Z 이상')
        self.resize(1320, 760)
        cfg = controller.current_config()
        self.targets = [dict(t) for t in cfg['targets']]
        box = QVBoxLayout(self)
        info = QLabel('기준 시세 1억 Z 이상인 아이템 중 기준가의 80% 이하 매물을 찾습니다.\n'
                      '기준가: 동일 검색 조건의 최근 7일 날짜별 평균의 평균(최소 3일). 카드·옵션 가치는 반영하지 않습니다.\n'
                      '재판매 예상가 = min(기준가, 다른 상점 최저가)에서 여유분 차감. 기존 판매세 적용 후 1개당 예상 차익입니다. 수익은 보장되지 않습니다.')
        info.setWordWrap(True); box.addWidget(info)
        controls = QHBoxLayout()
        self.enabled = QCheckBox('자동 감시·팝업 켜기'); self.enabled.setChecked(cfg['enabled'])
        controls.addWidget(self.enabled)
        self.server = QComboBox()
        for sid, name in SERVERS.items(): self.server.addItem(name, sid)
        self.server.setCurrentIndex(max(0, self.server.findData(cfg['server'])))
        controls.addWidget(self.server)
        self.ratio = QSpinBox(minimum=1, maximum=80, value=cfg['max_ratio'], suffix='% 이하')
        controls.addWidget(QLabel('기준가 대비')); controls.addWidget(self.ratio)
        self.profit = QSpinBox(minimum=1, maximum=200000, value=max(1, cfg['min_profit']//10000), suffix='만 Z')
        controls.addWidget(QLabel('최소 세후 차익/개')); controls.addWidget(self.profit)
        self.haircut = QSpinBox(minimum=0, maximum=30, value=cfg['haircut'], suffix='%')
        controls.addWidget(QLabel('재판매 여유분')); controls.addWidget(self.haircut)
        box.addLayout(controls)
        self.shadows = QCheckBox('+10 쉐도우 기본 계열 자동 탐색(기존 TOP20과 같은 대상, 옵션 무관)')
        self.shadows.setChecked(cfg['shadows']); box.addWidget(self.shadows)
        # 추가 대상: 장비를 자동완성에서 고르면 국민 옵션(공식 노점에 가장 많이 올라온 제련·등급·슬롯·카드)을 기본값으로 채운다
        line = QHBoxLayout()
        item_db = controller.app.item_db
        self._entries = {'%s · %d' % (item['name'], iid): (iid, item['name'])
                         for iid, item in (getattr(item_db, 'items', {}) or {}).items()
                         if isinstance(item, dict) and item.get('name') and PO.is_equipment(item_db, iid)}
        self.query = QLineEdit(placeholderText='추가 감시할 장비 이름 일부 — 목록에서 고르면 국민 옵션을 불러옵니다')
        attach_completer(self.query, sorted(self._entries), on_pick=self.pick_item)
        self.query.returnPressed.connect(lambda: self.pick_item(self.query.text()))
        self.query.textEdited.connect(lambda _: setattr(self, 'picked', None))   # 고른 뒤 이름을 고치면 다시 골라야 함
        line.addWidget(self.query, 1)
        self.refine = QSpinBox(minimum=0, maximum=20, prefix='+')
        self.grade = QComboBox(); self.grade.setEditable(True); self.grade.addItem('')
        self.grade.lineEdit().setPlaceholderText('등급 없음'); self.grade.setMinimumWidth(140)
        self.slots = QSpinBox(minimum=-1, maximum=4, prefix='슬롯 '); self.slots.setSpecialValueText('슬롯 표시 없음')
        self.slots.setValue(-1)
        for w in (QLabel('제련'), self.refine, QLabel('등급'), self.grade, self.slots):
            line.addWidget(w)
        add = QPushButton('추가 대상 등록'); add.clicked.connect(self.add_target); line.addWidget(add)
        remove = QPushButton('선택 삭제'); remove.clicked.connect(self.remove_target); line.addWidget(remove)
        box.addLayout(line)
        pline = QHBoxLayout()
        self.same_cards = QCheckBox('카드·인챈트도 같은 것만'); self.same_cards.setEnabled(False)
        self.reload = QPushButton('국민 옵션 다시 불러오기'); self.reload.setEnabled(False)
        self.reload.clicked.connect(lambda: self.load_popular(force=True))
        self.popular_note = QLabel('장비를 고르면 공식 노점 목록에서 가장 많이 올라온 구성을 기본값으로 채웁니다(요청 최대 8번, 30분 재사용). 필요하면 바꿔서 등록하세요.')
        self.popular_note.setWordWrap(True)
        pline.addWidget(self.same_cards); pline.addWidget(self.reload); pline.addWidget(self.popular_note, 1)
        box.addLayout(pline)
        self.picked, self.popular = None, None
        self.popular_finder = PO.PopularOption(controller.economy.store, controller.economy.client)
        self.task = BackgroundTask(self, self._popular_done, self._popular_error)
        self.finished.connect(lambda _: self.task.close())       # 창을 닫으면 국민 옵션 조회도 멈춘다
        self.target_list = QListWidget(); self.target_list.setMaximumHeight(90); box.addWidget(self.target_list)
        self.refresh_targets()
        actions = QHBoxLayout()
        apply = QPushButton('설정 저장'); apply.clicked.connect(self.save_settings); actions.addWidget(apply)
        scan = QPushButton('시세 확인'); scan.clicked.connect(controller.scan_now); actions.addWidget(scan)
        cached = QPushButton('저장 시세 다시 보기'); cached.clicked.connect(controller.refresh_cached); actions.addWidget(cached)
        actions.addStretch(); box.addLayout(actions)
        self.state = QLabel('설정 변경 후 저장하세요. 시세 확인은 저장된 설정을 사용합니다.'); self.state.setWordWrap(True); box.addWidget(self.state)
        self.table = Table([
            Col('판단', lambda r:r['state'], width=115, tooltip=lambda r:r['reason']),
            Col('아이템', lambda r:r['name'], width=240, tooltip=lambda r:r['name']),
            Col('구매가 Z', lambda r:r['price'], comma, width=105),
            Col('7일 기준가 Z', lambda r:r['base'], lambda v:comma(int(v)), numeric=True, width=110),
            Col('기준가 대비', lambda r:r['ratio'], lambda v:'%.1f%%'%v, numeric=True, width=85),
            Col('재판매 예상 Z', lambda r:r['resale'], comma, width=115),
            Col('세후 차익/개 Z', lambda r:r['profit'], comma, width=120),
            Col('수량', lambda r:r['qty'], comma, width=50),
            Col('상점', lambda r:r['shop'], width=160),
            Col('확인 시각', lambda r:r['fetched_at'], when, numeric=True, width=100),
            Col('이유', lambda r:r['reason'], width=280, tooltip=lambda r:r['reason'])])
        self.table.verticalHeader().setDefaultSectionSize(30); box.addWidget(self.table, 1)
        self.detail = QLabel('행을 선택하면 비교 근거를 표시합니다.'); self.detail.setWordWrap(True); self.detail.setTextFormat(Qt.PlainText)
        box.addWidget(self.detail)
        self.table.clicked.connect(self.show_detail)
        foot = QLabel('앱 실행 중 순차 감시 · 대상별 최소 10분 / 검색 캐시 30분 · 공식 요청 간격 2.5초 · 429 시 전체 5분 대기\n'
                      '시장 전체 실시간 검색이 아닙니다. 다른 상점 2곳 미만·미조회 페이지·오래된 시세는 팝업에서 제외합니다. 추가 대상 최대 20개.')
        foot.setWordWrap(True); box.addWidget(foot)
        close = QPushButton('닫기'); close.clicked.connect(self.close); box.addWidget(close)

    def refresh_targets(self):
        self.target_list.clear()
        self.target_list.addItems([t.get('query', '') + (' · 카드/인챈트: ' + ', '.join(t['cards']) if t.get('cards') else '')
                                   for t in self.targets])

    # ------------------------------------------------------------ 국민 옵션
    def pick_item(self, text):
        text = (text or '').strip()
        hit = self._entries.get(text)
        if hit is None:
            same = [v for k, v in self._entries.items() if v[1] == text]
            hit = same[0] if len(same) == 1 else None
        if hit is None:
            self.popular_note.setText('자동완성 목록에서 장비를 골라 주세요(같은 이름이 여러 개면 번호로 구분됩니다).'); return
        self.picked, self.popular = hit, None
        self.load_popular()

    def load_popular(self, force=False):
        if self.picked is None: return
        iid, name = self.picked
        server = self.server.currentData()
        if force:
            self.controller.economy.store.set_meta(self.popular_finder._key(server, iid), '')
        if self.controller.economy.client.retry_after:
            self.popular_note.setText('공식 요청 중지 중(429) · %d초 뒤 다시' % self.controller.economy.client.retry_after); return
        if self.task.start(lambda stop: (iid, self.popular_finder.find(server, iid, name, stop))):
            self.reload.setEnabled(False)
            self.popular_note.setText('%s 국민 옵션 찾는 중… (목록 최대 3쪽 + 상세 최대 5건, 2.5초 간격)' % name)

    def _popular_done(self, value):
        iid, opt = value
        self.reload.setEnabled(self.picked is not None)
        if self.picked is None or self.picked[0] != iid: return          # 그사이 다른 장비를 고름
        self.popular = opt
        if opt.get('name'):
            self.refine.setValue(opt['refine'])
            if opt['grade'] and self.grade.findText(opt['grade']) < 0: self.grade.addItem(opt['grade'])
            self.grade.setCurrentText(opt['grade'])
            self.slots.setValue(-1 if opt['slots'] is None else opt['slots'])
        self.same_cards.setEnabled(bool(opt.get('cards')))
        self.same_cards.setChecked(bool(opt.get('cards')))
        self.same_cards.setText('카드·인챈트도 같은 것만: ' + ', '.join(opt['cards']) if opt.get('cards') else '카드·인챈트도 같은 것만')
        self.popular_note.setText('국민 옵션 — ' + PO.describe(opt) + ' · 바꾸려면 제련·등급·슬롯을 고친 뒤 등록')

    def _popular_error(self, exc):
        self.reload.setEnabled(self.picked is not None)
        self.popular_note.setText(str(exc) if isinstance(exc, RateLimited) else '국민 옵션을 받지 못했습니다: %s' % exc)

    def add_target(self):
        try:
            if len(self.targets) >= R.MAX_TARGETS: raise ValueError('추가 대상은 최대 20개입니다.')
            if self.picked is None:
                # 목록에서 고르지 않고 정확한 전체 이름을 친 경우(예: +10인피니티 웨폰 쉐도우)는 예전처럼 이름으로 등록
                t = R.target_from_query(self.controller.app.item_db, self.query.text())
            else:
                opt = self.popular or {}
                slots = None if self.slots.value() < 0 else self.slots.value()
                cards = opt.get('cards') if self.same_cards.isChecked() else None
                t = R.target_from_item(self.controller.app.item_db, self.picked[0], self.refine.value(),
                                       self.grade.currentText().strip().upper(), slots, cards, base=opt.get('base'))
            if any(x['item_id']==t['item_id'] and R.variant(x['query'])==R.variant(t['query']) for x in self.targets):
                raise ValueError('이미 등록한 대상입니다.')
        except ValueError as exc:
            QMessageBox.information(self, '대상 확인', str(exc)); return
        self.targets.append(t); self.query.clear(); self.picked = self.popular = None; self.refresh_targets()

    def remove_target(self):
        i = self.target_list.currentRow()
        if i >= 0: self.targets.pop(i); self.refresh_targets()

    def save_settings(self):
        cfg = R.config(dict(enabled=self.enabled.isChecked(), server=self.server.currentData(),
                            shadows=self.shadows.isChecked(), targets=self.targets,
                            max_ratio=self.ratio.value(), min_profit=self.profit.value()*10000,
                            haircut=self.haircut.value()))
        self.controller.save_config(cfg)

    def set_result(self, result):
        self.table.set_rows(result['rows'])
        self.detail.setText('행을 선택하면 비교 근거를 표시합니다.')

    def show_detail(self, index):
        row = self.table.row_at(index)
        self.detail.setText('%s\n거래 기록 %d일 · 다른 상점 %d곳 · 타 상점 최저 %s Z · 세후 수령 예상 %s Z\n%s\n%s' % (
            row['name'], row['days'], row['peer_shops'], comma(row['peer_min']), comma(row['net']),
            row['reason'], row['caution']))


class ResalePopup(QDialog):
    def __init__(self, parent, rows, open_details):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('최저가 구매 추천 후보' if rows[0]['lowest'] else '고가 매물 구매 추천 후보')
        self.resize(540, 280)
        self.setStyleSheet('QDialog { background:#202630; color:#f2f5fa; } QLabel { color:#f2f5fa; font-size:14px; } QPushButton { padding:8px; }')
        box = QVBoxLayout(self)
        title = QLabel(self.windowTitle()); title.setStyleSheet('font-size:20px; font-weight:600; color:#7de4b4;'); box.addWidget(title)
        for row in rows:
            label = QLabel(R.alert_text(row)); label.setTextFormat(Qt.PlainText); label.setWordWrap(True); box.addWidget(label)
        actions = QHBoxLayout()
        details = QPushButton('후보 목록 보기'); details.clicked.connect(lambda:(self.close(), open_details())); actions.addWidget(details)
        close = QPushButton('닫기'); close.clicked.connect(self.close); actions.addWidget(close); box.addLayout(actions)
