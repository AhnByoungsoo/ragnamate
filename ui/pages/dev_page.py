"""Developer Packet Inspector: 미해석 패킷 집계 + raw hex 조회."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from analyzer.packets import packet_name, registry
from ui.widgets import Col, Table, comma, hms
from ui.completer import attach_completer, set_words

HEX_PREVIEW = 96
EFST_URL = 'https://raw.githubusercontent.com/rathena/rathena/master/src/map/status.hpp'


class DevPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.pr = None
        v = QVBoxLayout(self)
        head = QHBoxLayout()
        self.info = QLabel('Replay를 불러오면 패킷 정보가 표시됩니다.')
        head.addWidget(self.info, 1)
        for label, key in (('스킬 이름 가져오기...', 'skills'), ('버프 이름 가져오기...', 'buffs')):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self.import_names(k))
            head.addWidget(b)
        b = QPushButton('버프 이름 자동 수집(rAthena)')
        b.clicked.connect(self.collect_efst)
        head.addWidget(b)
        v.addLayout(head)
        self.names_info = QLabel()
        self.names_info.setObjectName('cardTitle')
        v.addWidget(self.names_info)
        self._update_names_info()
        sp = QSplitter(Qt.Horizontal)
        self.sum_tbl = Table([Col('Packet ID', lambda r: '0x%04x' % r['id'], width=80),
                              Col('상태', lambda r: r['status'], width=90), Col('이름', lambda r: r['name'], width=190),
                              Col('횟수', lambda r: r['count'], comma, width=70),
                              Col('길이', lambda r: r['lengths'])], sort_col=3)
        self.sum_tbl.clicked.connect(lambda idx: self.search.setText('0x%04x' % self.sum_tbl.row_at(idx)['id']))
        sp.addWidget(self.sum_tbl)
        right = QWidget()
        rv = QVBoxLayout(right)
        fl = QHBoxLayout()
        self.search = QLineEdit(placeholderText='Packet ID 검색 (예: 0x09ff)')
        self.search.textChanged.connect(self._filter)
        self.search_completer = attach_completer(self.search)
        fl.addWidget(self.search, 1)
        self.only_unknown = QCheckBox('미해석만')
        self.only_unknown.toggled.connect(self._filter)
        fl.addWidget(self.only_unknown)
        rv.addLayout(fl)
        self.raw_tbl = Table([Col('#', lambda r: r[0], comma, width=60), Col('시간', lambda r: r[1], hms, True, 80),
                              Col('Packet ID', lambda r: '0x%04x' % r[2], width=80),
                              Col('길이', lambda r: r[3], comma, width=60),
                              Col('raw hex', lambda r: r[4].hex(' ')[:HEX_PREVIEW * 3])])
        self.raw_tbl.doubleClicked.connect(self._show_full)
        rv.addWidget(self.raw_tbl, 1)
        self.full = QLabel()
        self.full.setWordWrap(True)
        self.full.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.full)
        sp.addWidget(right)
        sp.setSizes([480, 900])
        v.addWidget(sp, 1)

    # ---------------------------------------------------------------- 이름 파일
    def _update_names_info(self):
        g = self.app.analysis.gamedata
        mine = sum(1 for i in g.buffs.names if g.buffs.origin(i) == 'user')
        self.names_info.setText('스킬 이름 %d개 · 버프 이름 %d개(직접 확인 %d개) — 없는 ID는 `버프 #993`처럼 번호로 둡니다. '
                                'data.grf가 암호화 포맷이라 클라이언트에서 이름을 읽지 못해 지어내지 않습니다.'
                                % (len(g.skills.names), len(g.buffs.names), mine))

    def collect_efst(self):
        """rAthena의 공개 EFST 목록(status.hpp)에서 버프 이름을 받아온다.

        게임 클라이언트가 아니라 외부 목록이므로 이름이 다를 수 있다. 받아온 이름은 '확인 필요'로 표시하고,
        사용자가 직접 지정한 이름은 덮어쓰지 않는다."""
        if QMessageBox.question(self, '버프 이름 자동 수집',
                                'rAthena 공개 저장소의 EFST 목록(status.hpp)을 내려받아 버프 이름을 채웁니다.\n'
                                '클라이언트에서 읽은 이름이 아니라 외부 목록이라 실제와 다를 수 있어 '
                                "'확인 필요'로 표시됩니다. 진행할까요?") != QMessageBox.Yes:
            return
        buffs = self.app.analysis.gamedata.buffs

        def work(_):
            import requests
            r = requests.get(EFST_URL, timeout=30)
            r.raise_for_status()
            return buffs.import_efst(r.text)

        self.app.run(work, self._efst_done, '버프 이름 수집')

    def _efst_done(self, added):
        self._update_names_info()
        self.app.refresh_buffs()
        self.app.status('버프 이름 %d개 추가 (외부 목록 · 확인 필요). 다르면 Live 탭에서 더블클릭해 고치세요.' % added)

    def import_names(self, kind):
        """rAthena yml 또는 {"id": "이름"} JSON에서 스킬/버프 이름 가져오기."""
        path, _ = QFileDialog.getOpenFileName(self, '이름 파일 선택', '', '이름 파일 (*.yml *.yaml *.json *.txt)')
        if not path:
            return
        target = getattr(self.app.analysis.gamedata, kind)
        try:
            added = target.import_file(path)
        except Exception as e:
            QMessageBox.warning(self, '이름 가져오기 실패', str(e))
            return
        self._update_names_info()
        self.app.status('%s 이름 %d개 추가 (%s)' % ('스킬' if kind == 'skills' else '버프', added, target.path))

    def set_parsed(self, pr):
        import collections
        import struct
        self.pr = pr
        known = registry(pr.packet_ver, experimental=True)
        lengths = collections.defaultdict(set)
        rows = []
        t0 = pr.packets[0][0] if pr.packets else 0
        for i, (tm, p) in enumerate(pr.packets):
            if len(p) < 2:
                continue
            h = struct.unpack_from('<H', p)[0]
            lengths[h].add(len(p))
            rows.append((i, tm - t0, h, len(p), p))
        self._unknown = set(pr.unknown_counts)
        summary = []
        for h in lengths:
            d = known.get(h)
            status = ('미해석' if h in pr.unknown_counts else
                      '길이불일치' if pr.unsupported_counts.get(h) else
                      ('해석(검증)' if d and d.verified else '해석(미검증)'))
            if d and not d.verified and not self.app.s.get('experimental_packets'):
                status = '후보정의(꺼짐)'
            cnt = pr.unknown_counts.get(h) or pr.decoded_counts.get(h, 0) + pr.unsupported_counts.get(h, 0)
            summary.append(dict(id=h, status=status, name=packet_name(h, pr.packet_ver), count=cnt,
                                lengths=', '.join(map(str, sorted(lengths[h])[:6])) + (' …' if len(lengths[h]) > 6 else '')))
        self.sum_tbl.set_rows(summary)
        set_words(self.search_completer, ['0x%04x' % h for h in lengths])
        self.raw_tbl.set_rows(rows)
        self.info.setText('패킷 %s개 · 해석 %s종 · 미해석 %s종 %s회 (많이 등장하는 미해석 패킷부터 분석하면 효과가 큼) · '
                          '미해석 목록은 logs/unknown_packets.log에도 기록' % (
                              comma(len(rows)), len(pr.decoded_counts), len(pr.unknown_counts),
                              comma(sum(pr.unknown_counts.values()))))
        self._filter()

    def _filter(self, *_):
        q = self.search.text().strip().lower().replace('0x', '')
        try:
            pid = int(q, 16) if q else None
        except ValueError:
            pid = None
        only = self.only_unknown.isChecked()
        unk = getattr(self, '_unknown', set())
        self.raw_tbl.set_filter(lambda r: (pid is None or r[2] == pid) and (not only or r[2] in unk))

    def _show_full(self, idx):
        r = self.raw_tbl.row_at(idx)
        self.full.setText('#%d 0x%04x len=%d\n%s' % (r[0], r[2], r[3], r[4].hex(' ')))
