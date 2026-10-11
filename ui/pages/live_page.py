"""Passive Live 탭: 상태 · 현재 세션 · 영역 설정(Calibration) · 이벤트 모니터 · Screen Inspector(개발자)."""
import datetime
import json
import os
import sqlite3
import time
from contextlib import closing

import cv2
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from core import elements
from core import loot_rows as LR
from live.events import SOURCE_ICON
from live.hud_model import PAGE_MODE_LABEL as STATE_TEXT
from live.profile import REGION_KINDS, Region
from live.vision import dominant_color, suggest_bars
from ui.buff_panel import BuffPanel
from ui.loot_table import LootTable
from ui.theme import T
from ui.widgets import Col, Kpi, Table, comma, hms, unit

REGION_COLORS = {'HP_REGION': '#e53935', 'SP_REGION': '#1e88e5', 'AP_REGION': '#fdd835', 'EXP_REGION': '#8e24aa',
                 'JOB_EXP_REGION': '#6d4c41', 'WEIGHT_REGION': '#00897b', 'CHAT_REGION': '#43a047',
                 'MAP_REGION': '#fb8c00', 'BUFF_REGION': '#d81b60', 'ITEM_MESSAGE_REGION': '#7cb342'}


LOOT_IDLE = ('획득 아이템 — 리플레이를 켠 채 플레이하면 얻은 아이템이 모두 여기에 실시간으로 쌓입니다(●). '
             '리플레이가 없으면 화면에서 읽은 드랍(○)만 보입니다.')
LOOT_PRICE_SEC = 5       # 얻은 것이 그대로여도 이 간격으로는 시세를 다시 읽는다(방금 받아 온 시세 반영)


def to_pixmap(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())


