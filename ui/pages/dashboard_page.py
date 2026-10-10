"""Dashboard: 최근 분석 + 개인 기록 + 사냥터 요약."""
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ui.widgets import Col, Kpi, Table, comma, hms, unit


class DashboardPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        self.latest = QLabel('최근 분석한 Replay가 없습니다. 상단의 [최신 Replay 불러오기]를 누르세요.')
        self.latest.setObjectName('replayInfo')
        self.latest.setWordWrap(True)
        v.addWidget(self.latest)
        g = QGridLayout()
        self.kpi = {}
        for i, (k, t) in enumerate([('dps', '전투 DPS'), ('kills', '처치 수'), ('exp', '시간당 경험치'),
                                    ('profit', '시간당 수익')]):
            self.kpi[k] = Kpi(t)
            g.addWidget(self.kpi[k], 0, i)
        v.addLayout(g)
        v.addWidget(QLabel('개인 기록'))
        self.rec_tbl = Table([Col('기록', lambda r: r['label'], width=160),
                              Col('값', lambda r: r['value'], unit, width=120),
                              Col('리플레이', lambda r: r['filename'], width=180),
                              Col('녹화', lambda r: (r['start_time'] or '')[:16].replace('T', ' '))])
        v.addWidget(self.rec_tbl, 1)
        self.misc = QLabel()
        v.addWidget(self.misc)
        v.addWidget(QLabel('사냥터 비교'))
        self.map_tbl = Table([Col('사냥터', lambda r: self.app.analysis.gamedata.maps.name(r['map']), width=220),
                              Col('경험치/시간', lambda r: r['exp_per_hour'], unit, width=110),
                              Col('Zeny/시간', lambda r: r['zeny_per_hour'], unit, width=110),
                              Col('수익/시간', lambda r: r['profit_per_hour'], unit, width=110),
                              Col('처치/시간', lambda r: r['kills_per_hour'], comma, width=90),
                              Col('총 시간', lambda r: r['total_ms'], hms, True)], sort_col=5)
        v.addWidget(self.map_tbl, 1)

    def set_latest(self, rep):
        m, k = rep['meta'], rep['kpi']
        self.latest.setText('<b>이번 사냥 결과</b> — %s' % '<br>'.join(rep['summary_text']))
        self.kpi['dps'].set(unit(k['combat_dps']))
        self.kpi['kills'].set(comma(k['kills']))
        self.kpi['exp'].set(unit(k['base_exp_per_hour']) if rep['exp']['available'] else '미지원')
        self.kpi['profit'].set((unit(k['profit_per_hour']) + ' Z') if k['profit_per_hour'] is not None else '-')

    def refresh(self):
        try:
            h = self.app.analysis.history
            r = h.records()
            self.rec_tbl.set_rows(r['records'])
            self.misc.setText('분석한 Replay %s개 · 누적 %s · 가장 많이 사냥한 맵: %s · 가장 많이 사용한 스킬: %s' % (
                comma(r['sessions']), hms(r['total_ms']),
                self.app.analysis.gamedata.maps.name(r['most_map']) if r['most_map'] else '-', r['most_skill'] or '-'))
            self.map_tbl.set_rows(h.map_stats())
        except Exception:
            self.misc.setText('기록을 불러오지 못했습니다 (logs 참고)')
