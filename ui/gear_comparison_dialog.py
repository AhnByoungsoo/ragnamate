"""내 착용 스냅샷과 매물 옵션, 대상별 기록 피해와 외부 계산값의 처치선을 비교한다."""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout,
                               QLabel, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout)

from services.equipment_details import OptionIndex, clean_description, item_description, spec_text
from services import gear_comparison as C
from ui.completer import attach_completer
from ui.widgets import comma


def number():
    box = QDoubleSpinBox()
    box.setRange(0, 10**15)
    box.setDecimals(0)
    box.setGroupSeparatorShown(True)
    box.setSpecialValueText('미입력')
    return box


class GearComparisonDialog(QDialog):
    def __init__(self, app=None, candidate=None, item_db=None):
        super().__init__(app)
        self.app, self.candidate = app, candidate
        self.item_db = item_db or getattr(app, 'item_db', None)
        self.index = OptionIndex(self.item_db)
        result = getattr(getattr(app, 'replay_page', None), 'result', None)
        self.parsed = getattr(result, 'parsed', None)
        self.snapshot = C.worn_snapshot(self.parsed, self.item_db) if self.parsed else dict(known=False, time_ms=None, items=[])
        self.observations = C.damage_observations(self.parsed, self.snapshot['time_ms'] or 0) if self.parsed else dict(targets=[], samples=[])
        self.setWindowTitle('내 장비·피해 비교 — 리플레이와 노점 매물')
        self.resize(1220, 840)
        layout = QVBoxLayout(self)
        info = QLabel()
        if self.parsed:
            info.setText('%s · Base Lv.%s · 착용 스냅샷 %s초 · %s' % (
                self.parsed.character or '이름 미확인', self.parsed.base_level or '?',
                '%.1f' % (self.snapshot['time_ms'] / 1000) if self.snapshot['known'] else '미확인', Path(self.parsed.path).name))
        else:
            info.setText('내 장비는 리플레이 분석 탭에서 리플레이를 불러오면 확인할 수 있습니다.')
        info.setWordWrap(True)
        layout.addWidget(info)
        gear = QGridLayout()
        gear.addWidget(QLabel('현재 착용 장비 · 마지막 인벤토리 기록'), 0, 0)
        gear.addWidget(QLabel('노점 비교 장비 · 원문 옵션'), 0, 1)
        self.worn = QComboBox()
        self.worn.addItem('교체할 착용 장비를 선택하세요', None)
        for item in self.snapshot['items']:
            self.worn.addItem('+%s %s · 기록 등급 %s' % (item['refine'], item['name'], item['grade']), item)
        self.worn.currentIndexChanged.connect(self._show_worn)
        gear.addWidget(self.worn, 1, 0)
        title = QLabel(candidate['name'] + '\n' + spec_text(candidate) if candidate else '노점 검색의 [내 장비·피해 비교]로 비교할 매물을 선택하세요.')
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        gear.addWidget(title, 1, 1)
        self.current_options, self.candidate_options = QTextBrowser(), QTextBrowser()
        for browser in (self.current_options, self.candidate_options):
            browser.setOpenLinks(False)
            browser.setOpenExternalLinks(False)
            browser.setMinimumHeight(150)
        gear.addWidget(self.current_options, 2, 0)
        gear.addWidget(self.candidate_options, 2, 1)
        layout.addLayout(gear, 2)
        self.diff = QTextBrowser()
        self.diff.setOpenLinks(False)
        self.diff.setMaximumHeight(80)
        layout.addWidget(self.diff)

        controls = QGridLayout()
        self.monster = QComboBox()
        self.monster.setEditable(True)
        self.targets = list(self.observations['targets'])
        for target in self.targets:
            self.monster.addItem('%s [리플레이 #%s]' % (target['name'], target['class_id']), target)
        path = Path(__file__).resolve().parents[1]/'data/official/monsters.json'
        if path.is_file():
            for m in json.loads(path.read_text(encoding='utf-8'))['monsters']:
                self.monster.addItem('%s [%s]' % (m['name'], m['aegis']),
                                     dict(name=m['name'], class_id=None, hp=m.get('hp'), hp_source='저장된 공식 몬스터 자료'))
        self.monster.lineEdit().setPlaceholderText('몬스터 검색 · 예: 잠식당한 탄 (HP 미확인이면 직접 입력)')
        attach_completer(self.monster.lineEdit(), [self.monster.itemText(i) for i in range(self.monster.count())],
                         on_pick=lambda text: self.monster.setCurrentIndex(self.monster.findText(text)))
        self.monster.currentIndexChanged.connect(self._target_changed)
        self.monster.editTextChanged.connect(self._target_changed)
        self.skill = QComboBox()
        names = getattr(getattr(getattr(app, 'analysis', None), 'gamedata', None), 'skills', None)
        observed = {s['skill_id'] for s in self.observations['samples']}
        if self.parsed:
            observed.update(e.data['skill'] for e in self.parsed.events if e.actor_id == self.parsed.player_aid and e.data.get('skill') is not None)
        all_names = dict(getattr(names, 'names', {}) or {})
        for sid in sorted(observed) + sorted(set(all_names) - observed):
            self.skill.addItem('%s [#%s]' % (names.name(sid) if names else '스킬', sid), sid)
        self.skill.setEditable(True)
        attach_completer(self.skill.lineEdit(), [self.skill.itemText(i) for i in range(self.skill.count())],
                         on_pick=lambda text: self.skill.setCurrentIndex(self.skill.findText(text)))
        self.skill.currentIndexChanged.connect(self._calculate)
        self.skill.editTextChanged.connect(self._calculate)
        self.hp = number()
        self.hp.valueChanged.connect(self._hp_edited)
        self.hp_source = QLabel()
        self.hp_source.setWordWrap(True)
        for col, (title_text, widget) in enumerate((('대상 몬스터', self.monster), ('사용 스킬', self.skill), ('대상 최대 HP', self.hp))):
            controls.addWidget(QLabel(title_text), 0, col)
            controls.addWidget(widget, 1, col)
        controls.addWidget(self.hp_source, 2, 0, 1, 3)
        layout.addLayout(controls)
        self.allow_unverified = QCheckBox('미검증 피해 패킷 포함 (참고용)')
        self.allow_unverified.toggled.connect(self._calculate)
        layout.addWidget(self.allow_unverified)

        ranges = QGridLayout()
        self.manual_current = QCheckBox('현재 피해를 직접 입력')
        self.manual_after = QCheckBox('교체 후 피해 직접 입력 · 외부 시뮬레이터 계산값')
        self.current_min, self.current_max, self.after_min, self.after_max = number(), number(), number(), number()
        for column, (toggle, low, high) in enumerate(((self.manual_current, self.current_min, self.current_max),
                                                     (self.manual_after, self.after_min, self.after_max))):
            ranges.addWidget(toggle, 0, column * 2, 1, 2)
            ranges.addWidget(QLabel('1회 전체 최소 피해'), 1, column * 2)
            ranges.addWidget(QLabel('1회 전체 최대 피해'), 1, column * 2 + 1)
            for offset, widget in enumerate((low, high)):
                ranges.addWidget(widget, 2, column * 2 + offset)
                widget.setEnabled(False)
                widget.valueChanged.connect(self._calculate)
            toggle.toggled.connect(lambda enabled, a=low, b=high: (a.setEnabled(enabled), b.setEnabled(enabled), self._calculate()))
        layout.addLayout(ranges)
        self.result = QLabel()
        self.result.setTextFormat(Qt.PlainText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.setStyleSheet('padding:12px; background:#20354B; color:#F0F5FC; border-radius:8px; font-size:16px;')
        layout.addWidget(self.result)
        self.threshold = QProgressBar()
        self.threshold.setRange(0, 1000)
        layout.addWidget(self.threshold)
        note = QLabel('피해 기록은 선택 몬스터·스킬과 착용 스냅샷 이후 구간만 사용합니다. 동일 tick의 통지는 합산하고 div 수를 다시 곱하지 않습니다. '
                      '관측 범위는 이론적 최소·최대가 아니며, 버프·명중·치명타·대상 방어·HP 회복에 따라 달라집니다. '
                      '교체 후 장비 효과·DEF/RES·세트 조건을 자동 합산하는 엔진은 아직 미지원입니다.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#B9C7D9; font-size:12px;')
        layout.addWidget(note)
        actions = QHBoxLayout()
        hub = QPushButton('카츄아허브 시뮬레이터 열기')
        hub.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://kachuahub.com/damage-simulator')))
        actions.addWidget(hub)
        copy = QPushButton('비교 조건 복사')
        copy.clicked.connect(self.copy_inputs)
        actions.addWidget(copy)
        close = QPushButton('닫기')
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        if candidate:
            selected = [i for i in range(1, self.worn.count()) if self.worn.itemData(i)['item_id'] == candidate['item_id']]
            if len(selected) == 1:
                self.worn.setCurrentIndex(selected[0])
        self._show_worn()
        self._target_changed()

    def _show_worn(self):
        item = self.worn.currentData()
        if not item:
            self.current_options.setPlainText('착용 장비를 선택하세요.' if self.snapshot['items'] else '착용 상태가 확인된 인벤토리 기록이 없습니다. 창고·소지 장비는 착용 장비로 취급하지 않습니다.')
        else:
            summary = C.checked_refinement_summary(item['item_id'], item['refine'])
            lines = ([summary, ''] if summary else []) + [item['description'] or '기본 옵션 설명 없음', '\n[장착 카드·인챈트]']
            for iid in item['cards']:
                data = (getattr(self.item_db, 'items', {}) or {}).get(iid) or {}
                lines.append('%s (#%s)\n%s' % (data.get('name') or '설명 미확인', iid, clean_description(data.get('description'))))
            if item['options']:
                lines.append('\n[랜덤옵션 원시 기록 · 효과명 미확인]\n' + json.dumps(item['options'], ensure_ascii=False))
            self.current_options.setPlainText('\n'.join(lines))
        self.candidate_options.setPlainText(self._candidate_description())
        if item and self.candidate:
            diff = C.description_difference(item['description'], item_description(self.item_db, self.candidate['item_id']))
            self.diff.setPlainText('기본 옵션 원문 차이 · 적용 수치 자동 합산 아님\n' +
                                  '\n'.join(['+ ' + x for x in diff['added']] + ['− ' + x for x in diff['removed']]) +
                                  ('\n원문 옵션은 같음 · 제련·등급·카드 차이는 위에서 비교하세요.' if not any(diff.values()) else ''))
        else:
            self.diff.setPlainText('내 착용 장비와 매물을 선택하면 원문 옵션의 추가·제거 항목이 표시됩니다.')

    def _candidate_description(self):
        if not self.candidate:
            return '노점 매물을 선택하세요.'
        detail = self.candidate.get('_detail')
        summary = C.checked_refinement_summary(self.candidate['item_id'], self.candidate.get('refine'))
        lines = ([summary, ''] if summary else []) + [item_description(self.item_db, self.candidate['item_id']), '\n[카드·인챈트]']
        if detail is None:
            lines.append('매물 상세 자동 조회 대기 중 · 완료되면 갱신됩니다.')
        else:
            for name in detail.get('cards') or []:
                option = self.index.option(name)
                matches = option['matches']
                lines.append(name + '\n' + (clean_description(matches[0].get('description')) if len(matches) == 1 else '효과 미확인 또는 이름 중복'))
            lines.extend(detail.get('options') or [])
        return '\n'.join(lines)

    def _target(self):
        idx = self.monster.currentIndex()
        if idx >= 0 and self.monster.currentText() == self.monster.itemText(idx):
            return self.monster.currentData() or {}
        return dict(name=self.monster.currentText(), class_id=None, hp=None, hp_source='직접 지정 · HP 미확인')

    def _target_changed(self, *_):
        if not hasattr(self, 'result'):
            return
        target = self._target()
        self.hp.blockSignals(True)
        self.hp.setValue(target.get('hp') or 0)
        self.hp.blockSignals(False)
        self.hp_source.setText('HP 출처: ' + target.get('hp_source', '미확인'))
        self._calculate()

    def _hp_edited(self, *_):
        self.hp_source.setText('HP 출처: 직접 입력 (게임·계산기에서 확인한 최대 HP)')
        self._calculate()

    @staticmethod
    def _result_text(title, low, high, hp):
        counts = C.hits_to_kill(hp, low, high)
        if counts is None:
            return title + ': HP와 양수 피해 범위를 확인하세요. 최소 ≤ 최대여야 합니다.'
        text = '%s: %s~%s 피해 → 명중 시 %d~%d회' % (title, comma(int(low)), comma(int(high)), counts['fast'], counts['safe'])
        if counts['guaranteed_one']:
            text += ' · 입력된 최소 피해 기준 1회 처치선 충족'
        elif counts['possible_one']:
            text += ' · 최대 피해에서만 1회 처치 가능'
        return text

    def _calculate(self, *_):
        if not hasattr(self, 'result'):
            return
        target = self._target()
        idx = self.skill.currentIndex()
        sid = self.skill.currentData() if idx >= 0 and self.skill.currentText() == self.skill.itemText(idx) else None
        observed = C.observed_range(self.observations, target.get('class_id'), sid, self.allow_unverified.isChecked())
        hp = self.hp.value()
        if self.manual_current.isChecked():
            low, high = self.current_min.value(), self.current_max.value()
            text = self._result_text('현재 · 직접 입력', low, high, hp)
        else:
            low, high = observed['min'], observed['max']
            if observed['count']:
                text = self._result_text('현재 · 관측 피해', low, high, hp)
                text += '\n양수 표본 %d개 · 0 피해 %d개 · 미검증 포함 %d개' % (observed['count'], observed['misses'], observed['unverified'])
            else:
                text = '현재: 선택한 대상·스킬·착용 스냅샷 이후의 유효 피해 기록이 없습니다.'
        if self.manual_after.isChecked():
            text += '\n' + self._result_text('교체 후 · 외부 계산값 직접 입력', self.after_min.value(), self.after_max.value(), hp)
        else:
            text += '\n교체 후: 자동 피해 계산 미지원 · 외부 시뮬레이터의 1회 전체 피해 범위를 입력해 비교하세요.'
        self.result.setText(text)
        ratio = low / hp if hp > 0 and low and high and 0 < low <= high else 0
        self.threshold.setValue(min(1000, int(ratio * 1000)))
        self.threshold.setFormat('현재 최소 피해 / 대상 HP: %.1f%% · 100%%가 1회 처치선' % (ratio * 100))

    def copy_inputs(self):
        payload = dict(character=getattr(self.parsed, 'character', None), snapshot_ms=self.snapshot['time_ms'],
                       worn=self.snapshot['items'], replace=self.worn.currentData(), candidate=self.candidate,
                       target=self._target(), target_hp=self.hp.value(), hp_source=self.hp_source.text(),
                       skill_id=self.skill.currentData(), result=self.result.text(), automatic_swap_damage_supported=False)
        QGuiApplication.clipboard().setText(json.dumps(payload, ensure_ascii=False, indent=2))