class RegionCanvas(QLabel):
    """캡처 프레임 위에서 드래그로 영역 지정. 좌표는 원본(클라이언트) 픽셀로 변환해 저장."""

    def __init__(self, frame, regions):
        super().__init__()
        self.frame, self.regions = frame, regions
        self.kind = 'HP_REGION'
        self.scale = min(1.0, 1400 / frame.shape[1], 780 / frame.shape[0])
        self.base = to_pixmap(frame).scaled(int(frame.shape[1] * self.scale), int(frame.shape[0] * self.scale),
                                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.drag = None
        self.redraw()

    def redraw(self, temp=None):
        pm = self.base.copy()
        p = QPainter(pm)
        for k, r in self.regions.items():
            p.setPen(QPen(QColor(REGION_COLORS.get(k, '#fff')), 2))
            rect = QRect(int(r.x * self.scale), int(r.y * self.scale), int(r.w * self.scale), int(r.h * self.scale))
            p.drawRect(rect)
            p.drawText(rect.topLeft() + QPoint(2, -2), REGION_KINDS[k][0])
        if temp:
            p.setPen(QPen(QColor('#ffffff'), 1, Qt.DashLine))
            p.drawRect(temp)
        p.end()
        self.setPixmap(pm)

    def mousePressEvent(self, e):
        self.drag = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.drag:
            self.redraw(QRect(self.drag, e.position().toPoint()).normalized())

    def mouseReleaseEvent(self, e):
        if not self.drag:
            return
        r = QRect(self.drag, e.position().toPoint()).normalized()
        self.drag = None
        if r.width() < 4 or r.height() < 3:
            return
        x, y, w, h = (int(v / self.scale) for v in (r.x(), r.y(), r.width(), r.height()))
        reg = Region(self.kind, x, y, w, h)
        if REGION_KINDS[self.kind][1] == 'bar':
            crop = reg.crop(self.frame)
            reg.bar_color = dominant_color(crop) if crop is not None else []
        self.regions[self.kind] = reg
        self.redraw()


class CalibrationDialog(QDialog):
    def __init__(self, frame, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle('화면 영역 설정 — %dx%d' % (profile.width, profile.height))
        self.profile = profile
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.kind = QComboBox()
        for k, (label, typ) in REGION_KINDS.items():
            self.kind.addItem('%s (%s)' % (label, k), k)
        top.addWidget(QLabel('지정할 영역:'))
        top.addWidget(self.kind)
        b_del = QPushButton('선택 영역 삭제')
        top.addWidget(b_del)
        b_auto = QPushButton('막대 자동 추천')
        b_auto.setToolTip('화면에서 게이지 막대처럼 보이는 곳을 찾아 HP/SP/AP 후보로 넣습니다. 확인 후 저장하세요.')
        top.addWidget(b_auto)
        top.addStretch(1)
        v.addLayout(top)
        self.note = QLabel('게임 화면에서 해당 UI를 마우스로 드래그하세요. HP/SP/AP는 게이지 막대, 채팅창은 메시지가 '
                           '표시되는 영역 전체, EXP는 경험치 % 숫자가 보이는 부분.' +
                           (' ⚠ 다른 해상도 값을 환산한 영역입니다 — 확인 후 저장하세요.' if profile.derived_from else ''))
        self.note.setWordWrap(True)
        v.addWidget(self.note)
        self.canvas = RegionCanvas(frame, dict(profile.regions))
        v.addWidget(self.canvas, 1)
        self.kind.currentIndexChanged.connect(lambda: setattr(self.canvas, 'kind', self.kind.currentData()))
        b_del.clicked.connect(self._delete)
        b_auto.clicked.connect(self._auto)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _delete(self):
        self.canvas.regions.pop(self.kind.currentData(), None)
        self.canvas.redraw()

    def _auto(self):
        """막대 후보 자동 채우기. 추천일 뿐이므로 저장 전까지 '확인 필요'로 둔다."""
        found = suggest_bars(self.canvas.frame)
        for kind, (x, y, w, h) in found:
            reg = Region(kind, x, y, w, h)
            crop = reg.crop(self.canvas.frame)
            reg.bar_color = dominant_color(crop) if crop is not None else []
            self.canvas.regions[kind] = reg
        self.canvas.redraw()
        self.note.setText(('막대 후보 %d개를 넣었습니다: %s. ⚠ 추천 영역은 **지금 채워져 있는 부분**이라 '
                           '좌우로 끌어 막대 전체(빈 부분 포함)를 덮도록 고친 뒤 저장하세요.'
                           % (len(found), ', '.join(REGION_KINDS[k][0] for k, _ in found)))
                          if found else '게이지 막대로 보이는 곳을 찾지 못했습니다. 직접 드래그해 주세요.')

    def result_profile(self):
        self.profile.regions = self.canvas.regions
        self.profile.calibrated = bool(self.canvas.regions)
        self.profile.derived_from = ''
        return self.profile


class LivePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode = QLabel('WAITING FOR RAGNAROK')
        self.mode.setObjectName('liveMode')
        top.addWidget(self.mode)
        self.info = QLabel()
        self.info.setWordWrap(True)
        top.addWidget(self.info, 1)
        for text, slot in (('Live 켜기/끄기', self.app.toggle_live), ('영역 설정', self.calibrate),
                           ('세션 종료·저장', self.end_session), ('네비게이션 열기', self.open_navigator)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        self.b_overlay = QToolButton()
        self.b_overlay.setText('오버레이')
        self.b_overlay.setPopupMode(QToolButton.InstantPopup)
        self.b_overlay.setMenu(self.app.overlay_menu(self.b_overlay))
        top.addWidget(self.b_overlay)
        self.b_queue = QPushButton()
        self.b_queue.setCheckable(True)
        self.b_queue.setChecked(bool(self.app.s.get('queue_alert')))
        self.b_queue.setToolTip('인던 대기 순번이 기준 이하로 내려오면 소리와 음성으로 알립니다. 리플레이 녹화가 켜져 있어야 '
                                '순번을 읽습니다. 기준은 오른쪽 목록에서 고릅니다.')
        self.b_queue.toggled.connect(self.toggle_queue_alert)
        top.addWidget(self.b_queue)
        self.queue_below = QComboBox()
        self.queue_below.setToolTip('대기 순번이 이 숫자 이하가 되면 알립니다.')
        for n in range(1, 31):
            self.queue_below.addItem('%d 이하' % n, n)
        self.queue_below.setCurrentIndex(max(0, self.queue_below.findData(int(self.app.s['queue_alert_below']))))
        self.queue_below.currentIndexChanged.connect(lambda _i: self.set_queue_threshold(self.queue_below.currentData()))
        top.addWidget(self.queue_below)
        self._label_queue()
        self.b_mute = QPushButton('알림 음소거')
        self.b_mute.setCheckable(True)
        self.b_mute.toggled.connect(self.toggle_mute)
        top.addWidget(self.b_mute)
        v.addLayout(top)

        self.page_note = QLabel('지금 게임에서 확인되는 상태를 한눈에 봅니다. 효과가 끝나면 60초 동안 재사용 알림이 뜹니다.')
        self.page_note.setObjectName('cardTitle')
        v.addWidget(self.page_note)

        self.strip_title = QLabel('현재 효과')
        self.strip_title.setObjectName('cardTitle')
        v.addWidget(self.strip_title)
        self.strip = QWidget()
        self.strip_layout = QHBoxLayout(self.strip)
        self.strip_layout.setContentsMargins(0, 0, 0, 0)
        self.strip_layout.setSpacing(10)
        self.strip_empty = QLabel('확인된 효과 없음')
        self.strip_empty.setObjectName('cardTitle')
        self.strip_layout.addWidget(self.strip_empty)
        self.strip_layout.addStretch(1)
        v.addWidget(self.strip)
        self._strip_widgets = {}

        g = QGridLayout()
        self.kpi = {}
        for i, (k, t) in enumerate((('elapsed', '플레이 시간'), ('exp', '시간당 경험치'), ('profit', '현재 수익'),
                     ('profit_h', '시간당 수익'), ('kills', '처치 수'), ('dps', '전투 DPS'),
                     ('status', 'HP · SP · AP'))):
            self.kpi[k] = Kpi(t)
            self.kpi[k].sub.setWordWrap(True)
            self.kpi[k].value.setWordWrap(True)
            self.kpi[k].setMaximumHeight(86)
            g.addWidget(self.kpi[k], i // 4, i % 4)
        v.addLayout(g)
        self.note = QLabel('Screen 기준 값(○)은 화면에서 실제 감지한 것만 집계합니다. Kill/DPS(●)는 Replay 녹화가 감지될 때만 표시됩니다.')
        self.note.setObjectName('cardTitle')
        v.addWidget(self.note)
        self.elem_label = QLabel('추천 속성: Replay 녹화 중이면 이 맵에서 본 몬스터로 계산합니다')
        self.elem_label.setObjectName('elemAdvice')
        self.elem_label.setStyleSheet('QLabel#elemAdvice { background: %s; border: 1px solid %s; border-radius: 8px;'
                                      ' padding: 8px 10px; }' % (T['surface2'], T['accent']))
        self.elem_label.setWordWrap(True)
        v.addWidget(self.elem_label)
        self._elem_key = None
        self.replay_summary = QLabel('Replay 녹화 대기')
        self.replay_summary.setWordWrap(True)
        self.replay_summary.setTextFormat(Qt.PlainText)
        v.addWidget(self.replay_summary)
        self._replay_revision = (None, -1)
        self.last_replay_snap = None          # Buff 타이머 등 다른 화면이 같은 스냅샷을 다시 읽지 않도록

        tabs = QTabWidget()
        v.addWidget(tabs, 1)
        sp = QSplitter(Qt.Horizontal)
        self.drops = LootTable(self.app.s, self.app.loot_icons() if hasattr(self.app, 'loot_icons') else None,
                               last=True)
        self.drops_note = QLabel(LOOT_IDLE)
        self.drops_note.setWordWrap(True)
        self._loot_key, self._loot_priced_at = None, 0.0
        drops_box = QWidget()
        dl = QVBoxLayout(drops_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(self.drops_note)
        dl.addWidget(self.drops)
        sp.addWidget(drops_box)
        self.review = Table([Col('시각', lambda r: datetime.datetime.fromtimestamp(r['time']).strftime('%H:%M:%S'), width=70),
                             Col('인식 이름', lambda r: r['name'], width=150), Col('수량', lambda r: r['qty'], comma, width=50),
                             Col('사유', lambda r: r['reason'], width=80), Col('원문', lambda r: r['text'])])
        self.review.doubleClicked.connect(lambda idx: self.correct(self.review.row_at(idx)))
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(QLabel('검토 필요(수익 제외) — 더블클릭해서 올바른 아이템명으로 교정하면 OCR 별칭으로 학습'))
        bl.addWidget(self.review)
        sp.addWidget(box)
        tabs.addTab(sp, '현재 세션')
        self.event_tbl = Table([
            Col('시각', lambda e: datetime.datetime.fromtimestamp(e.timestamp).strftime('%H:%M:%S'), width=70),
            Col('출처', lambda e: SOURCE_ICON.get(e.source, e.source), width=80), Col('이벤트', lambda e: e.event_type, width=120),
            Col('신뢰도', lambda e: e.confidence, width=60),
            Col('내용', lambda e: ' '.join(str(x) for x in (e.item_name, ('×%s' % e.quantity) if e.quantity else '',
                                                           e.value if e.value is not None else '', e.map or '') if x))])
        tabs.addTab(self.event_tbl, '이벤트 모니터')
        self.buff_panel = BuffPanel(self.app)
        tabs.addTab(self.buff_panel, '버프 타이머')
        self.hist_tbl = Table([
            Col('시작', lambda r: datetime.datetime.fromtimestamp(r[1]).strftime('%m-%d %H:%M'), width=90),
            Col('모드', lambda r: r[3], width=70), Col('신뢰도', lambda r: r[4], width=150), Col('맵', lambda r: r[5], width=120),
            Col('활성', lambda r: (r[7] or 0) * 1000, hms, True, 70), Col('수익', lambda r: r[11], comma, width=100),
            Col('수익/시간', lambda r: r[12], unit, width=90), Col('경험치%/시간', lambda r: r[13], lambda v: '-' if v is None else '%.2f%%' % v, True, 70),
            Col('처치', lambda r: r[14], comma, width=60), Col('DPS', lambda r: r[15], unit)], sort_col=0)
        tabs.addTab(self.hist_tbl, 'Live 세션 기록')
        self.final_tbl = Table([Col('Replay', lambda r: r['file'], width=160),
            Col('LIVE / FINAL 일치', lambda r: '일치' if r['equal'] else '차이 있음', width=140),
            Col('Damage', lambda r: r['damage'], comma), Col('Kills', lambda r: r['kills'], comma),
            Col('저장 시각', lambda r: r['time'], width=160)])
        tabs.addTab(self.final_tbl, 'Replay FINAL 기록')
        self.final_tbl.doubleClicked.connect(lambda idx: self.app._load(self.final_tbl.row_at(idx)['path']))
        self._last_final_session = None
        self.inspector = QWidget()
        iv = QVBoxLayout(self.inspector)
        row = QHBoxLayout()
        for text, slot in (('화면 새로 보기', self.refresh_inspector), ('채팅 줄로 패턴 학습', self.learn_pattern)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        iv.addLayout(row)
        isp = QSplitter(Qt.Horizontal)
        self.preview = QLabel('Live 실행 중 [화면 새로 보기]를 누르세요.')
        self.preview.setAlignment(Qt.AlignCenter)
        isp.addWidget(self.preview)
        self.chat_tbl = Table([Col('시각', lambda r: datetime.datetime.fromtimestamp(r[0]).strftime('%H:%M:%S'), width=70),
                               Col('OCR 채팅 줄', lambda r: r[1], width=360), Col('conf', lambda r: r[2])])
        isp.addWidget(self.chat_tbl)
        iv.addWidget(isp, 1)
        self.acc = QLabel()
        iv.addWidget(self.acc)
        self.inspector_index = tabs.addTab(self.inspector, 'Screen Inspector')
        self.tabs = tabs
        self.replay_details = QPlainTextEdit()
        self.replay_details.setReadOnly(True)
        tabs.addTab(self.replay_details, 'Replay 상세 · Skill / Buff / Loot')
        self.replay_diag = QPlainTextEdit()
        self.replay_diag.setReadOnly(True)
        self.replay_diag_index = tabs.addTab(self.replay_diag, 'Live Diagnostics / Accuracy')
        self._toggle_inspector()
        self.timer = QTimer(self, interval=max(100, self.app.s.get('live_snapshot_ms', 200)), timeout=self.refresh)
        self.timer.start()

    def _toggle_inspector(self):
        self.tabs.setTabVisible(self.inspector_index, bool(self.app.s.get('developer_mode')))
        self.tabs.setTabVisible(self.replay_diag_index, bool(self.app.s.get('developer_mode')))

    # ------------------------------------------------------------------ 주기 갱신
    def refresh(self):
        eng = self.app.live
        if eng is None:
            self.mode.setText('LIVE OFF')
            self._mode_color('off')
            self.info.setText('Live 모드가 꺼져 있습니다. [Live 켜기/끄기]로 시작하세요.')
            self._label_queue()
            return
        snap = eng.snapshot()
        self._replay_active = bool(snap.get('replay_active'))
        s = snap.get('session') or {}
        st = 'PAUSED' if s.get('state') == 'PAUSED' else snap['state']
        self.mode.setText(STATE_TEXT.get(st, st))
        self._mode_color('paused' if st == 'PAUSED' else ('off' if st == 'WAITING' else 'live'))
        w = snap.get('window')
        self.info.setText('%s · 캡처 %s · %s · 맵 %s%s%s' % (
            snap['status'], snap['capture'], '영역 보정됨' if snap['calibrated'] else '영역 미보정',
            ('%s %s' % (snap['map_source'], snap['map'])) if snap['map'] else '? Unknown',
            (' · Replay: %s' % snap['replay_file'].replace('\\', '/').split('/')[-1]) if snap['replay_active'] else '',
            (' (해석 대기: %s)' % snap['replay_error'][:40]) if snap['replay_error'] else ''))
        if snap.get('ocr_error'):
            self.info.setText(self.info.text() + '\n⚠ 글자 인식 사용 불가: ' + snap['ocr_error'])
        rp = s.get('replay') or {}
        self.kpi['elapsed'].set(hms((s.get('active') or 0) * 1000) if s else '-', ('전체 %s' % hms(s['elapsed'] * 1000)) if s else '세션 없음')
        e = s.get('exp_pct_per_hour')
        self.kpi['exp'].set(('%+.2f%%' % e) if e is not None else '-', 'EXP % 기준' if e is not None else 'EXP 영역 미설정/미감지')
        self.kpi['profit'].set(comma(s.get('profit')) + ' Z' if s else '-', '화면 감지 기준' if s else '')
        self.kpi['profit_h'].set(unit(s.get('profit_per_hour')) if s.get('profit_per_hour') is not None else '-')
        kills = rp.get('kills')
        self.kpi['kills'].set('{:,}'.format(int(kills * 3600 / max(s.get('active') or 1, 1))) if kills is not None else '-',
                              'Replay 필요' if kills is None else '%s마리' % comma(kills))
        self.kpi['dps'].set(unit(rp.get('dps')) if rp.get('dps') else '-', 'Replay 필요' if not rp else '')
        vals = snap['values']
        self.kpi['status'].set(' / '.join(('%.0f%%' % vals[k]) if vals.get(k) is not None else '-'
                                          for k in ('HP_REGION', 'SP_REGION', 'AP_REGION', 'WEIGHT_REGION')))
        if not self._refresh_loot(snap.get('replay_live')):      # 리플레이가 없을 때만 화면에서 읽은 드랍
            npc = getattr(self.app, 'npc_price', None)
            self.drops.set_rows([LR.row(d.get('item_id'), d['name'], d['qty'], d.get('unit'), '화면에서 읽은 드랍 · 시세 탭 시세',
                                        npc(d['item_id']) if npc and d.get('item_id') else None)
                                 for d in snap.get('drops', [])])
        self.review.set_rows(snap.get('review', []))
        self.event_tbl.set_rows(list(reversed(snap.get('events', []))))
        if self.tabs.currentWidget() is self.inspector:
            self.chat_tbl.set_rows(list(reversed(snap.get('chat', []))))
            acc = s.get('accuracy') or {}
            self.acc.setText('Replay 대비 화면 감지: ' + (', '.join('%s Replay %d / Screen %d (%.1f%%)' % (k, v['replay'], v['screen'], v['rate'])
                                                               for k, v in acc.items()) or 'Replay 기준 데이터 없음'))
        self.last_replay_snap = snap.get('replay_live')
        self._refresh_replay(snap.get('replay_live'))
        self.app.update_hud(snap)

    # ------------------------------------------------------------------ 현재 효과 Strip
    def update_status_strip(self, rows):
        """아이콘 + 남은 시간만 작게. 이름과 자세한 내용은 Tooltip."""
        from live.status import EXPIRED_ALERT
        from live.buffs import mmss
        keep = set()
        for i, r in enumerate(rows[:10]):
            keep.add(r['key'])
            w = self._strip_widgets.get(r['key'])
            if w is None:
                w = QLabel()
                w.setTextFormat(Qt.RichText)
                w.setAlignment(Qt.AlignCenter)
                self._strip_widgets[r['key']] = w
                self.strip_layout.insertWidget(self.strip_layout.count() - 1, w)
            pm = self.app.icons.pixmap(r['key'], r.get('source', 'OTHER'), 24, r['name'])
            w.setPixmap(pm)
            time_text = mmss(r.get('remaining'))
            if r['state'] == EXPIRED_ALERT:
                time_text = '재사용!! %s' % time_text
            w.setToolTip('%s\n%s · %s\n%s' % (r['name'], r['label'], time_text, r.get('note', '')))
            w.setStyleSheet('QLabel{border:2px solid %s; border-radius:6px; padding:2px;}' % r['color'])
            w.setVisible(True)
        for key, w in list(self._strip_widgets.items()):
            if key not in keep:
                w.setParent(None)
                del self._strip_widgets[key]
        self.strip_empty.setVisible(not rows)
        names = ' · '.join('%s %s' % (r['name'], (r['label'] if r['state'] == EXPIRED_ALERT
                                                  else mmss(r.get('remaining')))) for r in rows[:6])
        self.strip_title.setText('현재 효과' + ('   ' + names if names else ''))

    def open_navigator(self):
        """LIVE에서 보던 상황(캐릭터·직업·레벨·현재 맵)을 그대로 넘기며 네비게이션 화면으로."""
        self.app.open_navigator_from_live()

    def _mode_color(self, state):
        if self.mode.property('state') != state:
            self.mode.setProperty('state', state)
            self.mode.style().unpolish(self.mode)
            self.mode.style().polish(self.mode)

    def _refresh_loot(self, rp):
        """리플레이가 붙어 있으면 획득 패킷으로 센 아이템을 전부 보여 준다(●). 방금 얻은 것이 맨 위.

        창고·카트에서 꺼낸 것과 산 것은 들어 있지 않다. 반환: 이 표를 리플레이 획득으로 채웠는가."""
        if not rp or rp.get('loot_confidence') != 'CONFIRMED':
            self._loot_key = None
            self.drops_note.setText(LOOT_IDLE)
            return False
        led = rp.get('ledger') or {}
        items = {int(k): q for k, q in (led.get('items') or {}).items()}
        key = (rp.get('session_id'), tuple(sorted(items.items())))
        now = time.monotonic()
        if key == self._loot_key and now - self._loot_priced_at < LOOT_PRICE_SEC:
            return True                               # 달라진 게 없으면 표를 다시 그리지 않는다(선택·스크롤 유지)
        self._loot_key, self._loot_priced_at = key, now
        last = {int(k): dict(last=t) for k, t in (led.get('last') or {}).items()}
        rows = LR.build(items, self.app.item_db.name, getattr(self.app, 'vend_price', None),
                        getattr(self.app, 'npc_price', None), extra=last)
        if hasattr(self.drops, 'set_overcharge'):
            self.drops.set_overcharge(int(self.app.s.get('overcharge_rate', LR.OVERCHARGE_MAX) or 0))
        self.drops.set_rows(rows)
        t = LR.totals(rows)
        money = lambda v: '모름' if v is None else comma(v) + ' Z'                    # noqa: E731
        self.drops_note.setText(
            '획득 아이템 ● %d종 %s개 · 가치 %s ○ (노점에 팔면 %s · 상점에 팔면 %s △)%s — 리플레이 획득 패킷 기준'
            '(창고·카트에서 꺼낸 것, 산 것 제외)' % (
                t['count'], comma(t['quantity']), money(t['best']), money(t['vend']), money(t['npc']),
                ' · 값 모름 %d종은 뺌' % t['unpriced'] if t['unpriced'] else ''))
        return True

    def _label_queue(self, queue=None):
        text = '대기열 알림'
        if not self.b_queue.isChecked():
            self.b_queue.setText(text)
            return
        # 켜 둔 동안에는 순번을 읽고 있는지 버튼에서 바로 보이게 한다
        if queue:
            state = '지금 %d' % queue['position']
        elif self.app.live is None:
            state = 'Live 꺼짐'
        elif not getattr(self, '_replay_active', False):
            state = '리플레이 녹화 필요'
        else:
            state = '대기 없음'
        self.b_queue.setText('%s ✓ · %s' % (text, state))

    def toggle_queue_alert(self, on):
        self.app.s['queue_alert'] = bool(on)
        self.app._save_settings()
        self._label_queue()
        self._prepare_queue_sound()

    def _prepare_queue_sound(self):
        """알릴 때 늦지 않게 음성 파일을 미리 만들어 둔다."""
        if self.app.s['queue_alert']:
            import threading
            threading.Thread(target=self.app._queue_sound, args=(self.app.s['queue_alert_below'],), daemon=True).start()

    def set_queue_threshold(self, value):
        self.app.s['queue_alert_below'] = int(value)
        self.app._save_settings()
        self._prepare_queue_sound()

    def _tracking_line(self, rp):
        """퀘스트 사냥 수 · 재사용 대기 · 대상 HP · 드랍 — 전부 리플레이 패킷으로 따라간 값(●)."""
        parts = []
        title = getattr(self.app.analysis.gamedata, 'quest_title', lambda q: None)
        active = [q for q in rp.get('quests') or [] if not q['removed'] and q['goals']
                  and any(g['need'] and (g['count'] or 0) < g['need'] for g in q['goals'])]
        active.sort(key=lambda q: -(q['at_ms'] or -1))                # 방금 잡은 퀘스트가 앞
        if active:
            parts.append('퀘스트 ' + ' · '.join('%s %d/%d' % (
                title(q['quest']) or '퀘스트 %s' % q['quest'], sum(g['count'] or 0 for g in q['goals']),
                sum(g['need'] or 0 for g in q['goals'])) for q in active[:3]))
        self._label_queue(rp.get('queue'))
        if rp.get('queue'):
            parts.append('인던 대기 %s %d번째' % (rp['queue']['name'], rp['queue']['position']))
        cools = rp.get('cooldowns') or []
        if cools:
            parts.append('재사용 대기 ' + ' · '.join('%s %d초' % (
                self.app.analysis.gamedata.skills.name(c['skill']), round(c['remain_ms'] / 1000)) for c in cools[:4]))
        if rp.get('target_hp') is not None:
            parts.append('대상 HP %d%%' % rp['target_hp'])
        dc = rp.get('drop_counts') or {}
        if dc.get('dropped'):
            parts.append('드랍 %s건 중 주움 %s · 놓침 %s' % (comma(dc['dropped']), comma(dc['picked']), comma(dc['missed'])))
        return ' │ '.join(parts)

    def _refresh_replay(self, rp):
        if not rp:
            return
        sid, rev = rp['session_id'], rp['revision']
        if sid == self._replay_revision[0] and rev < self._replay_revision[1]:
            return
        self.last_replay_snap = rp
        metrics = rp.get('metrics', {})
        self.mode.setText('FINAL' if rp['stage'] == 'FINAL' else '● LIVE')
        self._mode_color('live')
        self.info.setText('%s · %s · %s · %s' % (rp['stage'], rp['state'], rp['quality'], rp['file']))
        self.kpi['elapsed'].set(hms(rp.get('duration_ms', 0)), 'Combat ' + hms(rp.get('combat_ms', 0)))
        self.kpi['dps'].set(unit(rp.get('combat_dps')), 'Session ' + unit(rp.get('session_dps')))
        self.kpi['kills'].set(comma(metrics.get('kills')) if rp.get('combat_available') else '-', '확인된 사망 · 세션 누계')
        self.kpi['exp'].set(unit(rp.get('exp_per_hour')), 'Replay EXP · 미검증 후보 사용 시 △')
        self.kpi['profit'].set(unit(rp.get('profit')), rp.get('profit_note', '획득/시세 정보 부족'))
        self.kpi['profit_h'].set(unit(rp.get('profit_per_hour')))
        resources = rp.get('resources', {})
        resource_text = []
        for key in ('HP', 'SP', 'AP'):
            r = resources.get(key, {})
            value = '%s/%s%s' % (comma(r.get('value')), comma(r.get('max_value')), ' · 오래됨' if r.get('stale') else '')
            resource_text.append(value if r.get('value') is not None else '미지원/미수신')
        self.kpi['status'].set(' · '.join(resource_text), 'Replay에서 읽은 실제 값')
        rolling = rp.get('rolling_dps', {})
        buffs = [self.app.display.status(b['buff_id']).text + (' (예상 종료)' if b.get('ending') == 'ESTIMATED' else '')
                 for b in rp.get('buffs', []) if not self.app.display.status(b['buff_id']).internal]
        tracking_line = self._tracking_line(rp)
        self.replay_summary.setText(
            '%s · %s · Lv.%s · %s\n최근 DPS 1초 %s · 5초 %s · 10초 %s · 시간당 제니 %s\n효과: %s' % (
                rp.get('character') or '캐릭터 확인 안 됨',
                self.app.display.job(rp.get('job_id')).text, rp.get('level') or '?',
                self.app.display.map(rp.get('map')).with_raw(bool(self.app.s.get('developer_mode'))),
                unit(rolling.get('1')), unit(rolling.get('5')), unit(rolling.get('10')),
                unit(rp.get('zeny_per_hour')), ', '.join(buffs) or '확인된 효과 없음')
            + ('\n' + tracking_line if tracking_line else ''))
        self.note.setText(rp.get('coverage_note', '') + ('' if rp.get('loot_confidence') == 'CONFIRMED'
                                                         else ' · 인벤토리 순증감의 드랍 출처는 미확인'))
        self.note.setWordWrap(True)
        self._refresh_elements(rp.get('map'), rp.get('monsters') or [])
        if (sid, rev) != self._replay_revision:
            skills = ['%s: Cast %s / Hit packet %s / Damage %s' % (
                self.app.analysis.gamedata.skills.name(row['skill_id']), row['casts'], row['hits'], comma(row['damage']))
                for row in rp.get('skills', [])]
            loot = ['%s × %s' % (self.app.item_db.name(int(iid)), qty) for iid, qty in metrics.get('loot', {}).items()]
            packets = rp.get('loot_confidence') == 'CONFIRMED'
            self.replay_details.setPlainText('Top Skills\n' + ('\n'.join(skills) or '관측된 스킬 없음') +
                '\n\nBuff\n' + (', '.join(buffs) or '관측된 활성 Buff 없음') +
                ('\n\n획득 아이템 ● (획득 패킷 · 창고·카트·구입 제외)\n' if packets else
                 '\n\n인벤토리 순증가 (몬스터 드랍 출처 미확인)\n') + ('\n'.join(loot) or '확인된 변화 없음') +
                '\n\nAP, 스킬 취소·후딜레이, 장비 변경: 패킷 매핑 미지원')
            if self.app.s.get('developer_mode'):
                self.replay_diag.setPlainText(json.dumps({k: rp.get(k) for k in ('diagnostics', 'accuracy', 'events', 'error')},
                                                        ensure_ascii=False, indent=2))
            self._replay_revision = (sid, rev)
        if rp['stage'] == 'FINAL' and self._last_final_session != sid:
            self._last_final_session = sid
            QTimer.singleShot(1000, self.refresh_history)
        nav = self.app.navigator_page
        if rp.get('map') and rp.get('stage') == 'CONFIRMED':
            nav.set_live_map(rp['map'])

    def _refresh_elements(self, map_id, monsters):
        key = (map_id, tuple((m['class_id'], m['weight']) for m in monsters))
        if key == self._elem_key:
            return
        self._elem_key = key
        adv = elements.shared()
        map_name = self.app.display.map(map_id).text if map_id else ''
        basis = '관측'
        if not monsters and map_name:                 # 아직 몬스터를 못 봤으면 공식 출현 목록으로
            monsters, basis = adv.map_monsters(map_name), '공식 출현 목록'
        rec = adv.recommend(monsters)
        self.elem_label.setText('%s — %s' % (map_name or '맵 확인 중', elements.summary_text(rec, basis=basis)))
        self.elem_label.setToolTip('\n'.join(
            '%s × %d — %s' % (m.get('name') or m['info']['name'], m['weight'], elements.traits_text(m['info']))
            for m in rec['known']) or '몬스터 없음')

    def refresh_history(self):
        try:
            self.hist_tbl.set_rows(self.app.db._query('SELECT * FROM live_sessions ORDER BY start_time DESC LIMIT 500'))
        except Exception:
            self.hist_tbl.set_rows([])
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'live_replay.db')
        if os.path.isfile(path):
            try:
                with closing(sqlite3.connect(path)) as conn:
                    raw = conn.execute('SELECT path,updated,data_json FROM live_accuracy_reports ORDER BY updated DESC LIMIT 100').fetchall()
                rows = []
                for path, updated, data in raw:
                    obj = json.loads(data)
                    if obj.get('stage', 'FINAL') != 'FINAL':
                        continue
                    m = obj['view']['metrics']
                    rows.append(dict(path=path, file=os.path.basename(path), equal=obj['accuracy']['equal'], damage=m['damage'],
                        kills=m['kills'], time=datetime.datetime.fromtimestamp(updated).strftime('%m-%d %H:%M:%S')))
                self.final_tbl.set_rows(rows)
            except (sqlite3.Error, ValueError, KeyError):
                self.final_tbl.set_rows([])

    # ------------------------------------------------------------------ 동작
    def calibrate(self):
        eng = self.app.live
        frame = eng.last_frame if eng else None
        if frame is None:
            QMessageBox.information(self, '영역 설정', 'Ragnarok 창 화면을 아직 받지 못했습니다. 게임에 접속한 상태에서 다시 시도하세요.')
            return
        dlg = CalibrationDialog(frame.copy(), eng.profile, self)
        dlg.resize(1500, 900)
        if dlg.exec():
            p = dlg.result_profile()
            eng.profiles.save(p)
            eng.profile = p
            self.app.status('영역 %d개 저장 (%s)' % (len(p.regions), p.name))

    def end_session(self):
        if self.app.live:
            s = self.app.live.end_session()
            self.refresh_history()
            self.app.status('세션 저장됨' if s else '진행 중인 세션이 없습니다')

    def toggle_mute(self, on=None):
        a = self.app.alerts
        a.muted = (not a.muted) if on is None else bool(on)
        self.b_mute.setText('알림 음소거 중' if a.muted else '알림 음소거')
        self.app.status('알림 음소거' if a.muted else '알림 소리 켬')

    def refresh_inspector(self):
        eng = self.app.live
        frame = eng.last_frame if eng else None
        if frame is None:
            self.preview.setText('화면 없음')
            return
        img = frame.copy()
        for k, r in (eng.profile.regions if eng.profile else {}).items():
            c = QColor(REGION_COLORS.get(k, '#ffffff'))
            cv2.rectangle(img, (r.x, r.y), (r.x + r.w, r.y + r.h), (c.blue(), c.green(), c.red()), 2)
            txt = eng.region_text.get(k) or ('%.1f%%' % eng.region_values[k] if k in eng.region_values else '')
            cv2.putText(img, k.replace('_REGION', '') + ' ' + txt[:20].encode('ascii', 'ignore').decode(),
                        (r.x, max(12, r.y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (c.blue(), c.green(), c.red()), 1)
        pm = to_pixmap(img)
        self.preview.setPixmap(pm.scaled(self.preview.width(), self.preview.height(), Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))

    def learn_pattern(self):
        rows = self.chat_tbl.selected_rows()
        if not rows:
            QMessageBox.information(self, '패턴 학습', '오른쪽 표에서 실제 아이템 획득/제니/경험치 메시지 줄을 선택하세요.')
            return
        line = rows[0][1]
        kinds = ['ITEM_GAIN', 'ZENY_CHANGE', 'BASE_EXP_GAIN', 'JOB_EXP_GAIN']
        kind, ok = QInputDialog.getItem(self, '패턴 학습', '이 줄의 종류:\n' + line, kinds, editable=False)
        if not ok:
            return
        item = qty = value = None
        if kind == 'ITEM_GAIN':
            item, ok = QInputDialog.getText(self, '패턴 학습', '줄 안의 아이템 이름 부분(그대로 복사):\n' + line)
            if not ok:
                return
            qty, _ = QInputDialog.getText(self, '패턴 학습', '줄 안의 수량 부분(없으면 비움):\n' + line)
        else:
            value, ok = QInputDialog.getText(self, '패턴 학습', '줄 안의 숫자(금액/경험치) 부분:\n' + line)
            if not ok:
                return
        try:
            pid = self.app.live.patterns.learn(line, kind, item or None, qty or None, value or None)
            self.app.live.parser.patterns = self.app.live.patterns
            self.app.status('패턴 저장: %s' % pid)
        except Exception as e:
            QMessageBox.warning(self, '패턴 학습 실패', str(e))

    def correct(self, row):
        name, ok = QInputDialog.getText(self, 'OCR 교정', '인식 결과 "%s"의 올바른 아이템 이름:' % row['name'], text=row['name'])
        if ok and name.strip():
            self.app.live.item_alias.add(row['name'], name.strip())
            self.app.status('OCR 별칭 저장: %s → %s (다음 인식부터 적용)' % (row['name'], name.strip()))
