"""사냥터 속성표: 공식 홈페이지 출현 지역 기준으로 맵마다 평균적으로 어떤 속성으로 공격하면 좋은지."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSplitter,
                               QVBoxLayout, QWidget, QTabWidget)

from core import elements
from ui.theme import CARD_LIST_QSS, T
from ui.widgets import Col, Table, comma
from ui.completer import attach_completer

MAP_ROLE = Qt.UserRole + 1


class ElementMapDialog(QDialog):
    def __init__(self, parent=None, current_map_name=''):
        super().__init__(parent)
        self.setWindowTitle('사냥터 속성표 — 맵별 추천 공격 속성')
        self.resize(1100, 680)
        self.adv = elements.shared()
        outer=QVBoxLayout(self);tabs=QTabWidget();outer.addWidget(tabs)
        content=QWidget();tabs.addTab(content,'추천 공격 속성');v=QVBoxLayout(content)
        note = QLabel('공식 홈페이지 몬스터 정보의 출현 지역으로 맵마다 몬스터를 모아, 일반 몬스터(보스 제외)에게 평균적으로 '
                      '가장 강한 공격 속성을 계산합니다. 실제로 사냥한 기록이 있으면 리플레이 분석의 몬스터 탭이 더 정확합니다.')
        note.setWordWrap(True)
        note.setObjectName('cardTitle')
        v.addWidget(note)
        self.search = QLineEdit(placeholderText='맵 이름 검색  예) 글레스트헤임, 오딘, 수로')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)

        sp = QSplitter(Qt.Horizontal)
        self.maps = QListWidget()
        self.maps.setStyleSheet(CARD_LIST_QSS)
        self.maps.currentItemChanged.connect(lambda cur, _: self._show(cur.data(MAP_ROLE) if cur else None))
        sp.addWidget(self.maps)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setStyleSheet('font-size: 18px; font-weight: bold;')
        rv.addWidget(self.title)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet('background:%s; border:1px solid %s; border-radius:8px; padding:8px 10px;'
                                   % (T['surface2'], T['accent']))
        rv.addWidget(self.summary)
        self.mob_tbl = Table([
            Col('몬스터', lambda m: m['name'], width=170), Col('구분', lambda m: m.get('grade') or '', width=60),
            Col('레벨', lambda m: m.get('level'), comma, width=60),
            Col('속성', lambda m: '%s%d' % (elements.ELEMENT_KO[m['element']], m['element_level']), width=70),
            Col('종족', lambda m: elements.RACE_KO.get(m.get('race'), m.get('race')), width=80),
            Col('크기', lambda m: elements.SIZE_KO.get(m.get('size'), m.get('size')), width=60),
            Col('HP', lambda m: m.get('hp'), comma)], sort_col=2, desc=False)
        rv.addWidget(self.mob_tbl, 1)
        sp.addWidget(right)
        sp.setSizes([380, 720])
        v.addWidget(sp, 1)

        self._rows = self._build_rows()
        attach_completer(self.search, [r['name'] for r in self._rows])
        self._fill(self._rows)
        if current_map_name:
            self.search.setText(current_map_name)
            if not any(r['name'].replace(' ', '') == current_map_name.replace(' ', '') for r in self._rows):
                self.search.clear()                 # 현재 맵이 홈페이지 출현 지역에 없으면 전체 목록
                self.search.setPlaceholderText('현재 맵(%s)은 홈페이지 출현 지역에 없습니다 — 맵 이름 검색'
                                               % current_map_name)
        if self.maps.count():
            self.maps.setCurrentRow(0)
        app=getattr(parent,'app',parent)
        if getattr(app,'economy',None):
            page=app.economy.profit_page(tabs);tabs.addTab(page,'사냥터 수익')
            tabs.currentChanged.connect(lambda index:page.reload() if index==1 else None)
            self.finished.connect(lambda _:page.task.close())

    def _build_rows(self):
        rows = []
        for key, mobs in self.adv.official['by_map'].items():
            name = self.adv.official['map_names'][key]
            rec = self.adv.recommend(self.adv.map_monsters(name))
            best = rec['elements'][0] if rec['available'] else None
            rows.append(dict(name=name, count=len({m['aegis'] for m in mobs}), best=best))
        return sorted(rows, key=lambda r: r['name'])

    def _fill(self, rows):
        self.maps.clear()
        for r in rows:
            best = ('추천 %s %d%%' % (elements.ELEMENT_KO[r['best']['element']], round(r['best']['avg']))
                    if r['best'] else '보스만')
            it = QListWidgetItem('%s\n  %d종 · %s' % (r['name'], r['count'], best))
            it.setData(MAP_ROLE, r['name'])
            self.maps.addItem(it)
        if not rows:
            self.maps.addItem(QListWidgetItem('검색 결과 없음'))

    def _filter(self, text):
        q = text.replace(' ', '')
        self._fill([r for r in self._rows if q in r['name'].replace(' ', '')])
        if self.maps.count():
            self.maps.setCurrentRow(0)

    def _show(self, name):
        if not name:
            self.title.setText('')
            self.summary.setText('')
            self.mob_tbl.set_rows([])
            return
        self.title.setText(name)
        rec = self.adv.recommend(self.adv.map_monsters(name))
        self.summary.setText(elements.summary_text(rec, basis='공식 출현 목록'))
        self.mob_tbl.set_rows(list({m['aegis']: m for m in self.adv.official['by_map'].get(name.replace(' ', ''), [])}
                                   .values()))
