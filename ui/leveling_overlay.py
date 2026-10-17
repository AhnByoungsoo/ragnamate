"""레벨업 코치 오버레이: 지금 레벨에 맞는 사냥터·할 일, 대안, 다음 코스를 보여 준다.

게임에 입력을 보내지 않는다. [네비 복사]는 /navi 명령을 클립보드에 넣을 뿐이고, 붙여넣기는 사용자가 한다.
좌표 자료가 없는 단계는 네비를 만들지 않는다 — 그 사냥터에 서 있을 때 [여기를 네비로 저장]으로 직접 채울 수 있다.
"""
import html

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from leveling.course import CHECK_LABEL, KIND_LABEL, Courses, current_level
from ui.overlay import OverlayWindow


def _range(s):
    return 'Lv.%d~%d' % (s.min, s.max)


class LevelingOverlay(OverlayWindow):
    screen_update = Signal(object)                 # 화면 레벨 감시 스레드 → 화면: dict(hwnd, level, status)

    def __init__(self, settings, save, notify=lambda msg: None, alert=None):
        """save(): 설정 저장 · notify(글): 상태 알림 · alert(글, 음성 글): 소리·음성까지 내는 알림."""
        super().__init__(settings, 'leveling', '레벨업 코치')
        self.save_settings, self.notify = save, notify
        self.alert = alert or (lambda text, speak: notify(text))
        self.rp, self.primary_id, self.level = {}, None, None
        self.screen, self.screen_status = None, ''
        self.screen_update.connect(self.on_screen)
        self.setStyleSheet(
            'QWidget#lvRoot { background: #14171D; border: 1px solid #35598C; border-radius: 10px; }'
            'QLabel { color: #E6E9EF; background: transparent; font-size: 12px; }'
            'QLabel#t { color: #9ECBFF; font-weight: 700; font-size: 13px; }'
            'QLabel#lv { color: #FFD479; font-weight: 700; font-size: 13px; }'
            'QLabel#now { background: #1D3A2C; border-left: 3px solid #4CC38A; border-radius: 6px; padding: 6px 8px;'
            ' color: #FFFFFF; font-size: 13px; }'
            'QLabel#sub { color: #AAB2C0; } QLabel#src { color: #6F7888; font-size: 11px; }'
            'QPushButton { background: #1F242D; color: #E6E9EF; border: 1px solid #343B48; border-radius: 6px;'
            ' padding: 3px 8px; font-size: 12px; } QPushButton:hover { border-color: #4B84C8; }'
            'QPushButton:disabled { color: #5A6170; } QPushButton#x { border: 0; background: transparent; color: #8E97A8; }'
            'QComboBox { background: #1F242D; color: #E6E9EF; border: 1px solid #343B48; border-radius: 6px;'
            ' padding: 2px 6px; font-size: 12px; }')
        root = QWidget(self)
        root.setObjectName('lvRoot')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(5)
        head = QHBoxLayout()
        title = QLabel('레벨업 코치')
        title.setObjectName('t')
        self.route = QComboBox()
        self.courses = Courses(user_navi=self.s.get('leveling_navi') or {})
        for key, r in self.courses.routes.items():
            self.route.addItem(r['title'], key)
        self.route.setCurrentIndex(max(0, self.route.findData(self.s.get('leveling_route'))))
        self.route.setToolTip(self.courses.routes[self.route.currentData()]['summary'])
        self.route.currentIndexChanged.connect(self._route_changed)
        b_close = QPushButton('✕')
        b_close.setObjectName('x')
        b_close.clicked.connect(self.hide)
        head.addWidget(title)
        head.addWidget(self.route, 1)
        head.addWidget(b_close)
        v.addLayout(head)
        lv = QHBoxLayout()
        self.lv = QLabel()
        self.lv.setObjectName('lv')
        b_level = QPushButton('레벨 수정')
        b_level.setToolTip('화면·리플레이에서 레벨을 읽지 못할 때 쓸 레벨입니다. 자동으로 읽히면 그 값을 씁니다.')
        b_level.clicked.connect(self.ask_level)
        lv.addWidget(self.lv, 1)
        lv.addWidget(b_level)
        v.addLayout(lv)
        self.now = QLabel()
        self.now.setObjectName('now')
        self.now.setWordWrap(True)
        self.now.setTextFormat(Qt.RichText)
        v.addWidget(self.now)
        row = QHBoxLayout()
        self.b_navi, self.b_video, self.b_here = (QPushButton(t) for t in ('네비 복사', '영상 근거', '여기를 네비로 저장'))
        self.b_navi.clicked.connect(lambda: self.copy_navi(self._primary()))
        self.b_video.clicked.connect(lambda: self.open_video(self._primary()))
        self.b_here.clicked.connect(self.save_here)
        self.b_here.setToolTip('지금 서 있는 맵·좌표(리플레이에서 읽음)를 이 단계의 네비로 저장합니다. 이 PC에만 저장됩니다.')
        for b in (self.b_navi, self.b_video, self.b_here):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.more = QLabel()                       # 대안 · 다음 — 링크로 영상/네비
        self.more.setObjectName('sub')
        self.more.setWordWrap(True)
        self.more.setTextFormat(Qt.RichText)
        self.more.linkActivated.connect(self._link)
        v.addWidget(self.more)
        self.src = QLabel()
        self.src.setObjectName('src')
        self.src.setWordWrap(True)
        v.addWidget(self.src)
        self.setFixedWidth(380)
        self.advice = None
        self.refresh()

    # ------------------------------------------------------------------ 상태
    def _key(self):
        return self.route.currentData()

    def _primary(self):
        return self.advice.now[0] if self.advice and self.advice.now else None

    def _step(self, step_id):
        return next((s for s in self.courses.routes[self._key()]['steps'] if s.id == step_id), None)

    def _route_changed(self, _i):
        self.s['leveling_route'] = self._key()
        self.route.setToolTip(self.courses.routes[self._key()]['summary'])
        self.save_settings()
        self.primary_id = None                      # 코스를 바꾼 것은 알릴 일이 아니다
        self.refresh()

    def update_from(self, rp):
        self.rp = rp or {}
        self.refresh()

    def on_screen(self, d):
        self.screen, self.screen_status = d.get('level'), d.get('status', '')
        self.refresh()

    def ask_level(self):
        value, ok = QInputDialog.getInt(self, '레벨 수정', '자동으로 읽지 못할 때 쓸 Base 레벨 (0 = 쓰지 않음)',
                                        int(self.s.get('leveling_manual_level') or 0), 0, 999)
        if ok:
            self.s['leveling_manual_level'] = value
            self.save_settings()
            self.refresh()

    # ------------------------------------------------------------------ 화면
    def refresh(self):
        level, why = current_level(self.rp, self.s.get('leveling_manual_level') or 0, self.screen)
        if why != getattr(self, '_why', why):          # 레벨을 읽는 곳이 바뀐 것(직접 입력 → 화면 등)은 알릴 일이 아니다
            self.primary_id, self.level = None, None
        self._why = why
        self._level_jump(level)
        self.advice = a = self.courses.advise(self._key(), level)
        hint = ' · %s' % self.screen_status if self.screen_status and self.screen_status != '화면 인식' else ''
        self.lv.setText(('Lv.%d · %s' % (level, why) if level else '레벨 모름 — 게임 기본정보 창을 펼쳐 두세요') + hint)
        p = self._primary()
        if level is None:
            self.now.setText('레벨을 알면 맞는 사냥터를 보여 줍니다.')
        elif a.out_of_range:
            self.now.setText('이 코스 자료는 Lv.%d까지입니다. 지금 레벨은 자료 범위 밖입니다.'
                             % self.courses.routes[self._key()]['max_level'])
        elif p is None:
            self.now.setText('이 레벨 구간은 코스 자료가 없습니다. 아래 다음 자료나 영상을 확인하세요.')
        else:
            lines = ['<b>[%s] %s</b>  <span style="color:#AAB2C0">%s</span>'
                     % (KIND_LABEL.get(p.kind, p.kind), html.escape(p.title), _range(p))]
            for extra in (p.party and '파티: ' + p.party, p.note, p.level_note):
                if extra:
                    lines.append('<span style="color:#C9D1DC">· %s</span>' % html.escape(extra))
            self.now.setText('<br>'.join(lines))
        self.b_navi.setEnabled(bool(p and p.navi))
        self.b_navi.setToolTip(('%s 복사 — 게임 채팅창에 붙여 넣으세요.%s' % (p.navi_text, ' (%s)' % p.navi['note']
                                if p.navi.get('note') else '')) if p and p.navi else '이 단계는 좌표 자료가 없습니다.')
        self.b_video.setEnabled(bool(p))
        here = self.rp.get('map') and '@' not in str(self.rp.get('map')) and self.rp.get('position')
        self.b_here.setEnabled(bool(p and here))
        parts = []
        for s in (a.now[1:3] if a.now else []):
            parts.append('대안: [%s] %s %s · %s' % (KIND_LABEL.get(s.kind, s.kind), html.escape(s.title), _range(s),
                                                  self._links(s)))
        if len(a.now) > 3:
            parts.append('그 밖에 %d개 — 영상에서 확인' % (len(a.now) - 3))
        if a.next:
            parts.append('다음: %s %s · %s' % (_range(a.next), html.escape(a.next.title), self._links(a.next)))
        self.more.setText('<br>'.join(parts))
        self.more.setVisible(bool(parts))
        self.src.setText('근거: %s · %s (%s) · %s' % (p.src['author'], p.src['title'], p.src['published'],
                                                    CHECK_LABEL.get(p.check, p.check)) if p else '')
        self._announce(p)
        self.adjustSize()

    @staticmethod
    def _links(s):
        out = ['<a href="video:%s" style="color:#8FC2FF">영상</a>' % s.id]
        if s.navi:
            out.append('<a href="navi:%s" style="color:#FFD479">네비</a>' % s.id)
        return ' '.join(out)

    def _announce(self, p):
        """레벨이 올라 '지금 할 일'이 바뀌면 한 번 알린다(처음 열 때·코스를 바꿀 때는 조용히).
        레벨업 티켓을 쓸 차례면 음성으로 알린다."""
        pid = p.id if p else None
        if self.primary_id is not None and pid and pid != self.primary_id:
            if p.kind == 'TICKET':
                self.alert('레벨업 티켓 사용할 차례: %s (%s)' % (p.title, _range(p)), '레벨업 티켓을 사용하세요')
            else:
                self.alert('레벨업 코치: 다음 코스 — %s' % p.title, '다음 사냥터로 이동하세요')
        self.primary_id = pid or self.primary_id

    def _level_jump(self, level):
        """레벨이 한 번에 2 이상 오르면 레벨업 티켓을 쓴 것으로 보고 알린다(사냥으로는 한 번에 1씩 오른다)."""
        if level and self.level and level - self.level >= 2:
            self.notify('레벨업 티켓 사용 확인: Lv.%d → Lv.%d' % (self.level, level))
        if level:
            self.level = level

    # ------------------------------------------------------------------ 버튼
    def _link(self, href):
        kind, _, step_id = href.partition(':')
        step = self._step(step_id)
        if step is None:
            return
        (self.copy_navi if kind == 'navi' else self.open_video)(step)

    def copy_navi(self, step):
        if step and step.navi:
            QGuiApplication.clipboard().setText(step.navi_text)
            self.notify('네비 복사: %s — 게임 채팅창에 붙여 넣으세요.' % step.navi_text)

    def open_video(self, step):
        if step:
            QDesktopServices.openUrl(QUrl(step.video_url))

    def save_here(self):
        p = self._primary()
        mp, pos = self.rp.get('map'), self.rp.get('position')
        if not (p and mp and pos) or '@' in str(mp):
            return
        navi = dict(self.s.get('leveling_navi') or {})
        navi[p.id] = dict(map=mp, x=int(pos[0]), y=int(pos[1]), label='직접 저장', note='직접 저장한 위치')
        self.s['leveling_navi'] = navi
        self.save_settings()
        self.courses = Courses(user_navi=navi)
        self.notify('네비 저장: %s → /navi %s %d/%d' % (p.title, mp, pos[0], pos[1]))
        self.refresh()
