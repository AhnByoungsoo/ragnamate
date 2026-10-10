"""RO Navigator: 검색 → Guide 상세(준비도·재료·시세) → STEP 진행 → /navi 클립보드 복사.

게임 클라이언트에는 아무것도 입력하지 않는다(클립보드 복사까지만).
"""
import datetime
import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from navigator.models import Q_COMPLETED, Q_ICON, Q_NOT_COMPLETED, Q_UNKNOWN, RESULT_ICON, STATUS
from navigator.player_state import PlayerState
from navigator.rewards import parse_rewards
from ui.widgets import Col, Table, comma, unit
from ui.completer import attach_completer, set_words

CATEGORIES = [('전체', None), ('던전', {'DUNGEON', 'INSTANCE'}), ('전직', {'JOB_CHANGE'}), ('에피소드', {'EPISODE'}),
              ('챕터', {'CHAPTER'}),
              ('퀘스트', {'QUEST', 'DAILY', 'WEEKLY'}), ('제작', {'CRAFT', 'EQUIPMENT'}), ('인챈트', {'ENCHANT'}),
              ('사냥터', {'HUNT', 'FARMING'}), ('NPC', {'NPC', 'MAP'})]
GID = Qt.UserRole + 5


def _item(text, gid):
    it = QListWidgetItem(text)
    it.setData(GID, gid)
    return it


