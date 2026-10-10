"""사냥 결산 저장: 리플레이마다 획득·제니 수입/지출을 한 줄로 남기고, 날짜별로 꺼낸다.

같은 파일 이름으로 다시 녹화해도 섞이지 않게 (파일 경로, 녹화 시작 시각)을 열쇠로 쓴다.
하루는 '녹화를 시작한 날'이다(자정을 넘긴 녹화는 시작한 날에 넣는다).
"""
import datetime
import glob
import json
import logging
import os

from analyzer.normalizer import normalize
from core import ledger as L
from services import replay_service

log = logging.getLogger(__name__)


class LedgerService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        db._exec('CREATE TABLE IF NOT EXISTS daily_ledger (path TEXT NOT NULL, recorded_at TEXT NOT NULL, '
                 'day TEXT NOT NULL, character TEXT, server TEXT, job_id INTEGER, duration_ms INTEGER, '
                 'size INTEGER, mtime REAL, data TEXT NOT NULL, updated_at TEXT NOT NULL, '
                 'PRIMARY KEY (path, recorded_at))')

    # ---------------------------------------------------------------- 저장
    def record(self, pr, path):
        """분석한 리플레이 한 개의 결산을 저장하고 그 내용을 돌려준다."""
        entry = L.from_events(pr.events, pr.player_aid)
        try:
            stat = os.stat(path)
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            size, mtime = None, None
        self.db._exec('INSERT OR REPLACE INTO daily_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      (os.path.normcase(os.path.abspath(path)), pr.recorded_at.isoformat(),
                       pr.recorded_at.date().isoformat(), pr.character, pr.server, pr.job_id, pr.duration_ms,
                       size, mtime, json.dumps(entry), datetime.datetime.now().isoformat(timespec='seconds')))
        return entry

    def _current(self, path):
        """이 파일을 이미 읽었고 그 뒤로 바뀌지 않았는가."""
        try:
            stat = os.stat(path)
        except OSError:
            return True
        rows = self.db._query('SELECT size, mtime FROM daily_ledger WHERE path=?',
                              (os.path.normcase(os.path.abspath(path)),))
        return any(size == stat.st_size and mtime == stat.st_mtime for size, mtime in rows)

    def refresh(self, day, stop=None):
        """그날 이후에 바뀐 리플레이 중 아직 안 읽은 것을 읽어 저장한다. 반환: (읽은 수, 못 읽은 파일 이름들)."""
        folder = self.settings.get('replay_dir')
        since = datetime.datetime.combine(datetime.date.fromisoformat(day), datetime.time()).timestamp()
        done, failed = 0, []
        for path in sorted(glob.glob(os.path.join(folder, '*.rrf'))) if folder and os.path.isdir(folder) else []:
            if stop is not None and stop.is_set():
                break
            try:
                if os.path.getmtime(path) < since or self._current(path):
                    continue
                rp, src = replay_service.open_replay(path)
                try:
                    pr = normalize(rp, path, experimental=bool(self.settings.get('experimental_packets')))
                finally:
                    replay_service.close_copy(path, src)
                self.record(pr, path)
                done += 1
            except Exception as exc:              # 녹화 중이거나 깨진 파일: 건너뛰고 이름만 알린다
                log.info('결산: %s 를 읽지 못함 (%s)', path, exc)
                failed.append(os.path.basename(path))
        return done, failed

    # ---------------------------------------------------------------- 조회
    def day(self, day):
        """그날 녹화한 리플레이들의 결산 [dict(path, file, recorded_at, character, …, entry)]."""
        rows = self.db._query('SELECT path, recorded_at, character, server, job_id, duration_ms, data '
                              'FROM daily_ledger WHERE day=? ORDER BY recorded_at', (day,))
        return [dict(path=path, file=os.path.basename(path), recorded_at=at, character=name, server=server,
                     job_id=job, duration_ms=ms, entry=json.loads(data))
                for path, at, name, server, job, ms, data in rows]

    def days(self):
        return [r[0] for r in self.db._query('SELECT DISTINCT day FROM daily_ledger ORDER BY day DESC')]
