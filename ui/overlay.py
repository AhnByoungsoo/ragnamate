"""공용 Overlay 기반 + Navigator Mini Overlay.

게임에 입력을 보내지 않는다. 정보 표시 + 클립보드 복사만 한다.
- 항상 위 / 투명도 / Click-through(입력 통과) / 위치 저장
- Qt6는 Windows 배율(100~200%)을 자동 반영(High DPI)하며, 크기는 논리 픽셀 기준으로 저장한다.
"""
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ui import overlay_styles


class OverlayWindow(QWidget):
    """Buff Timer / Status / Storage 등에서 재사용할 Overlay 기본 창."""

    def __init__(self, settings, key, title=''):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.s, self.key = settings, key
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowTitle(title)
        self._drag = None
        cfg = (settings.get('overlays') or {}).get(key, {})
        self.setWindowOpacity(cfg.get('opacity', 0.9))
        if cfg.get('pos'):
            self.move(QPoint(*cfg['pos']))
        self.locked = bool(cfg.get('locked', False))
        self.set_click_through(cfg.get('click_through', False), save=False)

    def set_click_through(self, on, save=True):
        self.setWindowFlag(Qt.WindowTransparentForInput, on)
        if self.isVisible():
            self.show()
        if save:
            self._save(click_through=on)

    def set_locked(self, on):
        """위치 고정: 드래그로 옮기지 않는다(라그타임 lock_pos와 같은 개념)."""
        self.locked = bool(on)
        self._save(locked=self.locked)

    def set_opacity(self, v):
        self.setWindowOpacity(v)
        self._save(opacity=v)

    def _save(self, **kv):
        ov = dict(self.s.get('overlays') or {})
        cfg = dict(ov.get(self.key, {}))
        cfg.update(kv)
        ov[self.key] = cfg
        self.s['overlays'] = ov
        try:
            self.s.save()
        except Exception:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.locked:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self._save(pos=[self.x(), self.y()])


