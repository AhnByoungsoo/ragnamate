"""Replay 분석 화면: 기본 정보 → KPI → 자동 요약 → 세부 탭."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from analyzer.analyzers.timeline import timeline_rows
from analyzer.analyzers.context import Context
from analyzer.events import EventType as E
from config.settings import BASE_DIR
from core import elements
from core import loot_rows as LR
from services import report_export
from ui.economy_tasks import BackgroundTask
from ui.loot_table import LootTable
from ui.widgets import Col, Kpi, Table, comma, hms, line_chart, pct, sec, unit
from ui.completer import attach_completer, set_words

LEGEND = '● 직접 측정   ○ 계산   △ 추정/미검증'



class ReplayPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.result = None      # AnalysisResult
        self.report = None
        v = QVBoxLayout(self)

        head = QHBoxLayout()
        self.info = QLabel('Replay를 불러오면 분석 결과가 표시됩니다.')
        self.info.setObjectName('replayInfo')
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head.addWidget(self.info, 1)
        head.addWidget(QLabel('분석 구간'))
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(260)
        self.window_combo.activated.connect(self._window_changed)
        head.addWidget(self.window_combo)
        for text, slot in (('내 장비·피해 비교', self.open_gear_comparison), ('능력치 → 매크로 최적화', self.open_combo), ('버프 루틴 만들기', self.open_buff_routine), ('맵 가이드', self.open_map_guide), ('빌드 라벨', self.set_build), ('Excel', self.export_xlsx),
                           ('JSON', self.export_json), ('창고 분석으로 →', self.app.go_storage)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            head.addWidget(b)
        v.addLayout(head)

        kg = QGridLayout()
        self.kpi = {}
        for i, (k, t) in enumerate([('dps', '전투 DPS ○'), ('kills', '처치 수 ●'), ('exp', '시간당 경험치'),
                                    ('profit', '시간당 수익 ○'), ('damage', '총 피해 ●'), ('zeny', 'Zeny 순변화 ○')]):
            self.kpi[k] = Kpi(t)
            kg.addWidget(self.kpi[k], 0, i)
        v.addLayout(kg)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(110)
        v.addWidget(self.summary)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self._build_tabs()
        legend = QLabel(LEGEND)
        legend.setObjectName('cardTitle')
        v.addWidget(legend)

    def open_combo(self):
        from ui.combo_dialog import ComboDialog
        ComboDialog(self.app, self.app.macro_page).exec()

    def open_buff_routine(self):
        from ui.buff_routine_dialog import BuffRoutineDialog
        self.buff_routine_dialog = BuffRoutineDialog(self.app, self.app.macro_page)
        self.buff_routine_dialog.show()

    def open_gear_comparison(self):
        from ui.gear_comparison_dialog import GearComparisonDialog
        dialog = GearComparisonDialog(self.app)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        self.gear_comparison_dialog = dialog
        dialog.show()

    # ------------------------------------------------------------------ 탭
    def _build_tabs(self):
        # 전투
        w = QWidget()
        h = QHBoxLayout(w)
        self.combat_tbl = Table([Col('항목', lambda r: r[0], width=200), Col('값', lambda r: r[1], width=180),
                                 Col('', lambda r: r[2])])
        h.addWidget(self.combat_tbl, 1)
        right = QVBoxLayout()
        self.dps_bucket = QComboBox()
        for b in ('1', '5', '10', '30'):
            self.dps_bucket.addItem('Rolling DPS %s초' % b, b)
        self.dps_bucket.setCurrentIndex(1)
        self.dps_bucket.currentIndexChanged.connect(self._draw_charts)
        right.addWidget(self.dps_bucket)
        self.dps_chart, self.set_dps = line_chart('시간별 DPS')
        right.addWidget(self.dps_chart, 1)
        self.hp_chart, self.set_hp = line_chart('HP / SP')
        right.addWidget(self.hp_chart, 1)
        h.addLayout(right, 2)
        self.tabs.addTab(w, '전투')

        self.skill_tbl = Table([
            Col('스킬', lambda r: r['name'], width=210,
                icon=lambda r: self.app.icons.icon('skill_%s' % r['skill_id']),
                tooltip=lambda r: self.app.display.skill(r['skill_id']).tooltip()),
            Col('ID', lambda r: r['skill_id'], comma, width=60),
            Col('사용', lambda r: r['uses'], comma, width=60), Col('캐스팅', lambda r: r['casts'], comma, width=60),
            Col('총피해', lambda r: r['total_damage'], unit, width=90),
            Col('평균피해', lambda r: r['avg_damage'], unit, width=90),
            Col('최소', lambda r: r['min_damage'], unit, width=80), Col('최대', lambda r: r['max_damage'], unit, width=80),
            Col('기여도', lambda r: r['share'], pct, width=70), Col('DPS', lambda r: r['dps'], unit, width=80),
            Col('평균간격', lambda r: r['avg_interval'], sec, width=80),
            Col('최소간격', lambda r: r['min_interval'], sec, width=80),
            Col('재사용 대기 ●', lambda r: r.get('cooldown'), lambda v: '-' if v is None else '%.2f초' % (v / 1000),
                True, 96, tooltip=lambda r: '서버가 이 스킬을 쓸 때마다 알려 준 값입니다(패킷으로 측정).'),
            Col('실패 ●', lambda r: r.get('failures'), comma, width=64,
                tooltip=lambda r: '\n'.join('%s %d회' % c for c in r.get('fail_causes') or []) or '실패 기록 없음',
                color=lambda r: QColor('#FFC98A') if r.get('blocked') else None),
            Col('평균캐스팅', lambda r: r['cast_ms_avg'], lambda v: '-' if v is None else '%dms' % v, True, 80),
            Col('관측효율', lambda r: r['efficiency_observed'], pct, width=70),
            Col('기대딜레이 대비', lambda r: r['efficiency_expected'], pct)], sort_col=4)
        self.skill_note = QLabel()
        self.tabs.addTab(self._with_note(self.skill_tbl, self.skill_note), '스킬')

        self.buff_tbl = Table([
            Col('버프', lambda r: self.app.display.status(r['efst']).text, width=270,
                icon=lambda r: self.app.icons.icon('efst_%s' % r['efst']),
                tooltip=lambda r: self.app.display.status(r['efst']).tooltip()),
            Col('EFST', lambda r: r['efst'], comma, width=60),
            Col('가동률', lambda r: r['uptime'], pct, width=70),
            Col('활성시간', lambda r: r['active_ms'], hms, True, 80),
            Col('공백합', lambda r: r['gap_total_ms'], hms, True, 80), Col('재사용', lambda r: r['recasts'], comma, width=60),
            Col('DPS(버프O)', lambda r: r['dps_on'], unit, width=90), Col('DPS(버프X)', lambda r: r['dps_off'], unit, width=90),
            Col('공백 구간', lambda r: ', '.join('%s~%s(%.0fs)' % (hms(g['start_ms']), hms(g['end_ms']), g['sec'])
                                              for g in r['gaps'][:10]) or r.get('dps_note') or '')], sort_col=2)
        self.tabs.addTab(self.buff_tbl, '버프')

        w = QWidget()
        h = QVBoxLayout(w)
        self.exp_label = QLabel()
        self.exp_label.setWordWrap(True)
        h.addWidget(self.exp_label)
        self.exp_chart, self.set_exp = line_chart('누적 EXP')
        h.addWidget(self.exp_chart, 1)
        self.tabs.addTab(w, 'EXP · Zeny')

        w = QWidget()
        h = QVBoxLayout(w)
        cards = QHBoxLayout()
        self.loot_kpi = {}
        for key, title in (('best', '획득 가치 ○'), ('vend', '노점에 팔면 ○'), ('npc', '상점에 팔면 △'),
                           ('zeny', 'Zeny 순변화 ●'), ('cons', '소모품 △'), ('profit', '순이익 ○')):
            self.loot_kpi[key] = Kpi(title)
            cards.addWidget(self.loot_kpi[key])
        h.addLayout(cards)
        line = QHBoxLayout()
        self.profit_label = QLabel()
        self.profit_label.setWordWrap(True)
        self.profit_label.setObjectName('profitLabel')
        line.addWidget(self.profit_label, 1)
        self.b_loot_prices = QPushButton('시세 받기')
        self.b_loot_prices.setToolTip('노점 시세를 모르는 아이템을 공식 노점 자료로 채웁니다(아이템 1종에 요청 2번).')
        self.b_loot_prices.clicked.connect(self.fetch_loot_prices)
        line.addWidget(self.b_loot_prices)
        h.addLayout(line)
        sp = QSplitter(Qt.Horizontal)
        icons = self.app.loot_icons() if hasattr(self.app, 'loot_icons') else None
        developer = bool(self.app.s.get('developer_mode'))
        self.loot_tbl = LootTable(self.app.s, icons, developer=developer)
        self.cons_tbl = LootTable(self.app.s, icons, sell=False, developer=developer)
        self._loot_report = None
        self.loot_task = BackgroundTask(self, self._loot_prices_done, self._loot_prices_failed)
        for t, title in ((self.loot_tbl, '획득 아이템 — 노점가와 상점가 중 더 비싼 쪽에 색이 들어갑니다 · ★ 고가'),
                         (self.cons_tbl, '소모품 △ — 인벤토리가 줄어든 만큼(판매·보관·버리기 포함 가능)')):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            caption = QLabel(title)
            caption.setObjectName('cardTitle')
            bl.addWidget(caption)
            bl.addWidget(t)
            sp.addWidget(box)
        sp.setSizes([780, 420])
        h.addWidget(sp, 1)
        self.tabs.addTab(w, '드랍 / 수익')
        self._build_tracking_tabs(icons, developer)

        self.mob_tbl = Table([
            Col('몬스터', lambda r: r['name'], width=170), Col('ID', lambda r: r['class_id'], comma, width=60),
            Col('속성 · 종족 · 크기', lambda r: elements.traits_text(elements.shared().info(r['class_id'], r['name'])),
                width=190),
            Col('조우', lambda r: r['encounters'], comma, width=60), Col('처치', lambda r: r['kills'], comma, width=60),
            Col('총피해', lambda r: r['damage'], unit, width=90), Col('평균피해', lambda r: r['avg_damage'], unit, width=90),
            Col('평균 Kill', lambda r: r['kill_time_avg'], sec, width=80),
            Col('최소 Kill', lambda r: r['kill_time_min'], sec, width=80),
            Col('최대 Kill', lambda r: r['kill_time_max'], sec, width=80), Col('받은 피해', lambda r: r['taken'], unit)],
            sort_col=4)
        w = QWidget()
        h = QVBoxLayout(w)
        self.elem_label = QLabel()
        self.elem_label.setWordWrap(True)
        h.addWidget(self.elem_label)
        h.addWidget(self.mob_tbl, 1)
        self.tabs.addTab(w, '몬스터 · 추천 속성')

        w = QWidget()
        h = QVBoxLayout(w)
        fl = QHBoxLayout()
        self.tl_type = QComboBox()
        self.tl_type.addItem('전체 이벤트', '')
        self.tl_type.currentIndexChanged.connect(self._tl_filter)
        fl.addWidget(self.tl_type)
        self.tl_search = QLineEdit(placeholderText='내용 검색')
        self.tl_search.textChanged.connect(self._tl_filter)
        self.tl_completer = attach_completer(self.tl_search)
        fl.addWidget(self.tl_search, 1)
        h.addLayout(fl)
        self.tl_tbl = Table([Col('시간', lambda r: r[0], hms, True, 80), Col('분류', lambda r: r[1], width=100),
                             Col('내용', lambda r: r[2], width=520), Col('', lambda r: r[3], width=30),
                             Col('packet#', lambda r: r[4], comma)], sort_col=None)
        h.addWidget(self.tl_tbl, 1)
        self.tabs.addTab(w, '타임라인')

    def _with_note(self, table, note):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(table, 1)
        note.setObjectName('cardTitle')
        l.addWidget(note)
        return w

    # ------------------------------------------------------------------ 데이터 표시
    def set_result(self, res, report=None):
        self.result = res
        self.window_combo.clear()
        rep = report or res.report
        self.window_combo.addItem('전체 Replay', (0, res.parsed.duration_ms, '전체'))
        for s in rep['segments']:
            label = '구간 %d · %s%s (%s)' % (s['index'] + 1, s['map_name'] if s['map'] else '(시작 맵 불명)',
                                           ' · 사냥' if s['is_hunt'] else '', hms(s['duration_ms']))
            self.window_combo.addItem(label, (s['start_ms'], s['end_ms'], label))
        cur = rep['window']
        for i in range(self.window_combo.count()):
            a, b, _ = self.window_combo.itemData(i)
            if a == cur['start_ms'] and b == cur['end_ms']:
                self.window_combo.setCurrentIndex(i)
        self.show_report(rep)

    def show_report(self, rep):
        self.report = rep
        m, w, cb, k = rep['meta'], rep['window'], rep['combat'], rep['kpi']
        self.info.setText('<b>%s</b> · %s Lv.%s/%s · %s · 녹화 %s · 길이 %s' % (
            m['character'] or '?', m['job'], m['level'] or '?', m['job_level'] or '?',
            ' → '.join(m.get('map_names') or []) or '-', m['start_time'].replace('T', ' '), hms(m['duration_ms'])))
        na = '데이터 없음'
        self.kpi['dps'].set(unit(k['combat_dps']) if k['combat_dps'] is not None else na,
                            '전체기준 %s' % unit(k['total_dps']) if k['total_dps'] is not None else '')
        self.kpi['kills'].set(comma(k['kills']) if cb['available'] else na,
                              '%s /h' % comma(k['kills_per_hour']) if k['kills_per_hour'] else '')
        self.kpi['exp'].set(unit(k['base_exp_per_hour']) if rep['exp']['available'] else '미지원',
                            'Job %s /h' % unit(k['job_exp_per_hour']) if rep['exp']['available'] else 'EXP 패킷 미검증')
        self.kpi['profit'].set(unit(k['profit_per_hour']) + ' Z' if k['profit_per_hour'] is not None else na,
                               '순이익 %s Z' % comma(k['profit']) if k['profit'] is not None else '')
        self.kpi['damage'].set(unit(cb['total_damage']) if cb['available'] else na,
                               '최대 1타 %s' % unit(cb['max_hit']) if cb['max_hit'] else '')
        z = rep['zeny']
        self.kpi['zeny'].set('{:+,} Z'.format(z['net']) if z.get('available') else na,
                             '%s → %s' % (comma(z.get('start')), comma(z.get('end'))) if z.get('available') else '')
        notes = [n for n in (cb.get('note'), rep['skills'].get('note'), rep['exp'].get('note')) if n]
        self.summary.setHtml('<br>'.join(rep['summary_text']) +
                             ('<br><span style="color:gray">%s</span>' % ' / '.join(notes) if notes else ''))

        b = cb['burst']
        g = lambda key: b.get(key) or b.get(str(key)) or {}
        if not cb['available']:          # 측정값이 없는데 0으로 보이지 않게
            self.combat_tbl.set_rows([('전투 데이터', '데이터 없음', cb['note']),
                                      ('● 처치', '데이터 없음', '시야 내 몬스터 사망 %s' % comma(cb['monster_deaths_in_view'])),
                                      ('○ HP 회복량', comma(cb['hp_recovered']), 'HP 증가량 합'),
                                      ('○ 분석 구간 길이', hms(cb['duration_ms']), '')])
            self._after_combat(rep)
            return
        self.combat_tbl.set_rows([
            ('● 총 피해', comma(cb['total_damage']), ''), ('○ 전투기준 DPS', unit(cb['dps_combat']), ''),
            ('○ 전체기준 DPS', unit(cb['dps_total']), ''),
            ('○ 실전 전투시간', hms(cb['combat_ms']), '피해 간격 %d초 이상은 비전투' % (self.app.s['combat_gap_ms'] // 1000)),
            ('○ 분석 구간 길이', hms(cb['duration_ms']), '')] +
            [('○ 최대 %d초 DPS' % s, unit(g(s).get('dps')), ('시작 %s' % hms(g(s)['at_ms'])) if g(s).get('at_ms') is not None else '')
             for s in (1, 3, 5, 10)] + [
            ('● 공격 횟수', comma(cb['attack_count']), '일반 %s / 스킬 %s' % (comma(cb['normal_attacks']), comma(cb['skill_hits']))),
            ('● 평균/최대/최소 피해', '%s / %s / %s' % (unit(cb['avg_hit']), unit(cb['max_hit']), unit(cb['min_hit'])), ''),
            ('● Miss / Critical', '%s / %s' % (comma(cb['miss']), comma(cb['critical'])), ''),
            ('● 처치', comma(cb['kills']), '시야 내 몬스터 사망 %s' % comma(cb['monster_deaths_in_view'])),
            ('○ Kill Time 평균/최소/최대', '%s / %s / %s' % (sec(cb['kill_time_avg']), sec(cb['kill_time_min']),
                                                      sec(cb['kill_time_max'])), '첫 타격 → 사망'),
            ('● 받은 피해', comma(cb['taken_total']), '평균 %s / 최대 %s / 회피 %s' % (
                unit(cb['taken_avg']), unit(cb['taken_max']), comma(cb['taken_miss']))),
            ('○ HP 회복량', comma(cb['hp_recovered']), 'HP 증가량 합'),
            ('비고', cb['note'] or '', '')])
        self._after_combat(rep)

    def _after_combat(self, rep):
        z = rep['zeny']
        self.skill_tbl.set_rows(rep['skills']['skills'])
        self.skill_note.setText('%s   %s' % (rep['skills'].get('note', ''), rep['skills'].get('unsupported', '')))
        self.buff_tbl.set_rows(rep['buffs']['buffs'])
        ex = rep['exp']
        exp_txt = ('Base EXP 총 %s (시간당 %s) · Job EXP 총 %s (시간당 %s)' % (
            comma(ex['base_total']), unit(ex['base_per_hour']), comma(ex['job_total']), unit(ex['job_per_hour']))
                   if ex['available'] else ex['note'])
        if z.get('available'):
            exp_txt += '<br>Zeny: 시작 %s → 끝 %s · <b>순변화 %s Z</b> (증가 %s / 감소 %s) — %s' % (
                comma(z['start']), comma(z['end']), '{:+,}'.format(z['net']), comma(z['gained']), comma(z['spent']),
                z['split_note'])
        self.exp_label.setText(exp_txt)
        self._loot_report = rep
        self._fill_loot()
        self._fill_tracking(rep)
        self.mob_tbl.set_rows(rep['monsters']['monsters'])
        self.elem_label.setText(elements.summary_text(elements.shared().recommend(
            [dict(class_id=m['class_id'], name=m['name'], weight=m['encounters']) for m in rep['monsters']['monsters']])))
        self._fill_timeline()
        self._draw_charts()

    # ------------------------------------------------------------------ 드랍 추적 · 퀘스트 · 노점
    def _build_tracking_tabs(self, icons, developer):
        w = QWidget()
        v = QVBoxLayout(w)
        self.drop_label = QLabel()
        self.drop_label.setWordWrap(True)
        v.addWidget(self.drop_label)
        sp = QSplitter(Qt.Horizontal)
        self.missed_tbl = LootTable(self.app.s, icons, developer=developer)
        self.mob_drop_tbl = Table([
            Col('몬스터', lambda r: r['mob'], width=170), Col('처치', lambda r: r['kills'], comma, width=60),
            Col('아이템', lambda r: r['name'], width=190, icon=lambda r: icons.icon(r['item_id']) if icons else None),
            Col('떨어진 횟수', lambda r: r['drops'], comma, width=90),
            Col('드랍률 ●', lambda r: r['rate'], lambda v: '-' if v is None else '%.1f%%' % v, True, 80,
                tooltip=lambda r: '이 리플레이에서 잰 값: 떨어진 횟수 ÷ 그 몬스터 처치 수. 표본이 적으면 크게 흔들립니다.'),
            Col('수량', lambda r: r['amount'], comma)], sort_col=3)
        self.use_tbl = Table([
            Col('쓴 아이템', lambda r: r['name'], width=200, icon=lambda r: icons.icon(r['item_id']) if icons else None),
            Col('횟수 ●', lambda r: r['uses'], comma, width=70), Col('남은 수', lambda r: r['remaining'], comma)], sort_col=1)
        for t, title in ((self.missed_tbl, '놓친 드랍 — 떨어졌지만 내가 줍지 않은 것(남이 줍거나 사라짐)'),
                         (self.mob_drop_tbl, '몬스터별 드랍 ● — 누가 떨어뜨렸는지 한 종류로 좁혀진 것만'),
                         (self.use_tbl, '아이템 사용 ●')):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            caption = QLabel(title)
            caption.setObjectName('cardTitle')
            bl.addWidget(caption)
            bl.addWidget(t)
            sp.addWidget(box)
        sp.setSizes([560, 520, 300])
        v.addWidget(sp, 1)
        self.tabs.addTab(w, '드랍 추적')

        w = QWidget()
        v = QVBoxLayout(w)
        self.store_label = QLabel()
        self.store_label.setWordWrap(True)
        v.addWidget(self.store_label)
        sp = QSplitter(Qt.Horizontal)
        self.quest_tbl = Table([
            Col('퀘스트', lambda r: r['title'] or '퀘스트 %s' % r['quest'], width=230,
                tooltip=lambda r: '퀘스트 번호 %s' % r['quest']),
            Col('진행 ●', lambda r: r['count'], lambda v: comma(v), width=70),
            Col('목표', lambda r: r['need'], lambda v: '-' if v is None else comma(v), numeric=True, width=70),
            Col('상태', lambda r: '완료' if r['complete'] else '끝남' if r['removed'] else '진행 중', width=70,
                color=lambda r: QColor('#43C08A') if r['complete'] else None),
            Col('내용', lambda r: ' · '.join('%s %s/%s' % (g['name'] or '목표', g['count'], '?' if g['need'] is None else g['need'])
                                            for g in r['goals']))])
        price_cols = lambda: [                                                           # noqa: E731
            Col('아이템', lambda r: ('+%d ' % r['refine'] if r['refine'] else '') + r['name'], width=220,
                icon=lambda r: icons.icon(r['item_id']) if icons else None,
                tooltip=lambda r: '카드·인챈트 %d개' % len(r['cards']) if r['cards'] else ''),
            Col('가격 ●', lambda r: r['price'], comma, width=110), Col('수량', lambda r: r['amount'], comma, width=70)]
        self.search_tbl = Table(price_cols() + [Col('상점', lambda r: r.get('shop', ''))], sort_col=1, desc=False)
        self.opened_tbl = Table(price_cols(), sort_col=1)
        for t, title in ((self.quest_tbl, '퀘스트 사냥 수 ● — 게임이 보낸 진행 그대로'),
                         (self.search_tbl, '게임 안에서 노점 검색한 결과 ● — 시세로 저장됩니다'),
                         (self.opened_tbl, '열어 본 노점의 물건 ●')):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            caption = QLabel(title)
            caption.setObjectName('cardTitle')
            bl.addWidget(caption)
            bl.addWidget(t)
            sp.addWidget(box)
        sp.setSizes([520, 480, 380])
        v.addWidget(sp, 1)
        self.tabs.addTab(w, '퀘스트 · 노점')

    def _fill_tracking(self, rep):
        t = rep.get('tracking') or {}                     # 예전에 저장한 분석에는 없다
        d = t.get('drops') or {}
        missed = LR.build({r['item_id']: r['quantity'] for r in d.get('missed', [])},
                          lambda i: next(r['name'] for r in d['missed'] if r['item_id'] == i),
                          getattr(self.app, 'vend_price', None), getattr(self.app, 'npc_price', None))
        self.missed_tbl.set_overcharge(int(self.app.s.get('overcharge_rate', LR.OVERCHARGE_MAX) or 0))
        self.missed_tbl.set_rows(missed)
        self.mob_drop_tbl.set_rows([dict(i, mob=m['name'], kills=m['kills']) for m in d.get('by_mob', []) for i in m['items']])
        self.use_tbl.set_rows(t.get('item_use') or [])
        if d.get('available'):
            lost = LR.totals(missed)
            self.drop_label.setText(
                '떨어진 것 <b>%s</b>건 · 내가 주운 것 %s건 · <b style="color:#FFC98A">놓친 것 %s건</b>%s · 누가 떨어뜨렸는지 모르는 것 %s건'
                '<br><span style="color:#8E97A6">바닥에 떨어진 순간과 사라진 순간을 패킷으로 따라갑니다. 화면 밖에서 사라진 것, '
                '맵을 떠날 때 남은 것은 셀 수 없습니다.</span>' % (
                    comma(d['dropped']), comma(d['picked']), comma(d['missed_count']),
                    ' (값 %s Z)' % comma(lost['best']) if lost['best'] else '', comma(d['unknown_owner'])))
        else:
            self.drop_label.setText(d.get('note') or '이 분석에는 드랍 추적 자료가 없습니다(리플레이를 다시 불러오면 생깁니다).')
        st = t.get('stores') or {}
        self.quest_tbl.set_rows((t.get('quests') or {}).get('quests', []))
        self.search_tbl.set_rows(st.get('search', []))
        self.opened_tbl.set_rows(st.get('opened', []))
        self.store_label.setText(
            '지나가며 본 노점 간판: 판매 %s곳 · 구매 %s곳%s' % (
                comma(st.get('sale_signs', 0)), comma(st.get('buy_signs', 0)),
                ('<br>구매 노점: ' + ' / '.join(st['buying'][:8])) if st.get('buying') else '')
            if st.get('available') else '이 구간에는 노점 기록이 없습니다.')

    # ------------------------------------------------------------------ 드랍 / 수익
    def _loot_rows(self, source):
        """리포트의 아이템 줄 → 노점가·상점가를 붙인 줄. 공식 노점 자료를 먼저 보고, 없으면 리포트의 시세를 쓴다."""
        known = {r['item_id']: r for r in source}
        vend_price = getattr(self.app, 'vend_price', None)

        def vend(item_id):
            unit_price, basis = vend_price(item_id) if vend_price else (None, '')
            if unit_price is None and known[item_id].get('unit_price') is not None:
                return known[item_id]['unit_price'], '시세 탭의 저장된 시세'
            return unit_price, basis
        return LR.build({i: r['quantity'] for i, r in known.items()}, lambda i: known[i]['name'], vend,
                        getattr(self.app, 'npc_price', None))

    def _fill_loot(self):
        rep = self._loot_report
        if rep is None:
            return
        lt, z = rep['loot'], rep['zeny']
        rate = int(self.app.s.get('overcharge_rate', LR.OVERCHARGE_MAX) or 0)
        loot, cons = self._loot_rows(lt.get('loot', [])), self._loot_rows(lt.get('consumables', []))
        self.loot_tbl.set_overcharge(rate)
        self.loot_tbl.set_rows(loot)
        self.cons_tbl.set_rows(cons)
        t, c = LR.totals(loot), LR.totals(cons)
        money = lambda v, empty='모름': empty if v is None else comma(v) + ' Z'        # noqa: E731
        kpi = self.loot_kpi
        for key, color in (('best', '#FFD479'), ('vend', '#8FC2FF'), ('npc', '#7FE0B0')):      # 표의 색과 같은 뜻
            kpi[key].value.setStyleSheet('color: %s;' % color)
        if not loot:
            for key in ('best', 'vend', 'npc'):
                kpi[key].set('-', '획득 기록 없음' if lt.get('available') else '')
        else:
            kpi['best'].set(money(t['best']), '%d종 %s개%s' % (
                t['count'], comma(t['quantity']), ' · 값 모름 %d종 제외' % t['unpriced'] if t['unpriced'] else ''))
            kpi['vend'].set(money(t['vend']), '노점 시세 아는 %d종' % (t['count'] - t['no_vend']))
            kpi['npc'].set(money(t['npc']), '오버차지 +%d%%' % rate if rate else '오버차지 없음')
        net = z.get('net') if z.get('available') else None
        kpi['zeny'].set('-' if net is None else '{:+,} Z'.format(net))
        kpi['cons'].set('-' if not cons else ('-' + money(c['vend'])) if c['vend'] is not None else '모름',
                        '%d종' % c['count'] if cons else '')
        parts = [v for v in (t['best'], net) if v is not None]
        if parts:
            profit = sum(parts) - (c['vend'] or 0)
            ms = (rep.get('window') or {}).get('duration_ms') or 0
            kpi['profit'].set('{:+,} Z'.format(profit), '시간당 %s Z' % comma(profit * 3_600_000 // ms) if ms else '')
            kpi['profit'].value.setStyleSheet('color: %s;' % ('#FF7A7A' if profit < 0 else '#43C08A'))
        else:
            kpi['profit'].set('-', '획득·Zeny 정보 없음')
            kpi['profit'].value.setStyleSheet('')
        unknown = sum(1 for r in loot + cons if r['vend'] is None)
        self.b_loot_prices.setText('시세 받기 (%d종)' % unknown if unknown else '시세 받기')
        self.b_loot_prices.setEnabled(bool(unknown) and self.loot_task.future is None
                                      and getattr(self.app, '_loot_pricer', lambda: None)() is not None)
        self.profit_label.setText(
            '<span style="color:#8E97A6">%s · 획득 가치 = 노점가와 상점가 중 더 비싼 쪽 × 수량 · 실제 판매금액은 다를 수 있습니다</span>'
            % (lt.get('method') or lt.get('note', '')))

    def fetch_loot_prices(self):
        pricer = getattr(self.app, '_loot_pricer', lambda: None)()
        if self._loot_report is None or pricer is None:
            return
        lt = self._loot_report['loot']
        todo = [(r['item_id'], r['name']) for r in self.loot_tbl.model_.rows + self.cons_tbl.model_.rows
                if r['vend'] is None]

        def job(stop):
            done = 0
            for item_id, name in todo:
                if stop.is_set():
                    break
                pricer.ensure(item_id, name)
                done += 1
            return done
        if todo and self.loot_task.start(job):
            self.b_loot_prices.setEnabled(False)
            self.b_loot_prices.setText('받는 중… %d종' % len(todo))
            self.app.status('노점 시세 받는 중 — 아이템 %d종, 요청 최대 %d번 (%s)' % (
                len(todo), len(todo) * 2, lt.get('method') or ''))

    def _loot_prices_done(self, done):
        self._fill_loot()
        self.app.status('노점 시세 %d종을 받았습니다. 그래도 "-"인 것은 지금 노점도 최근 거래도 없는 아이템입니다.' % done)

    def _loot_prices_failed(self, exc):
        self._fill_loot()
        self.app.status('시세 받기 실패: %s' % exc)

    def _fill_timeline(self):
        res, w = self.result, self.report['window']
        if not res:
            return
        ctx = Context.build(res.parsed, self.app.analysis.gamedata, self.app.s, w['start_ms'], w['end_ms'])
        rows = timeline_rows(ctx, self._describe)
        self._tl_rows = rows
        types = sorted({r[1] for r in rows})
        cur = self.tl_type.currentData()
        self.tl_type.blockSignals(True)
        self.tl_type.clear()
        self.tl_type.addItem('전체 이벤트', '')
        for t in types:
            self.tl_type.addItem(t, t)
        idx = self.tl_type.findData(cur)
        self.tl_type.setCurrentIndex(max(idx, 0))
        self.tl_type.blockSignals(False)
        self.tl_tbl.set_rows(rows)
        set_words(self.tl_completer, [r[2] for r in rows])
        self._tl_filter()

    def _tl_filter(self, *_):
        t, q = self.tl_type.currentData(), self.tl_search.text().strip().lower()
        self.tl_tbl.set_filter(lambda r: (not t or r[1] == t) and (not q or q in r[2].lower()))

    def _describe(self, e):
        gd, pr = self.app.analysis.gamedata, self.result.parsed
        who = lambda aid: 'ME' if aid == pr.player_aid else (pr.actors.get(aid, {}).get('name') or str(aid))
        d, t = e.data, e.event_type
        if t == E.MAP_CHANGE:
            return '%s (%s,%s)' % (gd.maps.name(e.map_name), e.x, e.y)
        if t in (E.BUFF_START, E.BUFF_END):
            return '%s → %s' % (gd.buffs.name(d['efst']), who(e.actor_id))
        if t in (E.SKILL_USE, E.SKILL_CAST_START):
            extra = ' 캐스팅 %dms' % d['cast_ms'] if t == E.SKILL_CAST_START else ''
            return '%s: %s → %s%s' % (who(e.actor_id), gd.skills.name(d['skill']), who(e.target_id) if e.target_id else
                                      '(%s,%s)' % (e.x, e.y), extra)
        if t == E.DAMAGE:
            return '%s → %s %s%s' % (who(e.actor_id), who(e.target_id), comma(d['amount']),
                                     ' (%s)' % gd.skills.name(d['skill']) if d.get('skill') else '')
        if t == E.MONSTER_DEATH:
            return '%s 사망' % who(e.actor_id)
        if t == E.MONSTER_SPAWN:
            return '%s (%s,%s)' % (d.get('name') or gd.monsters.name(d['class_id']), e.x, e.y)
        if t == E.ZENY_CHANGE:
            return comma(d['value']) + ' Z'
        if t in (E.INVENTORY_UPDATE, E.CART_UPDATE, E.STORAGE_UPDATE):
            return '%d칸' % d['count']
        if t == E.ACTION:
            return '%s: %s' % (who(e.actor_id), d['action'])
        if t == E.PLAYER_MOVE:
            return '(%s,%s)' % (e.x, e.y)
        return str(d)[:120]

    def _draw_charts(self, *_):
        rep = self.report
        if not rep:
            return
        b = self.dps_bucket.currentData() or '5'
        pts = rep['rolling_dps'].get(b) or rep['rolling_dps'].get(int(b)) or []
        s0 = rep['window']['start_ms']
        series = {'DPS (%s초)' % b: [((t - s0) / 1000, v) for t, v in pts]}
        if pts and max(v for _, v in pts) > 0:
            t, v = max(pts, key=lambda p: p[1])
            series['최대 DPS 구간 %s' % unit(v)] = [((t - s0) / 1000, 0), ((t - s0) / 1000, v)]
        self.set_dps(series)
        vt = rep['vitals']
        self.set_hp({'HP': [((t - s0) / 1000, v) for t, v in vt['hp_series']],
                     'SP': [((t - s0) / 1000, v) for t, v in vt['sp_series']]})
        ex = rep['exp']
        self.set_exp({'Base': [((t - s0) / 1000, b_) for t, b_, j in ex.get('series', [])],
                      'Job': [((t - s0) / 1000, j) for t, b_, j in ex.get('series', [])]})

    # ------------------------------------------------------------------ 동작
    def _window_changed(self, i):
        if not self.result:
            return
        a, b, label = self.window_combo.itemData(i)
        self.app.run(lambda _: self.app.analysis.reanalyze(self.result, a, b, label), self.show_report,
                     '구간 분석 중')

    def open_map_guide(self):
        """분석 구간의 맵 → RO Navigator 관련 Guide."""
        if not self.report:
            return
        m = (self.report['window'].get('map') or '').split(',')[0].strip() or \
            (self.report['meta']['maps'][-1] if self.report['meta']['maps'] else '')
        if m:
            self.app.navigator_page.open_map(m)

    def set_build(self):
        if not self.result or not self.result.session_db_id:
            QMessageBox.information(self, '빌드 라벨', '먼저 Replay를 분석하세요.')
            return
        repo = self.app.analysis.history
        name, ok = QInputDialog.getItem(self, '빌드 라벨', '빌드 이름 (예: 소발 요르셋)', repo.builds(), editable=True)
        if not ok:
            return
        memo, ok = QInputDialog.getMultiLineText(self, '빌드 메모', '장비/카드 메모')
        if ok:
            repo.set_build(self.result.session_db_id, name.strip(), memo)
            self.app.refresh_history()
            self.app.status('빌드 라벨 저장: %s' % name)

    def export_xlsx(self):
        if not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Replay 분석 Excel', report_export.default_name(
            BASE_DIR, '리플레이분석', 'xlsx'), 'Excel (*.xlsx)')
        if path:
            self.app.safe(lambda: report_export.export_report_xlsx(path, self.report, getattr(self, '_tl_rows', None)),
                          '저장: %s' % path)

    def export_json(self):
        if not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Replay 분석 JSON', report_export.default_name(
            BASE_DIR, '리플레이분석', 'json'), 'JSON (*.json)')
        if path:
            self.app.safe(lambda: report_export.export_json(path, self.report), '저장: %s' % path)