class Section(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName('card')
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(12, 8, 12, 10)
        t = QLabel('<b>%s</b>' % title)
        self.v.addWidget(t)


class NavigatorPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.svc = app.guides
        self.gid = None
        self.detail = None
        self.step_index = 0
        v = QVBoxLayout(self)

        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText='무엇을 하고 싶으신가요?  예) 전사자의 무덤, 생던, 인퀴지터 전직, ㅇㅋㅁㅇㅈ')
        self.search.setObjectName('bigSearch')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self._timer.start(150))
        self.search.returnPressed.connect(self._open_first)
        self.search_completer = attach_completer(self.search)          # 공략 제목·별칭 — refresh_home 에서 채움
        top.addWidget(self.search, 1)
        self.mode = QComboBox()
        self.mode.addItem('초보자 모드', 'beginner')
        self.mode.addItem('숙련자 모드', 'expert')
        self.mode.setCurrentIndex(0 if app.s.get('navigator_mode', 'beginner') == 'beginner' else 1)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        top.addWidget(self.mode)
        b_home = QPushButton('홈')
        b_home.clicked.connect(self.go_home)
        top.addWidget(b_home)
        self.live_guide_button = QPushButton('현재 위치 가이드')
        self.live_guide_button.setEnabled(False)
        self.live_guide_button.setToolTip('LIVE에서 현재 맵이 확인되면 사용할 수 있습니다. '
                                          '(Replay 녹화 중이거나 맵 영역이 보정되어 있어야 합니다)')
        self.live_guide_button.clicked.connect(lambda: self.open_map(self.live_map))
        top.addWidget(self.live_guide_button)
        b_elem = QPushButton('사냥터 속성표')
        b_elem.setToolTip('맵마다 몬스터에게 평균적으로 강한 공격 속성 (공식 홈페이지 몬스터 정보 기준)')
        b_elem.clicked.connect(self.open_element_table)
        top.addWidget(b_elem)
        v.addLayout(top)
        self.live_context = {}
        self.page_note = QLabel('퀘스트·던전·전직의 입장 조건과 준비물, 이동 경로를 안내합니다.')
        self.page_note.setObjectName('cardTitle')
        v.addWidget(self.page_note)
        self.live_location = QLabel('현재 위치: LIVE 대기 중')
        self.live_location.setTextFormat(Qt.PlainText)
        v.addWidget(self.live_location)
        self._timer = QTimer(self, singleShot=True, timeout=self.run_search)

        cats = QHBoxLayout()
        self.cat_group = QButtonGroup(self)
        for i, (label, _) in enumerate(CATEGORIES):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName('chip')
            self.cat_group.addButton(b, i)
            cats.addWidget(b)
        self.cat_group.button(0).setChecked(True)
        self.cat_group.idClicked.connect(lambda _: self.run_search())
        cats.addStretch(1)
        v.addLayout(cats)

        sp = QSplitter(Qt.Horizontal)
        self.left = QStackedWidget()
        self.left.addWidget(self._build_home())
        self.results = QListWidget()
        self.results.itemClicked.connect(lambda it: self.open_guide(it.data(GID)))
        self.left.addWidget(self.results)
        sp.addWidget(self.left)
        self.detail_area = QScrollArea()
        self.detail_area.setWidgetResizable(True)
        self.detail_widget = QLabel('왼쪽에서 콘텐츠를 검색하거나 선택하세요.')
        self.detail_widget.setAlignment(Qt.AlignCenter)
        self.detail_area.setWidget(self.detail_widget)
        sp.addWidget(self.detail_area)
        sp.setSizes([380, 1000])
        v.addWidget(sp, 1)
        self.refresh_home()

    def apply_live_context(self, context):
        """LIVE에서 넘겨준 상황(맵·캐릭터·직업·레벨)을 화면에 반영한다."""
        self.live_context = dict(context or {})
        if context.get('map'):
            self.set_live_map(context['map'])
        who = ' · '.join(str(x) for x in (
            context.get('character'), self.app.display.job(context.get('job_id')).text
            if context.get('job_id') is not None else None,
            ('Lv.%s' % context['level']) if context.get('level') else None) if x)
        if who:
            self.live_location.setText(self.live_location.text() + '   (' + who + ')')
        return self.live_context

    def open_element_table(self):
        from ui.element_map_dialog import ElementMapDialog
        live = getattr(self, 'live_map', None)
        ElementMapDialog(self, self.app.display.map(live).text if live else '').exec()

    def set_live_map(self, map_id):
        self.live_map = map_id
        self.live_guide_button.setEnabled(True)
        self.live_guide_button.setToolTip('이 맵과 관련된 Guide를 찾습니다.')
        shown = self.app.display.map(map_id)
        text = '현재 위치: ' + shown.with_raw(bool(self.app.s.get('developer_mode')))
        if self.detail:
            guide = self.detail.get('guide') if isinstance(self.detail, dict) else None
            steps = getattr(guide, 'steps', []) if guide else []
            if self.step_index < len(steps) and getattr(steps[self.step_index], 'map', None) == map_id:
                text += ' · 목표 맵에 도착했습니다.'
        self.live_location.setText(text)

    # ------------------------------------------------------------------ 홈
    def _build_home(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel('<b>내 캐릭터</b>'))
        self.char_combo = QComboBox()
        self.char_combo.activated.connect(self._char_selected)
        v.addWidget(self.char_combo)
        self.char_info = QLabel()
        self.char_info.setWordWrap(True)
        v.addWidget(self.char_info)
        v.addWidget(QLabel('<b>다음 전직</b>'))
        self.next_list = QListWidget()
        self.next_list.setMaximumHeight(80)
        self.next_list.itemClicked.connect(lambda it: it.data(GID) and self.open_guide(it.data(GID)))
        v.addWidget(self.next_list)
        # 콘텐츠 찾기: 시작 레벨 구간 · 보상 아이템 · 내 조건만. 목록은 레벨 순(추천 순위 아님)
        head = QHBoxLayout()
        head.addWidget(QLabel('<b>콘텐츠 찾기</b>'))
        head.addStretch(1)
        self.content_count = QLabel()
        self.content_count.setObjectName('cardTitle')
        head.addWidget(self.content_count)
        v.addLayout(head)
        fb = QHBoxLayout()
        fb.addWidget(QLabel('레벨'))
        self.lv_from, self.lv_to = QSpinBox(), QSpinBox()
        for sb in (self.lv_from, self.lv_to):
            sb.setRange(0, 275)
            sb.setSpecialValueText('전체')          # 0 = 제한 없음
            sb.valueChanged.connect(self._refresh_content)
            fb.addWidget(sb)
            if sb is self.lv_from:
                fb.addWidget(QLabel('~'))
        self.only_mine = QCheckBox('내 조건만')
        self.only_mine.setToolTip('내 레벨·직업으로 시작할 수 있는 것만 (Replay를 불러온 뒤)')
        self.only_mine.setChecked(True)
        self.only_mine.toggled.connect(self._refresh_content)
        fb.addWidget(self.only_mine)
        v.addLayout(fb)
        self.item_q = QLineEdit(placeholderText='얻고 싶은 보상 아이템 (예: 포션, 카드, 상자)')
        self.item_q.setClearButtonEnabled(True)
        self.item_q.textChanged.connect(self._refresh_content)
        self.item_completer = attach_completer(self.item_q)            # 아이템 이름 — 아이템 DB가 늦게 읽혀 refresh_home 에서 채움
        v.addWidget(self.item_q)
        self.avail_list = QListWidget()
        self.avail_list.itemClicked.connect(lambda it: self.open_guide(it.data(GID)))
        v.addWidget(self.avail_list, 1)
        v.addWidget(QLabel('<b>★ 즐겨찾기</b>'))
        self.fav_list = QListWidget()
        self.fav_list.setMaximumHeight(90)
        self.fav_list.itemClicked.connect(lambda it: self.open_guide(it.data(GID)))
        v.addWidget(self.fav_list)
        v.addWidget(QLabel('<b>최근 본 Guide</b>'))
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(90)
        self.recent_list.itemClicked.connect(lambda it: self.open_guide(it.data(GID)))
        v.addWidget(self.recent_list)
        v.addWidget(QLabel('<b>오늘 할 일</b>'))
        self.today = QLabel('일일/주간 Guide 데이터가 아직 없습니다. (DAILY/WEEKLY 타입 Guide를 추가하면 여기에 표시)')
        self.today.setWordWrap(True)
        self.today.setObjectName('cardTitle')
        v.addWidget(self.today)
        return w

    def refresh_home(self):
        set_words(self.search_completer, [w for g in self.svc.repo.all() for w in [g.title] + list(g.aliases or [])])
        if not self.item_completer.model().stringList():
            items = getattr(getattr(self.app, 'item_db', None), 'items', None) or {}
            set_words(self.item_completer, [i.get('name') for i in items.values() if isinstance(i, dict)])
        st = self.svc.state
        chars = self.svc.characters()
        self.char_combo.blockSignals(True)
        self.char_combo.clear()
        if st.known:
            self.char_combo.addItem('현재 Replay: %s' % st.character_name, None)
        for c in chars:
            if c['character'] != st.character_name:
                self.char_combo.addItem('%s · %s Lv.%s' % (c['character'], c['job'], c['level']), c)
        self.char_combo.blockSignals(False)
        if st.known:
            where = ', '.join(st.sources_known()) or '없음'
            self.char_info.setText('%s · %s Lv.%s (Job %s△) · 맵 %s · Zeny %s<br><span style="color:gray">'
                                   '보유 아이템 확인 범위: %s · Replay %s</span>' % (
                                       html.escape(st.character_name), st.job_name or st.job_id, st.level or '?',
                                       st.job_level or '?', st.map or '?', comma(st.zeny), where, st.replay_time or ''))
        else:
            self.char_info.setText('Replay를 불러오면 직업/레벨/보유 아이템이 자동으로 채워집니다.')
        self.next_list.clear()
        for n in self.svc.next_jobs():
            g = n['guide']
            self.next_list.addItem(_item('→ %s%s' % (n['job']['name'], '' if g else '  (전직 Guide 데이터 없음)'),
                                         g.id if g else None))
        if st.job_id is not None and not self.next_list.count():
            self.next_list.addItem(_item('다음 전직 없음 (직업 트리 기준)', None))
        self._refresh_content()
        self._fill(self.fav_list, self.svc.repo.favorites(), '★ ')
        self._fill(self.recent_list, self.svc.repo.recent())

    @staticmethod
    def _prep_label(d, g):
        """Guide 맨 위 '준비물 · 보상' 요약. 준비물은 Guide 데이터(입장 조건·단계 재료)에 있는 것만 — 지어내지 않는다."""
        parts = []
        for m in d['materials']:
            if m['optional']:
                continue
            name = '%s ×%s' % (html.escape(str(m['name'])), comma(m['need']))
            if m['have'] is None:
                parts.append(name)
            elif m['missing']:
                parts.append('%s <span style="color:#F0B541">(보유 %s · %s개 부족)</span>'
                             % (name, comma(m['have']), comma(m['missing'])))
            else:
                parts.append('%s <span style="color:#43C08A">(보유 충분)</span>' % name)
        zeny = [r for r in g.requirements if r.kind == 'ZENY' and r.min]
        parts += ['%s Z' % comma(r.min) for r in zeny]
        prep = ' · '.join(parts) if parts else '<span style="color:#8E97A6">공식 안내에 적힌 준비물 없음</span>'
        tips = ''.join('<br>· %s' % html.escape(t) for t in g.strategy.get('prep', []))   # 준비 팁(진행 중 획득·이동 등)
        rw = parse_rewards(g).summary()
        lab = QLabel('<b>준비물</b>&nbsp;&nbsp;%s%s<br><b>보상</b>&nbsp;&nbsp;&nbsp;&nbsp;%s'
                     % (prep, tips, html.escape(rw) if rw else '<span style="color:#8E97A6">정보 없음</span>'))
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lab.setStyleSheet('QLabel { background: #1E2A3A; border: 1px solid #2E4461; border-radius: 8px; '
                          'padding: 8px 12px; }')
        return lab

    def _refresh_content(self, *_):
        """콘텐츠 목록: '[퀘스트] 도둑 퇴치1 · Lv.70+ — 보상 …' 형식, 시작 레벨 순."""
        st = self.svc.state
        only = self.only_mine.isChecked() and st.level is not None
        gs = self.svc.find_content(self.lv_from.value() or None, self.lv_to.value() or None,
                                   self.item_q.text().strip(), only_available=only)
        self.avail_list.clear()
        for g in gs[:800]:
            r = parse_rewards(g).summary()
            lv = 'Lv.%d+' % g.min_level if g.min_level else 'Lv.?'
            short = r if len(r) <= 60 else r[:58] + '…'
            it = _item('[%s] %s · %s%s' % (g.type_label, g.title, lv, ('  —  ' + short) if short else ''), g.id)
            it.setToolTip('%s\n시작 레벨: %s\n보상: %s' % (g.title, g.min_level or '정보 없음', r or '정보 없음'))
            self.avail_list.addItem(it)
        note = '' if st.level is not None or not self.only_mine.isChecked() else ' (Replay 전: 전체 표시)'
        self.content_count.setText('%d개%s' % (len(gs), note))

    def _fill(self, lst, ids, prefix=''):
        lst.clear()
        for gid in ids:
            g = self.svc.repo.get(gid)
            if g:
                lst.addItem(_item(prefix + g.title, gid))

    def _char_selected(self, i):
        c = self.char_combo.itemData(i)
        if c is None:
            self.app.apply_player_state()
        else:        # 기록에서 고른 캐릭터: 직업/레벨만 알고 보유 아이템은 모름
            self.svc.state = PlayerState(character_name=c['character'], job_id=c['job_id'], job_name=c['job'],
                                         level=c['level'], replay_file=c['file_path'], replay_time=c['start_time'])
        self.refresh_home()
        if self.gid:
            self.open_guide(self.gid, record=False)

    def go_home(self):
        self.search.clear()
        self.left.setCurrentIndex(0)
        self.refresh_home()

    # ------------------------------------------------------------------ 검색
    def run_search(self):
        q = self.search.text().strip()
        types = CATEGORIES[self.cat_group.checkedId()][1]
        if not q and types:                    # 검색어 없이 분류만 누르면 그 분류 전체 목록
            self.results.clear()
            for g in sorted((g for g in self.svc.repo.all() if g.type in types),
                            key=lambda g: (g.min_level or 0, bool(g.repeat_type), g.title)):   # 같은 레벨이면 메인 먼저
                lv = 'Lv.%d+' % g.min_level if g.min_level else 'Lv.?'
                self.results.addItem(_item('[%s] %s · %s' % (g.type_label, g.title, lv), g.id))
            self.left.setCurrentIndex(1)
            return
        if not q:
            self.left.setCurrentIndex(0)
            return
        res = self.svc.search(q, types, log_miss=len(q) >= 2)
        self.results.clear()
        for score, g, how in res:
            self.results.addItem(_item('[%s] %s   · %s %s' % (g.type_label, g.title, how,
                                                            '' if g.status == 'VERIFIED' else '(' + STATUS.get(g.status, g.status) + ')'),
                                       g.id))
        if not res:
            self.results.addItem(_item('"%s" 검색 결과 없음 — 검색어는 Guide 추가 후보로 기록됩니다.' % q, None))
        self.left.setCurrentIndex(1)

    def _open_first(self):
        if self.results.count() and self.results.item(0).data(GID):
            self.open_guide(self.results.item(0).data(GID))

    def open_map(self, map_id):
        """Replay 분석 화면에서 맵 → 관련 Guide."""
        gs = self.svc.map_guides(map_id)
        self.app.show_navigator()
        if len(gs) == 1:
            self.open_guide(gs[0].id)
        else:
            self.search.setText(map_id)
            self.results.clear()
            for g in gs:
                self.results.addItem(_item('[%s] %s' % (g.type_label, g.title), g.id))
            if not gs:
                self.results.addItem(_item('"%s" 관련 Guide 없음' % self.app.analysis.gamedata.maps.name(map_id), None))
            self.left.setCurrentIndex(1)

    # ------------------------------------------------------------------ 상세
    def open_guide(self, gid, record=True):
        if not gid:
            return
        d = self.svc.detail(gid)
        if not d:
            return
        self.gid, self.detail = gid, d
        if record:
            self.svc.repo.add_history(gid)
        self.step_index = d['progress'][0]
        self._render()
        self.app.update_overlay(self)

    def _render(self):
        d, g = self.detail, self.detail['guide']
        expert = self.mode.currentData() == 'expert'
        w = QWidget()
        v = QVBoxLayout(w)

        # 요약 카드
        card = Section('%s  <span style="color:gray">[%s]</span>' % (html.escape(g.title), g.type_label))
        card.v.addWidget(self._prep_label(d, g))
        rd = d['readiness']
        verified = g.verified_at[:10] if g.verified_at else '없음'
        grid = QGridLayout()
        info = [('입장/시작 레벨', g.min_level or '-'),
                ('선행 Guide', ', '.join(self.svc.repo.get(p).title for p in d['prerequisites'] if self.svc.repo.get(p)) or '-'),
                ('필요 재료', ('%d종 (위 준비물)' % sum(1 for m in d['materials'] if not m['optional']))
                 if any(not m['optional'] for m in d['materials']) else '-'),
                ('예상 준비비용', ('%s Z' % comma(d['prep_cost'])) + (' (+시세 없음 %d종)' % d['unpriced'] if d['unpriced'] else '')),
                ('현재 준비도', ('%s%%' % rd['percent']) if rd['percent'] is not None else '판단 불가'),
                ('확인 필요', '%d개 조건' % rd['unknown']),
                ('상태', '%s · %s' % (STATUS.get(g.status, g.status), g.confidence)),
                ('마지막 확인', verified)]
        for i, (k, val) in enumerate(info):
            grid.addWidget(QLabel('<span style="color:gray">%s</span>' % k), i // 4 * 2, i % 4)
            grid.addWidget(QLabel('<b>%s</b>' % html.escape(str(val))), i // 4 * 2 + 1, i % 4)
        card.v.addLayout(grid)
        bar = QHBoxLayout()
        first_navi = g.steps[0].navi_command if g.steps else ''
        b_start = QPushButton('▶ 시작하기 (1단계 네비 복사)' if first_navi else '▶ 시작하기')
        b_start.setToolTip('1단계부터 안내를 시작합니다.' if first_navi else
                           '이 Guide에는 좌표가 없어 네비 명령을 만들 수 없습니다. 단계 안내만 시작합니다.')
        b_start.setObjectName('primary')
        b_start.clicked.connect(self.start)
        b_start.setEnabled(bool(g.steps))
        b_fav = QPushButton('★ 즐겨찾기 해제' if g.id in self.svc.repo.favorites() else '☆ 즐겨찾기')
        b_fav.clicked.connect(self.toggle_fav)
        b_why = QPushButton('입장이 안 돼요')
        b_why.clicked.connect(self.why)
        b_ov = QPushButton('오버레이로 띄우기')
        b_ov.setToolTip('현재 단계를 게임 위에 작게 띄웁니다 — [공략 보기]로 펼치고 접을 수 있습니다.')
        b_ov.clicked.connect(lambda: self.app.toggle_overlay(self))
        for b in (b_start, b_fav, b_why, b_ov):
            bar.addWidget(b)
        if self.app.s.get('developer_mode'):
            b_edit = QPushButton('Guide 편집')
            b_edit.clicked.connect(lambda: self.app.edit_guide(g))
            bar.addWidget(b_edit)
        bar.addStretch(1)
        card.v.addLayout(bar)
        personal = [m for m in d['materials'] if m['missing'] and not m['optional'] and m['have'] is not None]
        if personal:
            tip = QLabel('<span style="color:#43C08A"><b>%s</b></span>' % ' / '.join(
                '%s %d개만 추가로 준비하면 됩니다' % (m['name'], m['missing']) for m in personal))
            tip.setWordWrap(True)          # 재료가 많으면 한 줄이 카드 폭을 밀어내던 문제
            card.v.addWidget(tip)
        if g.description and not expert:
            desc = QLabel(html.escape(g.description).replace('\n', '<br>'))
            desc.setWordWrap(True)
            card.v.addWidget(desc)
        v.addWidget(card)

        # 입장조건 / 준비상태
        sec = Section('입장 · 시작 조건과 내 준비 상태  (✅ 충족 / ❌ 미충족 / ? 확인 필요)')
        for r in d['results']:
            req = r.requirement
            lab = QLabel('%s <b>%s</b> — %s%s<br><span style="color:gray;font-size:11px">근거: %s</span>' % (
                RESULT_ICON[r.result], html.escape(req.description or req.kind), html.escape(r.message),
                ' (선택)' if req.optional else '', html.escape(req.source or '-')))
            lab.setWordWrap(True)
            sec.v.addWidget(lab)
        if not d['results']:
            sec.v.addWidget(QLabel('등록된 입장 조건 없음 (데이터 없음 — 검증 필요)'))
        st = self.svc.state
        if not st.known:
            sec.v.addWidget(QLabel('<span style="color:#d84315">Replay를 불러오면 내 레벨/직업/보유 아이템으로 자동 판정합니다.</span>'))
        v.addWidget(sec)

        # 재료
        if d['materials']:
            sec = Section('필요 재료 · 내 보유량(인벤토리+카트+창고) · 부족분 시세')
            tbl = Table([
                Col('아이템', lambda m: m['name'] + (' (선택)' if m['optional'] else ''), width=170),
                Col('필요', lambda m: m['need'], comma, width=60),
                Col('보유', lambda m: m['have'], comma, width=60),
                Col('보유 위치', lambda m: ', '.join('%s %d' % kv for kv in m['by_source'].items()) or '-', width=190),
                Col('부족', lambda m: m['missing'], comma, width=60),
                Col('최저가', lambda m: m['price'].lowest_price if m['price'] else None, comma, width=90),
                Col('중앙가', lambda m: m['price'].median_price if m['price'] else None, comma, width=90),
                Col('노점수', lambda m: m['price'].listing_count if m['price'] else None, comma, width=60),
                Col('예상비용', lambda m: m['cost'], comma, width=100),
                Col('근거', lambda m: 'Guide 입장조건' if m['entry'] else '공식 단계 텍스트 추출 ○')])
            tbl.set_rows(d['materials'])
            tbl.setMinimumHeight(min(60 + 24 * len(d['materials']), 260))
            tbl.doubleClicked.connect(lambda idx, t=tbl: self.material_info(t.row_at(idx)))
            sec.v.addWidget(tbl)
            sec.v.addWidget(QLabel('<span style="color:gray">행을 더블클릭: 어디서 얻나요? / 시세 상세 / 창고에서 보기 · '
                                   '보유량은 마지막 Replay 기준(%s)</span>' % (st.replay_time or '-')))
            v.addWidget(sec)

        # 진행 (Wizard)
        sec = Section('진행 순서 (%d단계)' % len(g.steps))
        done = d['progress'][1]
        self.step_list = QListWidget()
        for i, s in enumerate(g.steps):
            mark = '✔' if i in done else ('●' if i == self.step_index else '○')
            self.step_list.addItem('%s STEP %d  %s' % (mark, s.order, s.title))
        self.step_list.setMaximumHeight(min(26 * max(len(g.steps), 1) + 6, 220))
        self.step_list.currentRowChanged.connect(self._goto_step)
        sec.v.addWidget(self.step_list)
        self.step_box = QLabel()
        self.step_box.setWordWrap(True)
        self.step_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sec.v.addWidget(self.step_box)
        nav = QHBoxLayout()
        self.b_copy = QPushButton('📋 네비 복사')
        self.b_copy.setToolTip('이 단계의 /navi 명령을 클립보드에 복사합니다.')
        self.b_copy.clicked.connect(self.copy_navi)
        b_prev, b_done, b_next = QPushButton('◀ 이전'), QPushButton('✔ 완료'), QPushButton('다음 ▶')
        b_prev.clicked.connect(lambda: self._goto_step(self.step_index - 1))
        b_next.clicked.connect(lambda: self._goto_step(self.step_index + 1))
        b_done.clicked.connect(self.complete_step)
        for b in (self.b_copy, b_prev, b_done, b_next):
            nav.addWidget(b)
        nav.addStretch(1)
        sec.v.addLayout(nav)
        if not g.steps:
            sec.v.addWidget(QLabel('진행 단계 데이터 없음 — 검증 필요'))
        v.addWidget(sec)
        self._show_step()

        # 선행 관계 / 퀘스트 상태
        sec = Section('선행 관계 · 완료 상태')
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setMaximumHeight(140)

        def add(node, parent):
            it = QTreeWidgetItem([node['title'] + '   ' + Q_ICON[self.svc.repo.quest_state(st.character_name, node['id'])]])
            it.setData(0, GID, node['id'])
            (parent.addChild(it) if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem(it))
            for c in node['children']:
                add(c, it)
        add(d['relation'], tree)
        tree.expandAll()
        tree.itemDoubleClicked.connect(lambda it, _c: self.open_guide(it.data(0, GID)))
        sec.v.addWidget(tree)
        qs = QHBoxLayout()
        cur_state = self.svc.repo.quest_state(st.character_name, g.id)
        qs.addWidget(QLabel('이 콘텐츠 완료 상태: <b>%s</b>' % Q_ICON[cur_state]))
        for label, state in (('완료로 표시', Q_COMPLETED), ('미완료로 표시', Q_NOT_COMPLETED), ('모름', Q_UNKNOWN)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s_=state: self.mark_state(s_))
            qs.addWidget(b)
        qs.addStretch(1)
        sec.v.addLayout(qs)
        if d['cycle']:
            sec.v.addWidget(QLabel('⚠ 순환 선행관계: ' + d['cycle']))
        v.addWidget(sec)

        # 공략 / 보상 / 권장
        if g.strategy or g.rewards or g.recommended:
            sec = Section('공략 · 보상')
            parts = []
            for key, label in (('monsters', '몬스터'), ('bosses', '보스'), ('cautions', '주의사항'), ('drops', '주요 드랍')):
                if g.strategy.get(key):
                    parts.append('<b>%s</b><br>%s' % (label, '<br>'.join('· ' + html.escape(x) for x in g.strategy[key])))
            if g.rewards:
                parts.append('<b>보상</b><br>' + '<br>'.join('· ' + html.escape(r) for r in g.rewards))
            if g.recommended:
                parts.append('<b>권장 스펙(사용자 경험 기반 — 공식 입장조건 아님)</b><br>' +
                             '<br>'.join('· ' + html.escape(r) for r in g.recommended))
            lab = QLabel('<br><br>'.join(parts))
            lab.setWordWrap(True)
            sec.v.addWidget(lab)
            v.addWidget(sec)

        # 내 Replay 기록
        sec = Section('내 Replay 기록 (관련 맵: %s)' % (', '.join(g.related_maps) or '-'))
        recs = d['records']
        if recs:
            best = max((r for r in recs if r['combat_dps']), key=lambda r: r['combat_dps'], default=None)
            avg = lambda k: sum(r[k] for r in recs if r[k] is not None) / max(1, sum(1 for r in recs if r[k] is not None))
            sec.v.addWidget(QLabel('방문 %d회 · 최근 %s · 평균 Combat DPS %s · 평균 Kill %s · 평균 Profit/h %s · 최고 DPS %s' % (
                len(recs), (recs[0]['start_time'] or '')[:16].replace('T', ' '), unit(avg('combat_dps')),
                comma(avg('kills')), unit(avg('profit_per_hour')), unit(best['combat_dps']) if best else '-')))
        else:
            sec.v.addWidget(QLabel('이 콘텐츠 맵의 Replay 기록 없음'))
        v.addWidget(sec)

        # 출처
        sec = Section('출처 · 검증 정보')
        src = '<br>'.join('%s %s%s · 확인 %s%s' % (
            s.confidence, html.escape(s.title), (' — <a href="%s">%s</a>' % (s.url, s.url)) if s.url else '',
            s.checked_at or '-', (' · 게시 %s' % s.published_at) if s.published_at else '') for s in g.sources) or '출처 없음'
        lab = QLabel(src + ('<br><br><span style="color:gray">%s</span>' % html.escape(g.notes).replace('\n', '<br>')
                            if g.notes else ''))
        lab.setWordWrap(True)
        lab.setOpenExternalLinks(True)
        sec.v.addWidget(lab)
        v.addWidget(sec)
        v.addStretch(1)
        self.detail_area.setWidget(w)

    # ------------------------------------------------------------------ STEP
    def _show_step(self):
        g = self.detail['guide']
        if not g.steps:
            self.step_box.setText('')
            self.b_copy.setEnabled(False)
            self.b_copy.setToolTip('이 단계에는 맵·좌표 정보가 없습니다. 게임에서 확인해 Guide Editor로 채울 수 있습니다.')
            return
        self.step_index = max(0, min(self.step_index, len(g.steps) - 1))
        s = g.steps[self.step_index]
        expert = self.mode.currentData() == 'expert'
        rows = ['<b>STEP %d / %d — %s</b>' % (s.order, len(g.steps), html.escape(s.title))]
        loc = ' · '.join(x for x in (s.npc and 'NPC <b>%s</b>' % html.escape(s.npc),
                                      s.map and '맵 <b>%s</b>' % html.escape(self.app.analysis.gamedata.maps.name(s.map)),
                                      s.x is not None and '좌표 <b>%s, %s</b>' % (s.x, s.y)) if x)
        if loc:
            rows.append(loc)
        rows.append('<span style="color:#b8860b">%s</span>' % (s.navi_command or '(이 단계는 좌표 정보가 없어 /navi를 만들 수 없음)'))
        if not expert:
            rows.append(html.escape(s.description).replace('\n', '<br>'))
            if s.beginner_tip:
                rows.append('<span style="color:gray">도움말: %s</span>' % html.escape(s.beginner_tip))
            if s.navi_command:
                rows.append('<span style="color:gray">[네비 복사]를 누른 뒤 게임 채팅창에 Ctrl+V → Enter 하면 길 안내가 표시됩니다.</span>')
        self.step_box.setText('<br>'.join(rows))
        self.b_copy.setEnabled(bool(s.navi_command))
        self.b_copy.setToolTip('이 단계의 /navi 명령을 클립보드에 복사합니다.' if s.navi_command else
                               '이 단계에는 맵·좌표 정보가 없습니다. 게임에서 확인해 Guide Editor로 채울 수 있습니다.')
        self.step_list.blockSignals(True)
        self.step_list.setCurrentRow(self.step_index)
        self.step_list.blockSignals(False)

    def _goto_step(self, i):
        g = self.detail['guide']
        if 0 <= i < len(g.steps):
            self.step_index = i
            done = self.detail['progress'][1]
            self.svc.set_step(self.gid, i, done)
            self.detail['progress'] = (i, done)
            self._refresh_marks()
            self._show_step()
            self.app.update_overlay(self)

    def _refresh_marks(self):
        done = self.detail['progress'][1]
        for i, s in enumerate(self.detail['guide'].steps):
            mark = '✔' if i in done else ('●' if i == self.step_index else '○')
            self.step_list.item(i).setText('%s STEP %d  %s' % (mark, s.order, s.title))

    def complete_step(self, index=None):
        i = self.step_index if index is None or index is False else index
        done = set(self.detail['progress'][1]) | {i}
        self.detail['progress'] = (i, done)
        self.svc.set_step(self.gid, i, done)
        nxt = i + 1 if i + 1 < len(self.detail['guide'].steps) else i
        self._goto_step(nxt)
        self._refresh_marks()

    def start(self):
        self._goto_step(0)
        self.copy_navi()

    def copy_navi(self):
        g = self.detail['guide'] if self.detail else None
        if g and g.steps:
            cmd = g.steps[self.step_index].navi_command
            if cmd:
                QGuiApplication.clipboard().setText(cmd)
                self.app.status('네비게이션 명령을 복사했습니다 — 게임 채팅창에 Ctrl+V → Enter  (%s)' % cmd)
            else:
                self.app.status('이 단계는 좌표 정보가 없습니다.')

    def _mode_changed(self):
        self.app.s['navigator_mode'] = self.mode.currentData()
        self.app._save_settings()
        if self.detail:
            self._render()

    # ------------------------------------------------------------------ 기타 동작
    def toggle_fav(self):
        self.svc.repo.toggle_favorite(self.gid)
        self._render()
        self.refresh_home()

    def mark_state(self, state):
        self.svc.repo.set_quest_state(self.svc.state.character_name, self.gid, state)
        self.open_guide(self.gid, record=False)

    def why(self):
        r = self.svc.why_cant_enter(self.gid)
        dlg = QDialog(self)
        dlg.setWindowTitle('왜 못 들어가지? — %s' % self.detail['guide'].title)
        v = QVBoxLayout(dlg)
        lines = ['%s %s — %s' % (RESULT_ICON[x.result], html.escape(x.requirement.description or x.requirement.kind),
                                  html.escape(x.message)) for x in r['results']]
        tb = QTextBrowser()
        tb.setHtml('<b>조건 확인</b><br>' + '<br>'.join(lines or ['등록된 조건 없음']) +
                   '<br><br><b>결론</b><br>' + '<br>'.join(html.escape(x) for x in r['diagnosis']))
        v.addWidget(tb)
        for gid in r['fix_guides']:
            g = self.svc.repo.get(gid)
            row = QHBoxLayout()
            b = QPushButton('%s 가이드' % g.title)
            b.clicked.connect(lambda _=False, x=gid: (dlg.accept(), self.open_guide(x)))
            row.addWidget(b)
            first = next((s for s in g.steps if s.navi_command), None)
            if first:
                b2 = QPushButton('시작 NPC 네비 복사')
                b2.clicked.connect(lambda _=False, c=first.navi_command: (QGuiApplication.clipboard().setText(c),
                                                                           self.app.status('복사: ' + c)))
                row.addWidget(b2)
            v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.resize(560, 420)
        dlg.exec()

    def material_info(self, m):
        p = m['price']
        where = '<br>'.join('· ' + html.escape(x) for x in m['where']) or '데이터 없음 (몬스터 드랍/NPC 교환/제작 정보 미등록)'
        price = ('최저 %s · 중앙 %s · 평균 %s · 판매 노점 %s · 기준시각 %s' % (
            comma(p.lowest_price), comma(p.median_price), comma(p.average_price), p.listing_count,
            datetime.datetime.fromtimestamp(p.updated_at).strftime('%m-%d %H:%M') if p.updated_at else '-')
                 if p and p.has_price else '시세 없음 (시세 DB에 등록된 가격 없음)')
        box = QMessageBox(self)
        box.setWindowTitle(m['name'])
        box.setText('<b>%s</b> (ID %s)<br><br><b>보유</b>: %s<br><b>시세</b>: %s<br><br><b>어디서 얻나요?</b><br>%s' % (
            html.escape(m['name']), m['item_id'],
            ', '.join('%s %d' % kv for kv in m['by_source'].items()) or '없음', price, where))
        b_st = box.addButton('창고 분석에서 보기', QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is b_st:
            self.app.show_item_in_storage(m['item_id'])