class NavOverlay(OverlayWindow):
    """현재 퀘스트 진행 오버레이. 접으면 현재 STEP·위치·다음 STEP 만, [공략 보기 ▾]로 그 STEP 세부 공략을 펼친다.
    [◀][네비 복사][완료][▶] — 네비 명령은 클립보드 복사만(게임에 입력하지 않음).
    [자동]: 게임 퀘스트 알림창을 읽어(live/quest_watch) 맞는 STEP 으로 옮기고 '지금 할 일'을 강조한다."""
    step_changed = Signal(int)
    completed = Signal(int)
    tracker = Signal(object)        # 감시 스레드 → 화면: [TrackedQuest]

    def __init__(self, settings, map_name=None):
        super().__init__(settings, 'navigator', 'RO Navigator')
        self.map_name = map_name or (lambda m: m)
        self.setStyleSheet(
            'QWidget#navRoot { background: #14171D; border: 1px solid #35598C; border-radius: 10px; }'
            'QLabel { color: #E6E9EF; background: transparent; font-size: 13px; }'
            'QLabel#t { color: #9ECBFF; font-weight: 700; }'
            'QLabel#prog { color: #8E97A8; font-size: 12px; }'
            'QLabel#cur { font-size: 14px; font-weight: 700; }'
            'QLabel#where, QLabel#nxt { color: #AAB2C0; font-size: 12px; }'
            'QLabel#navi { color: #FFD479; font-size: 12px; }'
            'QLabel#body { color: #D5DAE3; font-size: 12px; }'
            'QPushButton { background: #1F242D; color: #E6E9EF; border: 1px solid #343B48; border-radius: 6px;'
            ' padding: 3px 8px; font-size: 12px; }'
            'QPushButton:hover { border-color: #4B84C8; } QPushButton:disabled { color: #5A6170; }'
            'QPushButton#more { color: #8FC2FF; border: 0; background: transparent; padding: 2px 4px; }'
            'QPushButton#x { border: 0; background: transparent; color: #8E97A8; padding: 0 4px; }'
            'QPushButton#auto { border: 0; background: transparent; color: #5A6170; padding: 0 4px; font-size: 11px; }'
            'QPushButton#auto:checked { color: #7FE0B0; }'
            'QLabel#now { background: #1D3A2C; border-left: 3px solid #4CC38A; border-radius: 6px; padding: 6px 8px;'
            ' color: #FFFFFF; font-size: 13px; }'
            'QLabel#strip { font-size: 13px; } QPushButton#stripNavi { background: #2B5C9E; border: 0; color: #FFFFFF;'
            ' font-weight: 700; padding: 4px 12px; border-radius: 12px; }')
        root = QWidget(self)
        root.setObjectName('navRoot')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(4)
        self.head_row = QWidget()
        head = QHBoxLayout(self.head_row)
        head.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setObjectName('t')
        self.prog = QLabel()
        self.prog.setObjectName('prog')
        b_close = QPushButton('✕')
        b_close.setObjectName('x')
        b_close.setToolTip('닫기')
        cfg = (settings.get('overlays') or {}).get('navigator', {})
        self.b_auto = QPushButton('● 자동')
        self.b_auto.setObjectName('auto')
        self.b_auto.setCheckable(True)
        self.b_auto.setChecked(cfg.get('auto_follow', True))
        self.b_auto.setToolTip('게임 오른쪽 퀘스트 알림창을 읽어 지금 단계로 자동으로 옮기고 할 일을 강조합니다(화면 읽기만).')
        self.b_auto.toggled.connect(self._auto_toggled)
        head.addWidget(self.title, 1)
        head.addWidget(self.prog)
        head.addWidget(self.b_auto)
        head.addWidget(b_close)
        v.addWidget(self.head_row)
        # 한 줄 스트립: '16/22 · 그림자 간수 7/20 · 일 160,160 [네비]'
        self.strip_row = QWidget()
        sh = QHBoxLayout(self.strip_row)
        sh.setContentsMargins(0, 0, 0, 0)
        self.strip = QLabel()
        self.strip.setObjectName('strip')
        self.strip.setTextFormat(Qt.RichText)
        self.b_strip = QPushButton('네비')
        self.b_strip.setObjectName('stripNavi')
        self.b_strip.clicked.connect(self._copy_best)
        sh.addWidget(self.strip, 1)
        sh.addWidget(self.b_strip)
        v.addWidget(self.strip_row)
        self.now = QLabel()                      # '지금 할 일' 강조(알림창에서 읽은 목표·진행도)
        self.now.setObjectName('now')
        self.now.setWordWrap(True)
        self.now.setTextFormat(Qt.RichText)
        self.now.linkActivated.connect(self._copy_now)
        self.now_navi = ''
        self.now.hide()
        v.addWidget(self.now)
        self.steps_label = QLabel()              # 단계 목록형: 앞·지금·뒤 단계
        self.steps_label.setTextFormat(Qt.RichText)
        self.steps_label.setWordWrap(True)
        v.addWidget(self.steps_label)
        self.cur, self.where, self.navi, self.nxt, self.body = (QLabel() for _ in range(5))
        for w, name in ((self.cur, 'cur'), (self.where, 'where'), (self.navi, 'navi'), (self.body, 'body'),
                        (self.nxt, 'nxt')):
            w.setObjectName(name)
            w.setWordWrap(True)
            w.setTextFormat(Qt.RichText if name == 'body' else Qt.PlainText)
            v.addWidget(w)
        self.b_more = QPushButton()
        self.b_more.setObjectName('more')
        self.b_more.clicked.connect(self.toggle_more)
        v.addWidget(self.b_more, 0, Qt.AlignLeft)
        self.btn_row = QWidget()
        h = QHBoxLayout(self.btn_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.b_prev, self.b_copy, self.b_done, self.b_next = (QPushButton(t) for t in ('◀', '네비 복사', '완료 ✓', '▶'))
        self.b_prev.setToolTip('이전 단계')
        self.b_next.setToolTip('다음 단계')
        self.b_done.setToolTip('이 단계를 완료로 표시하고 다음 단계로')
        for b in (self.b_prev, self.b_copy, self.b_done, self.b_next):
            h.addWidget(b)
        h.addStretch(1)
        v.addWidget(self.btn_row)
        self.b_prev.clicked.connect(lambda: self.step_changed.emit(self.index - 1))
        self.b_next.clicked.connect(lambda: self.step_changed.emit(self.index + 1))
        self.b_done.clicked.connect(lambda: self.completed.emit(self.index))
        self.b_copy.clicked.connect(self.copy)
        b_close.clicked.connect(self.hide)
        self.setFixedWidth(360)
        self.expanded = bool((settings.get('overlays') or {}).get('navigator', {}).get('expanded', False))
        self.guide, self.index, self.done = None, 0, set()
        self._quest = None                       # 알림창에서 읽은 지금 퀘스트(스트립에 진행도 표시)
        self.style = overlay_styles.get(settings, 'quest')
        self._apply_more()

    # ------------------------------------------------------------ 모양(카드형 / 단계 목록형 / 한 줄 스트립)
    def apply_style(self):
        self.style = overlay_styles.get(self.s, 'quest')
        if self.guide:
            self.show_step(self.guide, self.index, self.done)
        else:
            self._layout()

    def _layout(self):
        card, steps, strip = (self.style == k for k in ('card', 'steps', 'strip'))
        self.head_row.setVisible(not strip)
        self.strip_row.setVisible(strip)
        self.btn_row.setVisible(not strip)
        self.steps_label.setVisible(steps)
        for w in (self.cur, self.where, self.navi, self.nxt):
            w.setVisible(card and bool(w.text()))
        self.b_more.setVisible(card and bool(self.body.text()))
        self.body.setVisible(card and self.expanded and bool(self.body.text()))
        self.now.setVisible(not strip and self._quest is not None and self.auto)
        self.setFixedWidth(420 if strip else 360)
        self.adjustSize()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        overlay_styles.add_menu(m, self.s, 'quest', self.apply_style)
        m.addSeparator()
        m.addAction('닫기', self.hide)
        m.exec(e.globalPos())

    def _copy_best(self):
        """스트립의 [네비]: 알림창 공식 목적지가 있으면 그것, 없으면 이 STEP 좌표."""
        if self.now_navi:
            QGuiApplication.clipboard().setText(self.now_navi)
        else:
            self.copy()

    def _steps_html(self):
        import html as _h
        steps, i = self.guide.steps, self.index
        rows = []
        for j in range(max(0, i - 1), min(len(steps), i + 3)):
            s = steps[j]
            mark = '✓' if j in self.done else str(s.order)
            if j == i:
                where = ' · '.join(x for x in (s.npc, s.map and self.map_name(s.map),
                                              s.x is not None and '%s,%s' % (s.x, s.y)) if x)
                rows.append('<div style="background:#1E2E44; padding:4px 6px;"><b style="color:#9ECBFF">%s</b>&nbsp; '
                            '<b>%s</b>%s</div>' % (mark, _h.escape(s.title),
                                                   ('<br><span style="color:#AAB2C0; font-size:12px">→ %s</span>'
                                                    % _h.escape(where)) if where else ''))
            else:
                rows.append('<div style="color:#8E97A8; padding:2px 6px;">%s&nbsp; %s</div>' % (mark, _h.escape(s.title)))
        return ''.join(rows)

    def _strip_html(self):
        import html as _h
        steps, i = self.guide.steps, self.index
        s = steps[i] if 0 <= i < len(steps) else None
        parts = ['<span style="color:#9ECBFF; font-weight:700">%d/%d</span>' % (i + 1, len(steps))]
        obj = next((o for o in (self._quest.objectives if self._quest else []) if o.need is not None), None)
        if obj:
            parts.append('%s <b style="color:#FFD479">%d/%d</b>' % (_h.escape(obj.text[:14]), obj.cur, obj.need))
        elif s:
            parts.append(_h.escape(s.title[:22]))
        if s and s.x is not None:
            parts.append('%s %s,%s' % (_h.escape(s.npc or ''), s.x, s.y))
        return ' · '.join(parts)

    @property
    def auto(self):
        return self.b_auto.isChecked()

    def _auto_toggled(self, on):
        self._save(auto_follow=bool(on))
        if not on:
            self.set_now(None)

    def set_now(self, quest, map_hint='', official=None):
        """알림창에서 읽은 지금 퀘스트를 강조. quest=None 이면 숨긴다.
        official: 게임 클라이언트 공식 퀘스트 정보(navigator.quest_info.QuestInfo) — 가야 할 NPC·좌표를 함께 보여 준다."""
        import html as _h
        if quest is None or not self.auto:
            self._quest = None
            self.now.hide()
            self.now_navi = ''
            if self.guide and self.style == 'strip':
                self.strip.setText(self._strip_html())
            self.adjustSize()
            return
        self._quest = quest
        rows = ['<b>▶ 지금 할 일</b>&nbsp; <span style="color:#BFEAD3">%s</span>' % _h.escape(quest.title)]
        for o in quest.objectives[:3]:
            if o.need is None:
                rows.append('· %s' % _h.escape(o.text))
            elif o.done:
                rows.append('<span style="color:#7FE0B0">✔ %s %d / %d — 완료, 다음 NPC에게</span>'
                            % (_h.escape(o.text), o.cur, o.need))
            else:
                rows.append('· %s <b style="color:#FFD479">%d / %d</b> <span style="color:#BFEAD3">(%d 남음)</span>'
                            % (_h.escape(o.text), o.cur, o.need, o.left))
        goal = official.goal if official else None
        self.now_navi = official.navi if official else ''
        if goal:
            rows.append('→ 가야 할 곳: <b>%s</b>%s %d,%d &nbsp;<a href="navi" style="color:#FFD479">[네비 복사]</a>'
                        % (_h.escape(goal[0] + ' · ' if goal[0] else ''), _h.escape(self.map_name(goal[1])),
                           goal[2], goal[3]))
        if map_hint:
            rows.append('<span style="color:#9ECBFF">%s</span>' % _h.escape(map_hint))
        if official:
            rows.append('<span style="color:#7F8898; font-size:11px">공식 퀘스트 정보(게임 데이터)</span>')
        self.now.setText('<br>'.join(rows))
        if self.guide and self.style == 'strip':
            self.strip.setText(self._strip_html())
        self._layout()

    def toggle_more(self):
        self.expanded = not self.expanded
        self._save(expanded=self.expanded)
        self._apply_more()

    def _apply_more(self):
        self.b_more.setText('접기 ▴' if self.expanded else '공략 보기 ▾')
        self._layout()

    @staticmethod
    def _body_html(s):
        """STEP 설명(① ② …)과 도움말. '스샷 좌표' 줄은 흐리게."""
        import html as _h
        rows = []
        for line in (s.description or '').split('\n'):
            if not line.strip():
                continue
            t = _h.escape(line)
            rows.append('<span style="color:#7F8898">%s</span>' % t if line.startswith('스샷 좌표') else t)
        if s.beginner_tip:
            rows.append('<span style="color:#7FE0B0">도움말: %s</span>' % _h.escape(s.beginner_tip))
        return '<br>'.join(rows)

    def show_step(self, guide, index, done=()):
        self.guide, self.index, self.done = guide, index, set(done)
        steps = guide.steps
        s = steps[index] if 0 <= index < len(steps) else None
        self.title.setText(guide.title)
        self.prog.setText('%d / %d%s' % (index + 1, len(steps), '  ✔' if index in self.done else ''))
        if s:
            self.cur.setText('STEP %d  %s' % (s.order, s.title))
            where = ' · '.join(x for x in (s.npc and 'NPC ' + s.npc, s.map and self.map_name(s.map),
                                          s.x is not None and '%s, %s' % (s.x, s.y)) if x)
            self.where.setText(where)
            self.where.setVisible(bool(where))
            self.navi.setText(s.navi_command)
            self.navi.setVisible(bool(s.navi_command))
            self.b_copy.setEnabled(bool(s.navi_command))
            self.b_copy.setToolTip(s.navi_command or '이 단계는 좌표 정보가 없습니다')
            self.body.setText(self._body_html(s))
        nxt = steps[index + 1] if index + 1 < len(steps) else None
        self.nxt.setText('다음: ' + (nxt.title if nxt else '마지막 단계'))
        self.b_prev.setEnabled(index > 0)
        self.b_next.setEnabled(index + 1 < len(steps))
        self.b_more.setVisible(bool(self.body.text()))
        if self.style == 'steps':
            self.steps_label.setText(self._steps_html())
        if self.style == 'strip':
            self.strip.setText(self._strip_html())
        self._apply_more()

    def _copy_now(self, _link=''):
        if self.now_navi:
            QGuiApplication.clipboard().setText(self.now_navi)

    def copy(self):
        if self.guide and 0 <= self.index < len(self.guide.steps):
            cmd = self.guide.steps[self.index].navi_command
            if cmd:
                QGuiApplication.clipboard().setText(cmd)
