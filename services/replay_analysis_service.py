"""Replay 1회 파싱으로 창고 분석 + 전투/사냥 분석을 함께 만든다."""
import logging
import os
from dataclasses import dataclass

from analyzer.gamedata import GameDataProvider
from analyzer.normalizer import normalize
from analyzer.report import analyze
from analyzer.session import file_hash
from database.history import HistoryRepo
from services import replay_service
from services.ledger_service import LedgerService

log = logging.getLogger(__name__)
STAGES = ['Replay 읽기', 'Packet 분석 · Event 생성', '통계 계산', '시세 조회', '기록 저장', '완료']


@dataclass
class AnalysisResult:
    path: str
    file_hash: str
    parsed: object          # ParsedReplay
    report: dict
    session: object
    storage: object         # replay_service.ReplayResult (기존 창고 분석 입력)
    session_db_id: int = None


class ReplayAnalysisService:
    def __init__(self, settings, item_db, db, market, data_dir):
        self.settings = settings
        self.db = db
        self.market = market
        self.history = HistoryRepo(db)
        self.ledger = LedgerService(db, settings)
        self.gamedata = GameDataProvider(item_db, settings['ro_dir'], data_dir, db)

    def price_lookup(self, items):
        try:
            return self.market.fetch(items)[0]
        except Exception:
            logging.getLogger('market').exception('Loot 시세 조회 실패')
            return {}

    def analyze_file(self, path, stage=None):
        """stage(index, label) 콜백으로 진행 상황 전달."""
        step = (lambda i: stage(i, STAGES[i])) if stage else (lambda i: None)
        step(0)
        if not self.gamedata.items.items:
            self.gamedata.items.load()
        rp, src = replay_service.open_replay(path)
        try:
            fh = file_hash(src)
            step(1)
            pr = normalize(rp, path, experimental=bool(self.settings.get('experimental_packets')))
            storage = replay_service.build_storage_result(path, rp, pr.inventory_lists, self.gamedata.items)
        finally:
            replay_service.close_copy(path, src)
        try:                                       # 오늘의 결산용: 획득·제니 수입/지출 한 줄
            self.ledger.record(pr, path)
        except Exception:
            log.exception('결산 저장 실패')
        step(2)
        report, session, _ = analyze(pr, self.gamedata, self.settings, self._lookup_step(step), fh)
        res = AnalysisResult(path, fh, pr, report, session, storage)
        step(4)
        res.session_db_id = self.save_history(res)
        step(5)
        return res

    def _lookup_step(self, step):
        def lookup(items):
            step(3)
            return self.price_lookup(items)
        return lookup

    def reanalyze(self, res, start_ms, end_ms, label):
        report, session, _ = analyze(res.parsed, self.gamedata, self.settings, self.price_lookup, res.file_hash,
                                     start_ms, end_ms, label)
        return report

    def save_history(self, res):
        try:
            per_seg = []
            for s in res.report['segments']:
                if s['is_hunt']:
                    r, _, _ = analyze(res.parsed, self.gamedata, self.settings, self.price_lookup, res.file_hash,
                                      s['start_ms'], s['end_ms'], 'seg%d' % s['index'])
                    per_seg.append((s, r))
            sid = self.history.save(res.report, res.file_hash, per_seg)
            self._save_route(res)
            return sid
        except Exception:
            log.exception('분석 기록 저장 실패')
            return None

    def _save_route(self, res):
        """개인 이동 기록(PLAYER_MOVE/MAP_CHANGE). 공식 Guide Route와 별도 테이블."""
        pts = [(res.file_hash, e.timestamp_ms, e.map_name, e.x, e.y) for e in res.parsed.events
               if e.event_type in ('PLAYER_MOVE', 'MAP_CHANGE') and e.x is not None]
        with self.db._lock:
            self.db.conn.execute('DELETE FROM route_history WHERE file_hash=?', (res.file_hash,))
            self.db.conn.executemany('INSERT INTO route_history VALUES (?,?,?,?,?)', pts)
            self.db.conn.commit()

    def watch_candidates(self):
        folder = self.settings['replay_dir']
        return replay_service.find_latest(folder) if os.path.isdir(folder) else None
