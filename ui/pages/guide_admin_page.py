"""Guide 관리 (Developer Mode): 검사 · Import/Export · 별칭 CSV · 검색 실패 로그 · 공식 전직 가이드 갱신."""
import json
import os

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget
from PySide6.QtCore import Qt

from config.settings import BASE_DIR
from navigator.importers.csv_importer import import_aliases
from navigator.providers.local_provider import list_packs
from navigator.validation import validate
from ui.widgets import Col, Table, comma


class GuideAdminPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.svc = app.guides
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        for text, slot in (('Guide 검사', self.run_validate), ('새 Guide', lambda: self.app.edit_guide(None)),
                           ('Guide Pack 가져오기', self.import_pack), ('전체 내보내기', self.export_pack),
                           ('별칭 CSV 가져오기', self.import_alias), ('다시 로드', self.reload),
                           ('공식 전직 가이드 갱신', self.refresh_official),
                           ('공식 퀘스트 전체 수집', self.collect_quests)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.info = QLabel()
        v.addWidget(self.info)
        sp = QSplitter(Qt.Vertical)
        self.issues = Table([Col('심각도', lambda r: r[0], width=70), Col('Guide', lambda r: r[1], width=220),
                             Col('내용', lambda r: r[2])], sort_col=None)
        self.issues.doubleClicked.connect(lambda idx: self._open(self.issues.row_at(idx)[1]))
        sp.addWidget(self.issues)
        low = QSplitter(Qt.Horizontal)
        self.packs = Table([Col('Pack', lambda r: r['pack'], width=160), Col('Guide 수', lambda r: r['guides'], comma, width=70),
                            Col('생성', lambda r: r['generated_at'] or '', width=150), Col('파일', lambda r: r['file'])])
        low.addWidget(self.packs)
        self.misses = Table([Col('검색 실패 키워드', lambda r: r[0], width=200), Col('횟수', lambda r: r[1], comma, width=60),
                             Col('마지막', lambda r: r[2])], sort_col=1)
        low.addWidget(self.misses)
        sp.addWidget(low)
        v.addWidget(sp, 1)
        self.refresh()

    def refresh(self):
        self.packs.set_rows(list_packs(self.svc.repo.dir))
        self.misses.set_rows(self.svc.repo.search_misses())
        self.info.setText('Guide %d개 · 직업 %d개 · 로드 오류 %d건%s' % (
            len(self.svc.repo.guides), len(self.svc.repo.jobs), len(self.svc.repo.load_errors),
            (' — ' + '; '.join(self.svc.repo.load_errors[:3])) if self.svc.repo.load_errors else ''))

    def run_validate(self):
        if not self.app.item_db.items:
            self.app.item_db.load()
        res = validate(self.svc.repo, self.app.item_db, self.app.analysis.gamedata.maps)
        order = {'ERROR': 0, 'WARN': 1, 'INFO': 2}
        self.issues.set_rows(sorted(res, key=lambda r: (order.get(r[0], 3), r[1])))
        self.app.status('Guide 검사: 오류 %d · 경고 %d · 정보 %d' % tuple(sum(1 for r in res if r[0] == k)
                                                                     for k in ('ERROR', 'WARN', 'INFO')))

    def _open(self, gid):
        if self.svc.repo.get(gid):
            self.app.show_navigator()
            self.app.navigator_page.open_guide(gid)

    def import_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Guide Pack(JSON)', BASE_DIR, 'JSON (*.json)')
        if path:
            try:
                n = self.svc.repo.import_pack(path)
                self.reload()
                self.app.status('Guide %d개 가져옴' % n)
            except Exception as e:
                QMessageBox.warning(self, '가져오기 실패', str(e))

    def export_pack(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Guide Pack 내보내기', os.path.join(BASE_DIR, 'ro_guides_kr.json'),
                                              'JSON (*.json)')
        if path:
            self.app.safe(lambda: self.svc.repo.export_pack(path, name='ro_guides_kr'), '내보냄: %s' % path)

    def import_alias(self):
        path, _ = QFileDialog.getOpenFileName(self, '별칭 CSV (guide_id,별칭,별칭...)', BASE_DIR, 'CSV (*.csv)')
        if path:
            try:
                n = import_aliases(self.svc.repo, path)
                self.svc.search_engine.build()
                self.app.status('별칭 %d개 추가' % n)
            except Exception as e:
                QMessageBox.warning(self, '별칭 가져오기 실패', str(e))

    def reload(self):
        self.svc.reload()
        from navigator.importers.csv_importer import apply_aliases
        apply_aliases(self.svc.repo)
        self.svc.search_engine.build()
        self.refresh()
        self.app.navigator_page.refresh_home()

    def collect_quests(self):
        """룬미드가츠 도서관 퀘스트 목록/상세를 전부 받아 official_quests.json 생성(이어받기 가능)."""
        if QMessageBox.question(self, '공식 퀘스트 전체 수집',
                                '공식 홈페이지의 퀘스트 목록과 상세를 0.7초 간격으로 모두 받아옵니다(수백 건, 몇 분 소요).\n'
                                '이미 받은 것은 건너뜁니다. 진행할까요?') != QMessageBox.Yes:
            return
        from navigator.providers.official_quests import QuestCollector
        repo, items = self.svc.repo, self.app.item_db

        def work(progress):
            col = QuestCollector(os.path.join(repo.dir, 'official_quests_cache.json'))
            col.collect(progress=progress)
            by_name = {}
            for i, it in items.items.items():
                by_name.setdefault(it['name'], []).append(i)
            lookup = lambda n: by_name[n][0] if len(by_name.get(n, [])) == 1 else None
            guides = [g.to_dict() for g in col.guides(lookup)]
            path = os.path.join(repo.dir, 'official_quests.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(dict(pack='official_quests', version=1, guides=guides), f, ensure_ascii=False, indent=1)
            return len(guides)

        self.app.run(work, lambda n: (self.reload(), self.app.status('공식 퀘스트 %d개 저장' % n)), '공식 퀘스트 수집')

    def refresh_official(self):
        """공식 룬미드가츠 도서관에서 전직 안내를 다시 받아 official_job_changes.json 재생성(요청 간격 1초)."""
        if QMessageBox.question(self, '공식 전직 가이드 갱신',
                                '공식 홈페이지에서 전직 퀘스트 페이지 %d개를 1초 간격으로 가져옵니다. 진행할까요?'
                                % len(self.svc.repo.jobs)) != QMessageBox.Yes:
            return
        from navigator.providers.official_provider import fetch_job_pages, parse_job_page
        repo, items = self.svc.repo, self.app.item_db

        def work(_):
            by_name = {}
            for i, it in items.items.items():
                by_name.setdefault(it['name'], []).append(i)
            lookup = lambda n: by_name[n][0] if len(by_name.get(n, [])) == 1 else None
            ids = [j for j, n in repo.jobs.items() if n.get('official_title')]
            pages = fetch_job_pages(ids)
            guides = [g.to_dict() for jid, h in pages.items() if (g := parse_job_page(jid, h, lookup, repo.jobs))]
            path = os.path.join(repo.dir, 'official_job_changes.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(dict(pack='official_job_changes', version=1, guides=guides), f, ensure_ascii=False, indent=1)
            return len(guides)
        self.app.run(work, lambda n: (self.reload(), self.app.status('공식 전직 가이드 %d개 갱신' % n)), '공식 전직 가이드 갱신')
