"""노점 개설 추천가 패널(디자인 시안 4A): 게임의 '노점 개설' 창 옆에 붙어 아이템마다
지금 최저가 · 최근 거래 평균 · 추천가 · 판단을 보여 준다. 아이템을 올리기만 하면(판매가 0) 추천가가 나온다.
추천가를 누르면 클립보드로 복사(게임에 입력하지 않음).
반투명이고, 게임의 '천만 Zeny 이상' 확인 창이 뜨면 가리지 않게 숨는다.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import logging
import os
import queue
import re
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
                               QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from config.settings import BASE_DIR
from core import vend_ocr as V
from services import gnjoy_market as G
from ui import overlay_styles
from ui.theme import T

log = logging.getLogger(__name__)

FRESH_SEC = 30 * 60
TONES = {'danger': ('#4A1D1D', '#FFB4AE'), 'warn': ('#402A14', '#FFC98A'), 'todo': ('#16304F', '#8FC2FF'),
         'ok': ('#173A2B', '#7FE0B0'), 'muted': ('#262B35', '#AAB2C0')}
COLS = ['아이템', '내 가격', '지금 최저가', '최근 거래 평균', '추천가', '장착 카드', '판단']
INLINE_W, INLINE_H = 232, 22      # '아이템 옆 한 줄' 모양의 한 줄 크기(논리 px)
PANEL_CSS = ('QWidget#vendRoot { background: #14171D; border: 1px solid #35598C; border-radius: 12px; }'
             'QLabel { color: %s; background: transparent; }' % T['text'])
INLINE_CSS = 'QWidget#vendRoot { background: transparent; border: 0; }'
OPACITY = 0.86


def _c(v):
    return '-' if v is None else format(int(v), ',')


class _Bridge(QObject):
    state = Signal(object)      # 감시 스레드 → 화면
    priced = Signal(str)        # 시세 받기 완료(이름)


class VendPanel(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.app = app
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('노점 추천가')
        self.store = G.VendingStore(os.path.join(BASE_DIR, 'data', 'vending.db'))
        self.client = G.shared_client()
        from services.market_insights import MarketInsights
        self.insights = MarketInsights(self.store, self.client)
        self._equip = {}                      # 이름 → 장비 조회에 쓸 정보(제련·장착 카드)
        self.bridge = _Bridge()
        self.bridge.state.connect(self.set_state)
        self.bridge.priced.connect(lambda _n: self._render())
        self.setWindowOpacity(OPACITY)
        self.setAttribute(Qt.WA_TranslucentBackground)          # '아이템 옆 한 줄'일 때 게임 화면을 가리지 않는다
        self.state = None
        self._matcher = None
        self._ids = {}
        self._pending = set()
        self._failed = {}                           # 이름 → 실패 글(요청 제한·네트워크) — '받는 중'으로 멈춰 보이지 않게
        self._q = queue.Queue()
        self._worker = threading.Thread(target=self._price_loop, daemon=True, name='VendPrice')
        self._worker.start()
        self._rows = []
        self.setStyleSheet(PANEL_CSS)
        root = QWidget(self)
        root.setObjectName('vendRoot')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)
        self.head_w = QWidget()
        head = QHBoxLayout(self.head_w)
        head.setContentsMargins(0, 0, 0, 0)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet('background: #4CC38A; border-radius: 5px;')
        head.addWidget(dot)
        titles = QVBoxLayout()
        t = QLabel('추천 판매가')
        t.setStyleSheet('font-size: 16px; font-weight: 700;')
        self.sub = QLabel()
        self.sub.setStyleSheet('font-size: 11px; color: #8E97A8;')
        titles.addWidget(t)
        titles.addWidget(self.sub)
        head.addLayout(titles, 1)
        self.chips = {}
        for kind, label in (('danger', '너무 쌈'), ('warn', '비쌈'), ('todo', '입력 전'), ('ok', '적정')):
            c = QLabel()
            bg, fg = TONES[kind]
            c.setStyleSheet('background: %s; color: %s; border-radius: 9px; padding: 3px 9px; font-size: 12px;'
                            ' font-weight: 700;' % (bg, fg))
            self.chips[kind] = (c, label)
            head.addWidget(c)
        self.refresh_cb = None                      # 노점 창을 바로 다시 읽기(VendWatcher.force)
        b = QPushButton('다시 읽기')
        b.setToolTip('노점 창을 지금 다시 읽습니다 (보통은 아이템을 올리거나 가격을 바꾸면 1.5초 안에 저절로 다시 읽음)')
        b.setStyleSheet('QPushButton { background: #1F242D; color: %s; border: 1px solid #343B48; border-radius: 8px;'
                        ' padding: 4px 10px; font-size: 12px; }' % T['text'])
        b.clicked.connect(lambda: self.refresh_cb and self.refresh_cb())
        head.addWidget(b)
        v.addWidget(self.head_w)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate((0, 120, 120, 126, 106, 150, 160)):      # 장착 카드 칸 추가
            if w:
                self.table.setColumnWidth(i, w)
        self.table.setStyleSheet('QTableWidget { background: #14171D; border: 0; color: %s; font-size: 13px; }'
                                 'QHeaderView::section { background: #1E232C; color: #AAB2C0; border: 0;'
                                 ' padding: 6px; font-size: 12px; font-weight: 600; }' % T['text'])
        self.table.cellClicked.connect(self._copy)
        v.addWidget(self.table)
        # 좁은 카드 모양: 문제 있는 줄만 펼치고(내 가격 → 추천가, 복사) 나머지는 한 줄
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        self.cards_area.setFrameShape(QFrame.NoFrame)
        self.cards_area.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }')
        self.cards = QWidget()
        self.cards_box = QVBoxLayout(self.cards)
        self.cards_box.setContentsMargins(0, 0, 0, 0)
        self.cards_box.setSpacing(6)
        self.cards_area.setWidget(self.cards)
        self.cards_area.hide()
        v.addWidget(self.cards_area)
        self.inline = QWidget()                 # '아이템 옆 한 줄': 게임 줄 y에 맞춰 직접 배치한다
        self.inline.hide()
        self.inline_rows = []
        self._inline_data = {}
        v.addWidget(self.inline, 1)
        self._v = v
        self.note = QLabel('추천가를 누르면 복사됩니다 · 제련·카드·옵션이 붙은 장비는 참고만')
        self.note.setStyleSheet('font-size: 11px; color: #8E97A8;')
        v.addWidget(self.note)
        self.resize(820, 300)
        self._vis = QTimer(self, interval=400, timeout=self._follow)
        self._vis.start()

    # ------------------------------------------------------------ 이름 맞추기
    def _names(self):
        if self._matcher is None:
            db = self.app.item_db
            if not db.items:
                db.load()
            for iid, it in db.items.items():
                name = it.get('name') if isinstance(it, dict) else None
                if name:
                    self._ids.setdefault(name, []).append(int(iid))
            cart = {e.name for e in getattr(self.app, 'entries', []) if getattr(e, 'source', '') == '카트'}
            self._matcher = V.NameMatcher(self._ids, preferred=cart)
        return self._matcher

    @property
    def sid(self):
        return self.store.meta('server', '129')

    # ------------------------------------------------------------ 상태
    def set_state(self, state):
        """VendWatcher 결과(GUI 스레드). None 이면 숨긴다."""
        self.state = state
        if not state or state.get('dialog'):
            self.hide()                             # 게임 확인 창('천만 Zeny 이상…')을 가리지 않는다
            return
        m = self._names()
        for r in state['rows']:
            if not r.match and (r.name_text or r.alts):
                r.match, r.score, r.candidates, r.prefix = m.best_prefixed([r.name_text] + r.alts)
                if r.refine is None:
                    got = next((V.REFINE.match(t) for t in r.alts if V.REFINE.match(t)), None)
                    r.refine = int(got.group(1)) if got else None
            if r.equipment:
                self._equip[r.match] = dict(item_id=self._key(r.match), name=r.match, refine=r.refine or 0,
                                            card_names=[c for c, _ in self._cards_of(r)], options=[])
            for name in self._fetch_targets(r):
                if not self._fresh(name) and name not in self._pending:
                    self._pending.add(name)
                    self._q.put(name)
        self._render()
        self._place()
        self.show()

    # ------------------------------------------------------------ 장비·장착 카드
    def _replay_entry(self, name, refine):
        """리플레이에서 읽은 내 아이템 중 같은 이름·같은 제련인 것(장착 카드를 알기 위해).
        노점 창 글자만으로는 어떤 카드가 박혔는지 알 수 없다 — 리플레이에 있을 때만 쓴다."""
        want = refine or 0
        for e in getattr(self.app, 'entries', []) or []:
            if e.name == name and (e.refine or 0) == want and e.cards:
                return e
        return None

    def _cards_of(self, r):
        """[(카드 이름, 카드 item_id)] — 리플레이에서 확인된 장착 카드만."""
        e = self._replay_entry(r.match, r.refine)
        if not e:
            return []
        db = self.app.item_db
        out = []
        for cid in e.cards or []:
            if not cid:
                continue
            item = db.get(cid) if hasattr(db, 'get') else None
            name = (item or {}).get('name') if isinstance(item, dict) else None
            name = name or (db.name(cid) if hasattr(db, 'name') else None)
            if name and not str(name).startswith('아이템 #'):
                out.append((name, int(cid)))
        return out

    def _fetch_targets(self, r):
        """이 줄 때문에 공식 시세를 받아야 할 이름들: 본체 + 장착 카드."""
        if not r.match:
            return []
        out = [r.match] if (r.comparable or r.equipment) else []
        out += [name for name, _ in self._cards_of(r)]
        return out

    def _card_text(self, r):
        """'장착 카드' 칸: 카드 이름과 최저가. 카드는 따로 팔아야 하므로 합계에 넣지 않고 참고로 둔다."""
        cards = self._cards_of(r)
        if not cards:
            return ('-', '') if not r.prefix else ('카드 있음(확인 필요)',
                                                   '이름 앞 "%s" 는 카드 접두사로 보입니다. 리플레이를 불러오면 '
                                                   '어떤 카드인지 확인합니다.' % r.prefix)
        parts, tip, total = [], [], 0
        for name, cid in cards:
            _t, ls = self.store.latest_listings(self.sid, cid)
            low = min((l['price'] for l in ls if l.get('kind') == 'sale' and l.get('price')), default=None)
            parts.append('%s %s' % (name, _c(low) if low else '시세 받는 중'))
            tip.append('%s · %s' % (name, _c(low) if low else '아직 받지 않음'))
            total += low or 0
        head = _c(total) if total else '시세 받는 중'
        return ('%s (%d장)' % (head, len(cards)) if len(cards) > 1 else parts[0],
                '장착 카드 — 따로 팔아야 하므로 장비 가격에 더하지 않습니다\n' + '\n'.join(tip))

    def _key(self, name):
        ids = self._ids.get(name)
        return min(ids) if ids else None

    def _unknown_at(self, key):
        """공식 검색이 비슷한 이름의 더 싼 아이템으로 가득 차 이 아이템 줄을 못 본 시각(services/market_insights 와 같은 키)."""
        v = self.store.meta('listing_unknown:%s:%s' % (self.sid, key))
        return float(v) if v else None

    def _fresh(self, name):
        key = self._key(name)
        t, _ = self.store.latest_listings(self.sid, key)
        t = max(t or 0, self._unknown_at(key) or 0)
        return bool(t and time.time() - t < FRESH_SEC)

    def _fetch_price(self, name):
        item = self._equip.get(name)
        if item and item.get('item_id'):
            # 장비: 목록 5쪽 + 같은 제련 매물의 상세(카드·옵션)까지 — 창고 정리 추천과 같은 경로
            self.insights.fetch_variant(self.sid, item)
            self._failed.pop(name, None)
            return
        key = self._key(name)
        # 공식 검색은 이름 '포함'·가격순 — 이 아이템 줄이 하나라도 보이면 그 줄이 최저가, 안 보이는데 뒤 쪽이
        # 남았으면 '노점 없음'이 아니라 '확인 불가'로 남긴다(2쪽까지 받음)
        raw, more = self.client.listings(self.sid, name, max_pages=2, with_more=True)
        ls = [l for l in raw if l['name'] == name]
        hist, _ = self.client.history(self.sid, name, 1)
        if not ls and more:
            self.store.set_meta('listing_unknown:%s:%s' % (self.sid, key), time.time())
        else:
            self.store.save_listings(self.sid, key, ls)
        self.store.save_history(self.sid, key, hist)
        self._failed.pop(name, None)

    def _price_loop(self):
        while True:
            name = self._q.get()
            try:
                self._fetch_price(name)
            except G.RateLimited as e:
                self._failed[name] = '요청 제한 중'
                log.info('노점 시세: %s', e)
                time.sleep(300)
            except Exception as e:
                self._failed[name] = '시세 받기 실패'
                log.warning('노점 시세 받기 실패(%s): %s', name, e)
            finally:
                self._pending.discard(name)
                self.bridge.priced.emit(name)

    # ------------------------------------------------------------ 그리기
    def _row_data(self, r):
        mine = r.price
        if not r.match:
            return dict(row=r, name=r.name_text or '?', mine=mine, low='-', lowNote='', avg='-', avgNote='', rec=None,
                        card='-', cardTip='', kind='muted', verdict='이름을 읽지 못함')
        card, card_tip = self._card_text(r)
        if r.equipment:
            return dict(self._equipment_data(r, mine), card=card, cardTip=card_tip)
        key = self._key(r.match)
        t, ls = self.store.latest_listings(self.sid, key)
        unknown = self._unknown_at(key)
        unknown = bool(unknown and unknown > (t or 0))
        if unknown:
            t, ls = unknown, []
        shop = (self.state or {}).get('shop') or ''
        # 지금 여는 내 노점은 시세에서 뺀다 — 상점명까지 맞을 때만. 상점명을 못 읽었으면 가격만 같다고 빼지 않는다
        maybe_own = not shop and any(V.is_own_listing(l, mine, '') for l in ls)
        if shop:
            ls = [l for l in ls if not V.is_own_listing(l, mine, shop)]
        if t is None:
            failed = self._failed.get(r.match)
            return dict(row=r, name=r.match, mine=mine, low=failed or '받는 중…', lowNote='', avg='-', avgNote='',
                        card=card, cardTip=card_tip, rec=None, kind='muted',
                        verdict=(failed + ' · 잠시 뒤 다시 시도') if failed else '시세 받는 중')
        hist = self.store.history(self.sid, key)
        cmp = G.compare(hist, ls)
        rec = G.recommend_price(cmp)
        kind, verdict = G.judge_price(mine, rec, cmp.get('now_min'))
        avg, note = (cmp['avg7'], '최근 7일') if cmp.get('avg7') else \
            ((hist[0]['avg'], hist[0]['day'][5:] + ' 거래') if hist else (None, ''))
        if rec is None:
            kind, verdict = 'muted', '거래 기록 없음'
        elif mine == 0:
            kind, verdict = 'todo', '가격 입력 전'
        elif mine is None:
            kind, verdict = 'muted', '판매가 못 읽음'
        if unknown:
            low, low_note = '확인 불가', '비슷한 이름에 밀림'
            if rec is not None and verdict:
                verdict += ' (평균 기준)'                  # 지금 최저가 없이 최근 거래 평균만으로 판단함
        else:
            low = _c(cmp.get('now_min'))
            low_note = ('판매 %d곳' % cmp['sellers']) if cmp.get('sellers') else '노점 없음'
            if maybe_own:
                low_note += ' · 내 노점일 수 있음'
        return dict(row=r, name=r.match, mine=mine, low=low, lowNote=low_note, card=card, cardTip=card_tip,
                    avg=_c(avg), avgNote=note, rec=rec, kind=kind, verdict=verdict)

    def _equipment_data(self, r, mine):
        """제련·카드가 붙은 장비: 공식 목록에서 **같은 제련** 매물만 견준다(카드·옵션은 상세가 있을 때만 확인).

        창고 정리 추천과 같은 판단(core.storage_advice.advise_variant)을 쓴다."""
        from core.storage_advice import advise_variant
        name = ('+%d ' % r.refine if r.refine else '') + r.match + (' [%s…]' % r.prefix if r.prefix else '')
        key = self._key(r.match)
        data = self.insights.cached(self.sid, key)
        cards = self._cards_of(r)
        item = dict(item_id=key, name=r.match, quantity=1, refine=r.refine or 0,
                    card_names=[c for c, _ in cards], options=[])
        shop = (self.state or {}).get('shop') or ''
        own = [dict(price=mine, shop=shop)] if mine else []
        adv = advise_variant(item, data['listings'], self.insights.details(self.sid, data['listings']),
                             fetched_at=data['fetched_at'], listing_unknown=data['listing_unknown'],
                             listing_more=data['listing_more'], own=own)
        low, rec = adv['now_min'], adv['recommended']
        if data['fetched_at'] is None:
            kind, verdict = 'muted', ('시세 받는 중' if r.match in self._pending
                                      else self._failed.get(r.match, '시세 없음 — 잠시 뒤 표시'))
        elif rec is not None:
            kind, verdict = G.judge_price(mine, rec, low)
            verdict += ' · 같은 구성'
        else:
            kind, verdict = 'muted', '참고가(같은 구성 매물 없음)'
        if not adv['same_count']:
            note = '같은 제련 매물 없음'
        elif adv['identical_count']:
            note = '같은 구성 %d건' % adv['identical_count']
        else:                       # 제련만 같고 카드·옵션은 다르거나 아직 확인 못 함 → 숫자를 '최저가'로 단정하지 않는다
            note = '같은 제련 %d건 · 구성 다름(참고)' % adv['same_count']
        if r.prefix and not cards:
            note += ' · 카드 접두사 추정'
        return dict(row=r, name=name, mine=mine, low=_c(low) if low else '-', lowNote=note,
                    avg='-', avgNote='제련별 평균은 공식 자료에 없음', rec=rec, kind=kind, verdict=verdict,
                    tip=adv['reason'])

    # ------------------------------------------------------------ 모양(표 / 좁은 카드)
    def _settings(self):
        return getattr(self.app, 's', None) or {}

    def apply_style(self):
        self._render()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        overlay_styles.add_menu(m, self._settings(), 'vend', self.apply_style)
        m.exec(e.globalPos())

    def _copy_row(self, d):
        QGuiApplication.clipboard().setText(str(d['rec']))
        self.note.setText('%s 추천가 %s 을(를) 복사했습니다 — 게임 노점 창에 붙여 넣으세요' % (d['name'], _c(d['rec'])))

    def _render_cards(self, data):
        import html as _h
        while self.cards_box.count():
            w = self.cards_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        height = 0
        for d in data:
            bg, fg = TONES[d['kind']]
            card = QFrame()
            h = QHBoxLayout(card)
            h.setContentsMargins(10, 6, 8, 6)
            mine = '입력 전' if d['mine'] == 0 else _c(d['mine'])
            if d['kind'] in ('danger', 'warn', 'todo'):
                card.setStyleSheet('QFrame { background: %s; border-radius: 10px; }' % bg)
                text = ('<b>%s</b> &nbsp;<span style="color:%s; font-weight:700">%s</span><br>'
                        '<span style="color:#CFD6E0">%s</span> → <b style="font-size:17px; color:#FFD479">%s</b><br>'
                        '<span style="color:#AAB2C0; font-size:11px">최저 %s · %s %s</span>'
                        % (_h.escape(d['name']), fg, _h.escape(d['verdict']), mine, _c(d['rec']),
                           _h.escape(d['low']), _h.escape(d['avgNote'] or '평균'), _h.escape(d['avg'])))
                height += 78
            else:
                card.setStyleSheet('QFrame { background: #1B2029; border-radius: 10px; }')
                text = ('%s &nbsp;<span style="color:#CFD6E0">%s</span> &nbsp;<span style="color:%s; font-weight:700">%s</span>'
                        % (_h.escape(d['name']), mine, fg, _h.escape(d['verdict'])))
                height += 40
            label = QLabel(text)
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(True)
            h.addWidget(label, 1)
            if d['rec'] and d['kind'] in ('danger', 'warn', 'todo'):
                b = QPushButton('복사')
                b.setToolTip('추천가 %s 복사' % _c(d['rec']))
                b.setStyleSheet('QPushButton { background: #2B5C9E; color: #FFFFFF; border: 0; border-radius: 7px;'
                                ' padding: 5px 10px; font-weight: 700; }')
                b.clicked.connect(lambda _=False, d=d: self._copy_row(d))
                h.addWidget(b)
            self.cards_box.addWidget(card)
        self.cards_box.addStretch(1)
        return height

    def _render(self):
        if not self.state:
            return
        rows = [self._row_data(r) for r in self.state['rows']]           # 게임 창에 보이는 순서 그대로
        order = {'danger': 0, 'warn': 1, 'todo': 2, 'ok': 3, 'muted': 4}
        data = sorted(rows, key=lambda d: order[d['kind']])
        self._rows = data
        style = overlay_styles.get(self._settings(), 'vend')
        cards, inline = style == 'cards', style == 'inline'
        self.head_w.setVisible(not inline)
        self.note.setVisible(not inline)
        self.inline.setVisible(inline)
        self._v.setContentsMargins(*((0, 0, 0, 0) if inline else (14, 10, 14, 10)))
        self.setStyleSheet(INLINE_CSS if inline else PANEL_CSS)
        if inline:
            self.table.hide()
            self.cards_area.hide()
            self._render_inline(rows)
            return
        self.table.setVisible(not cards)
        self.cards_area.setVisible(cards)
        for chip, _label in self.chips.values():
            chip.setVisible(not cards)
        self.table.setRowCount(len(data))
        for i, d in enumerate(data):
            real = V.real_amount(d['mine'])
            mine = '입력 전' if d['mine'] == 0 else _c(d['mine']) + ('\n받음 %s' % _c(real) if real != d['mine'] else '')
            cells = [d['name'], mine, d['low'] + ('\n' + d['lowNote'] if d['lowNote'] else ''),
                     d['avg'] + ('\n' + d['avgNote'] if d['avgNote'] else ''), _c(d['rec']),
                     d.get('card', '-'), d['verdict']]
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setTextAlignment((Qt.AlignLeft if j == 0 else Qt.AlignRight) | Qt.AlignVCenter)
                if d['kind'] == 'danger':
                    it.setBackground(QColor('#2A1719'))
                if j == 4 and d['rec']:
                    it.setForeground(QColor('#8FC2FF'))
                    it.setToolTip('눌러서 %s 복사' % _c(d['rec']))
                if j == 5 and d.get('cardTip'):
                    it.setToolTip(d['cardTip'])
                if j == 6:
                    it.setForeground(QColor(TONES[d['kind']][1]))
                self.table.setItem(i, j, it)
            self.table.setRowHeight(i, 46)
        counts = {k: sum(1 for d in data if d['kind'] == k) for k in self.chips}
        for k, (chip, label) in self.chips.items():
            chip.setText('%s %d' % (label, counts[k]))
            chip.setVisible(counts[k] > 0 and not cards)
        if cards:
            height = self._render_cards(data)
            self.sub.setText('%s · %s' % (G.SERVERS.get(self.sid, self.sid), datetime.datetime.now().strftime('%H:%M')))
            self.resize(420, min(720, 130 + height))
            return
        self.sub.setText('노점 개설 창 감지 · %s · 공식 홈페이지 노점 거래 정보 · %s' % (
            G.SERVERS.get(self.sid, self.sid), datetime.datetime.now().strftime('%H:%M')))
        self.resize(980, min(720, 150 + 46 * len(data)))

    def _render_inline(self, rows):
        """게임 노점 창의 줄 높이에 맞춰 추천가를 한 줄씩 붙인다(창 오른쪽).

        VendRow.y 는 노점 창 영역 기준 '판매가 줄'의 y(물리 px)다. 창 위쪽에 맞춰 띄우므로 같은 y에 그리면 줄이 맞는다.
        """
        dpr = self.devicePixelRatioF() or 1
        while len(self.inline_rows) < len(rows):
            b = QPushButton(self.inline)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, b=b: self._inline_clicked(b))
            self.inline_rows.append(b)
        for b in self.inline_rows[len(rows):]:
            b.hide()
        for b, d in zip(self.inline_rows, rows):
            bg, fg = TONES[d['kind']]
            rec = _c(d['rec']) if d['rec'] else ''
            b.setText(' %s%s' % (rec + '  ' if rec else '', d['verdict']))
            b.setStyleSheet('QPushButton { background: %s; color: %s; border: 0; border-radius: 6px;'
                            ' text-align: left; padding: 0 8px; font-size: 12px; font-weight: 600; }' % (bg, fg))
            tip = ['%s · 내 가격 %s' % (d['name'], '입력 전' if d['mine'] == 0 else _c(d['mine'])),
                   '지금 최저가 %s%s' % (d['low'], ' (%s)' % d['lowNote'] if d['lowNote'] else ''),
                   '%s %s' % (d['avgNote'] or '평균', d['avg'])]
            if d['rec']:
                tip.append('눌러서 추천가 %s 복사' % _c(d['rec']))
            b.setToolTip('\n'.join(tip))
            b.setGeometry(0, max(0, int(d['row'].y / dpr) - INLINE_H // 2), INLINE_W, INLINE_H)
            self._inline_data[b] = d
            b.show()
        h = self.state['region'][3] if self.state else 0
        self.resize(INLINE_W, max(60, int(h / dpr)))

    def _inline_clicked(self, b):
        d = self._inline_data.get(b)
        if d and d.get('rec'):
            self._copy_row(d)

    def _copy(self, row, col):
        if col == 4 and 0 <= row < len(self._rows) and self._rows[row]['rec']:
            QGuiApplication.clipboard().setText(str(self._rows[row]['rec']))
            self.note.setText('%s 추천가 %s 을(를) 복사했습니다 — 게임 노점 창에 붙여 넣으세요' % (
                self._rows[row]['name'], _c(self._rows[row]['rec'])))

    # ------------------------------------------------------------ 위치
    def _place(self):
        """노점 개설 창 오른쪽에 붙인다(물리 픽셀로 Win32 배치)."""
        cx, cy, _cw, _ch = self.state['client']
        x, y, w, _h = self.state['region']
        fn = ctypes.windll.user32.SetWindowPos
        fn.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
        fn(int(self.winId()), wt.HWND(-1), cx + x + w + 6, cy + y, 0, 0, 0x0001 | 0x0010)   # NOSIZE|NOACTIVATE

    def _follow(self):
        """게임 창이 맨 앞이 아니면 숨기고, 돌아오면 다시 보인다."""
        if not self.state:
            return
        fg = ctypes.windll.user32.GetForegroundWindow()
        if (fg == self.state['hwnd'] or fg == int(self.winId())) and not self.state.get('dialog'):
            if not self.isVisible():
                self.show()
                self._place()
        elif self.isVisible():
            self.hide()
