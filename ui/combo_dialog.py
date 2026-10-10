"""리플레이로 연격 콤보 매크로 만들기: 자주 쓴 스킬 순서 + 실제로 잰 간격 + 단축키 창의 키."""
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core import combo as C
from core import macros as M
from ui.key_capture import KeyCapture
from ui.theme import CARD_LIST_QSS
from ui.icons import IconResolver
from core.display import DisplayNames


def _item(text):
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemIsEnabled)
    return it


QUANTILES = [('auto', '자동 최적화 초안 (추천)'), (0.1, '실측 빠른 10%'), (0.2, '실측 빠른 20%'), (0.5, '실측 중앙값 (여유)')]


class ComboDialog(QDialog):
    def __init__(self, app, page):
        super().__init__(page)
        self.app, self.page = app, page
        self.setWindowTitle('리플레이로 연격 콤보 만들기')
        self.resize(1080, 640)
        self.rows = []
        v = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        res = getattr(app.replay_page, 'result', None)
        if res is None:
            self.info.setText('리플레이 분석 탭에서 먼저 리플레이를 불러오세요. 불러온 리플레이의 스킬 사용 기록으로 콤보를 만듭니다.')
            return
        pr = self.parsed = res.parsed
        self.name_of = lambda sid: app.analysis.gamedata.skills.name(sid)
        self.icons = getattr(app, 'icons', None) or IconResolver()
        self.display_names = DisplayNames(app.analysis.gamedata)
        self.prof = C.character_profile(pr)
        self.casts, self.stats = self.prof['casts'], self.prof['stats']
        bar = C.hotkey_bar(pr.packets)
        self.character = pr.character or '?'
        self.key_scope = '%s|%s|%s' % (pr.server, self.character, pr.job_id)
        all_keys = app.s.get('macro_skill_keys') or {}
        if not all(isinstance(v, dict) for v in all_keys.values()):     # 예전 형식(캐릭터 구분 없음)은 버린다
            all_keys = {}
        saved = {int(k): v for k, v in (all_keys.get(self.key_scope) or all_keys.get(self.character) or {}).items()}
        self.confirmed_keys = dict(saved)
        if bar:
            self.keys = C.merge_keys(saved, bar)
            bar_text = '단축키 창을 읽어 키를 채웠습니다 (1줄 F1~F9 · 2줄 Q~O · 3줄 A~L · 4줄 Z~. — 게임 키 설정이 다르면 표에서 바꾸세요)'
        else:
            self.keys = saved
            bar_text = ('이 리플레이에는 단축키 창 정보가 없어 %s' %
                        ('%s 캐릭터의 이전 키를 씁니다 — 없는 스킬은 키 칸을 눌러 지정하세요' % self.character if saved
                         else '키 칸을 클릭하고 키를 눌러 지정하세요'))
        pf = self.prof
        me = ('ASPD %g (관측 중앙값 · 모션 %dms / 참고 간격 %dms)' % (pf['aspd'], pf['amotion'], pf['motion_ms'])
              if pf['amotion'] else '공격속도 정보 없음')
        def top(d, limit=4, skip_long=False):
            items = [(sid, ms) for sid, ms in d.items() if ms and not (skip_long and ms >= 10000)]
            return ', '.join('%s %dms' % (self.name_of(sid).split('(')[0], ms)
                             for sid, ms in sorted(items, key=lambda kv: -kv[1])[:limit]) or '없음'
        self.info.setText('%s · %s · 스킬 시작 %d회 기록\n내 캐릭터(녹화 당시 값·추정 포함): %s\n'
                          '후딜레이: %s · 쿨타임: %s\n%s' % (
                              os.path.basename(res.path), '%s · %s · Lv.%s / Job Lv.%s (직업 ID %s)' % (pr.server, self.character, pr.base_level, pr.job_level, pr.job_id), len(self.casts), me,
                              top(pf['postdelay']), top(pf['cooldown'], skip_long=True), bar_text))

        sp = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        t = QLabel('자주 쓴 스킬 순서 (콤보가 끊기지 않은 구간에서 센 횟수)')
        t.setObjectName('cardTitle')
        lv.addWidget(t)
        self.cands = QListWidget()
        self.cands.setIconSize(QSize(28, 28))
        self.cands.setStyleSheet(CARD_LIST_QSS)
        self.cands.currentRowChanged.connect(self._pick)
        lv.addWidget(self.cands, 1)
        sp.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        opts = QGridLayout()
        self.q = QComboBox()
        for q, label in QUANTILES:
            self.q.addItem(label, q)
        self.q.currentIndexChanged.connect(lambda _: self._fill())
        self.extra = QSpinBox(minimum=0, maximum=1000, singleStep=10, suffix=' ms', value=10)
        self.extra.valueChanged.connect(lambda _: self._fill())
        opts.addWidget(QLabel('간격 기준'), 0, 0)
        opts.addWidget(self.q, 0, 1)
        opts.addWidget(QLabel('여유 더하기'), 0, 2)
        opts.addWidget(self.extra, 0, 3)
        rv.addLayout(opts)
        self.table = QTableWidget(0, 4)
        self.table.setIconSize(QSize(24, 24))
        self.table.setHorizontalHeaderLabels(['스킬', '키', '다음 스킬까지 대기', '근거'])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 150)
        self.table.verticalHeader().setVisible(False)
        rv.addWidget(self.table, 1)
        note = QLabel('자동 최적화 초안: 실측 하위 20%와 캐스팅·후딜레이·모션 참고값 중 긴 간격에 여유를 더하고, 재사용 대기를 반영합니다. '
                      '후딜레이·캐스팅·쿨타임은 녹화 중 최대 관측값입니다. 모션×2는 추정이며 게임의 최소 간격이나 시전 성공을 보장하지 않습니다. '
                      '장비·버프가 달라지면 새 리플레이로 갱신하세요. 자료 없는 간격은 직접 입력해야 합니다.')
        note.setWordWrap(True)
        note.setObjectName('cardTitle')
        rv.addWidget(note)
        trig = QHBoxLayout()
        self.name = QLineEdit()
        self.mod = QComboBox()
        for k, label in M.MODIFIERS.items():
            self.mod.addItem(label, k)
        self.trigger = KeyCapture()
        self.trigger.captured.connect(self._trigger_captured)
        self.mode = QComboBox()
        for k, label in M.MODES.items():
            self.mode.addItem(label, k)
        for label, w in (('이름', self.name), ('트리거', self.mod), ('', self.trigger), ('', self.mode)):
            if label:
                trig.addWidget(QLabel(label))
            trig.addWidget(w, 2 if w is self.name else 0)
        rv.addLayout(trig)
        self.keys_confirmed = QCheckBox('표의 스킬 키가 게임 설정과 일치함 (슬롯 배치는 추정)')
        rv.addWidget(self.keys_confirmed)
        b_add = QPushButton('자동 계산 결과 등록 / 갱신')
        b_add.setProperty('primary', True)
        b_add.clicked.connect(self._add)
        rv.addWidget(b_add, 0, Qt.AlignRight)
        sp.addWidget(right)
        sp.setSizes([380, 700])
        v.addWidget(sp, 1)

        self.suggestions = C.suggest(self.casts)
        for s in self.suggestions:
            it = QListWidgetItem('%s\n%d회' % (self._seq_text(s['skills']), s['count']))
            it.setIcon(self.icons.icon('skill_%s' % s['skills'][0]))
            it.setToolTip('\n\n'.join(self.display_names.skill(x).tooltip() for x in s['skills']))
            self.cands.addItem(it)
        if not self.suggestions:
            self.cands.addItem('이 리플레이에서 이어서 쓴 스킬 순서를 찾지 못했습니다')
        else:
            self.cands.setCurrentRow(0)

    def _seq_text(self, skills):
        out, i = [], 0
        while i < len(skills):                                    # 같은 스킬 연속은 ×N 으로 줄여서 표시
            j = i
            while j + 1 < len(skills) and skills[j + 1] == skills[i]:
                j += 1
            name = self.name_of(skills[i]).split('(')[0].replace(' : ', ':')    # 제1격 : 낙인 → 제1격:낙인
            out.append(name + (' ×%d' % (j - i + 1) if j > i else ''))
            i = j + 1
        return ' → '.join(out)

    def _pick(self, i):
        if not 0 <= i < len(self.suggestions):
            return
        self.sequence = self.suggestions[i]['skills']
        self.name.setText(self._seq_text(self.sequence)[:40])
        first = self.keys.get(self.sequence[0])
        self.trigger.setCurrentText(first or 'F12')
        self.mode.setCurrentIndex(self.mode.findData('hold'))
        self.keys_confirmed.setChecked(all(s in self.confirmed_keys for s in self.sequence))
        self._fill()

    def _fill(self):
        if not getattr(self, 'sequence', None):
            return
        auto = self.q.currentData() == 'auto'
        self.rows = (C.plan_auto(self.sequence, self.stats, self.prof, margin_ms=self.extra.value()) if auto else
                     C.plan(self.sequence, self.stats, q=self.q.currentData(), extra_ms=self.extra.value()))
        for r in range(self.table.rowCount()):                  # 이전 후보의 칸 위젯을 확실히 지운다
            for c in range(self.table.columnCount()):
                w = self.table.cellWidget(r, c)
                if w is not None:
                    self.table.removeCellWidget(r, c)
                    w.hide()                                      # 실제 삭제는 나중이라 먼저 숨긴다
                    w.deleteLater()
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            skill_item = _item(self.name_of(row['skill']))
            skill_item.setIcon(self.icons.icon('skill_%s' % row['skill']))
            skill_item.setToolTip(self.display_names.skill(row['skill']).tooltip())
            self.table.setItem(r, 0, skill_item)
            key = KeyCapture(self.keys.get(row['skill'], ''))
            key.keyChanged.connect(lambda text, sid=row['skill']: self._key_changed(sid, text))
            self.table.setCellWidget(r, 1, key)
            wait = QSpinBox(minimum=C.MIN_WAIT_MS, maximum=60000, singleStep=10, suffix=' ms')
            wait.setValue(row['wait'] if row['wait'] is not None else C.MIN_WAIT_MS)
            row['manual'] = False
            wait.valueChanged.connect(lambda value, row=row: row.update(wait=value, manual=True))
            self.table.setCellWidget(r, 2, wait)
            real = ('실측 %d/%d' % (row['fast'], row['median'])) if row['samples'] else '실측 없음'
            real_long = ('실측(응답 받은 간격) 빠른 20%% %d ms · 중앙값 %d ms · %d회' % (
                row['fast'], row['median'], row['samples']) if row['samples'] else '실측 없음')
            if row['wait'] is None:
                meas = long = '자료 없음 — 대기 직접 입력 필요 (등록 전 확인)'
            elif row['wait'] > 60000:
                meas = long = '계산값 %dms — 60000ms 초과, 등록 불가' % row['wait']
            elif auto:
                parts = ['캐스팅 %d' % row['cast'] if row.get('cast') is not None else '캐스팅 미관측',
                         '후딜 %d' % row['acd'] if row['acd'] is not None else '후딜 -',
                         '모션 %d' % row['motion'] if row['motion'] else '모션 -']
                cd_for = ', '.join('%s %dms' % (self.name_of(s).split('(')[0],
                                                self.prof['cooldown'].get(s, 0)) for s in row.get('cooldown_for', []))
                meas = '%s → %s | %s' % (' · '.join(p for p in parts if p), row['basis'], real)
                long = ('캐스팅 %s · 서버 후딜레이 %s · 공격 모션×2 추정 %s → %s 기준 + 여유 %d ms%s\n%s' % (
                    '%d ms' % row['cast'] if row.get('cast') is not None else '정보 없음', '%d ms' % row['acd'] if row['acd'] is not None else '정보 없음',
                    '%d ms' % row['motion'] if row['motion'] else '정보 없음', row['basis'], self.extra.value(),
                    ('\n쿨타임 맞추려고 대기를 늘림: ' + cd_for + ' (한 바퀴 돌아 다시 쓸 수 있을 때까지)') if cd_for else '',
                    real_long))
            else:
                meas, long = real_long, real_long
            it = _item(meas)
            it.setToolTip(long)
            self.table.setItem(r, 3, it)
            self.table.setRowHeight(r, 34)

    def _trigger_captured(self, key, mod):
        if key not in ('Ctrl', 'Alt', 'Shift'):              # 누른 그대로: Ctrl+Q 면 조합 Ctrl
            self.mod.setCurrentIndex(max(0, self.mod.findData(mod)))

    def _save_keys(self):
        """스킬 → 키를 캐릭터별로 기억 (다음에 단축키 창 정보가 없는 리플레이에서 씀)."""
        all_keys = self.app.s.get('macro_skill_keys') or {}
        if not all(isinstance(v, dict) for v in all_keys.values()):
            all_keys = {}
        all_keys[self.key_scope] = {str(k): v for k, v in self.confirmed_keys.items()}
        self.app.s['macro_skill_keys'] = all_keys
        self.app.s.save()

    def _key_changed(self, sid, text):
        if text in M.KEYS:
            self.keys[sid] = text
            self.confirmed_keys[sid] = text
            for r, row in enumerate(self.rows):                  # 같은 스킬 줄은 같이 바꾼다
                w = self.table.cellWidget(r, 1)
                if row['skill'] == sid and w.currentText() != text:
                    w.setCurrentText(text)

    def _add(self):
        if not self.rows:
            return
        if not self.keys_confirmed.isChecked():
            QMessageBox.warning(self, '키 확인', '키 배치는 추정일 수 있습니다. 표를 수정한 뒤 게임 설정과 일치함을 체크하세요.')
            return
        rows, keys = [], {}
        for r, row in enumerate(self.rows):
            rows.append(dict(row))
            keys[row['skill']] = self.table.cellWidget(r, 1).currentText()
        try:
            m = C.make_macro(self.parsed, rows, keys, self.name.text().strip() or '연격 콤보',
                             self.trigger.currentText(), self.mod.currentData(), self.mode.currentData(),
                             basis=str(self.q.currentData()), margin_ms=self.extra.value())
            registered = self.page.add_macro(m)
        except ValueError as exc:
            QMessageBox.warning(self, '등록 확인', str(exc))
            return
        self.confirmed_keys.update(keys)
        self._save_keys()
        QMessageBox.information(self, '등록됨', '"%s" 계산 결과와 리플레이 근거를 저장했습니다%s.' % (
            m['name'], '' if registered['enabled'] else ' (다른 매크로와 트리거가 겹쳐 비활성 저장)'))
        self.accept()
