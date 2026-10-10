"""A dedicated SQLite writer; batched diagnostics and finalized Replay results.

Existing market/history/guide tables and user data are untouched.
Checkpoints are informational: recovery safely replays the same file from start
instead of skipping state reconstruction based on an offset alone.
"""
import json
import queue
import sqlite3
import threading
import time
from contextlib import closing


class LiveJournal:
    def __init__(self, path):
        self.path = path
        self.queue = queue.Queue(maxsize=128)
        self.error = ''
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='ReplayJournal', daemon=True)
        self.thread.start()

    def checkpoint(self, data):
        try:
            self.queue.put_nowait(('checkpoint', data))
        except queue.Full:
            self.error = 'Checkpoint queue full; next checkpoint will retry'

    def final(self, sid, path, accuracy, view):
        self.queue.put(('final', dict(session_id=sid, path=path, accuracy=accuracy, view=view, stage='FINAL')), timeout=5)

    def resumed(self, sid):
        self.queue.put(('resumed', dict(session_id=sid)), timeout=5)

    def close(self):
        self.queue.put(None, timeout=5)
        self.thread.join(10)

    def recovery(self, path=None):
        if not self.ready.is_set():
            return None
        try:
            with closing(sqlite3.connect(self.path, timeout=1)) as conn:
                if path:
                    row = conn.execute('SELECT data_json FROM live_replay_checkpoints WHERE path=?', (path,)).fetchone()
                else:
                    row = conn.execute('SELECT c.data_json FROM live_replay_checkpoints c LEFT JOIN live_accuracy_reports f '
                                       'ON c.session_id=f.session_id WHERE f.session_id IS NULL ORDER BY c.updated DESC LIMIT 1').fetchone()
                return json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError):
            return None

    def _run(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=10)) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS live_replay_checkpoints '
                             '(path TEXT PRIMARY KEY, session_id TEXT, updated REAL, data_json TEXT)')
                conn.execute('CREATE TABLE IF NOT EXISTS live_accuracy_reports '
                             '(session_id TEXT PRIMARY KEY, path TEXT, updated REAL, data_json TEXT)')
                conn.commit()
                self.ready.set()
                done = False
                while not done:
                    batch = [self.queue.get()]
                    while len(batch) < 64:
                        try:
                            batch.append(self.queue.get_nowait())
                        except queue.Empty:
                            break
                    for row in batch:
                        if row is None:
                            done = True
                            continue
                        kind, data = row
                        if kind == 'resumed':
                            previous = conn.execute('SELECT data_json FROM live_accuracy_reports WHERE session_id=?',
                                                    (data['session_id'],)).fetchone()
                            if previous:
                                saved = json.loads(previous[0])
                                saved['stage'] = 'SUPERSEDED_BY_GROWTH'
                                conn.execute('UPDATE live_accuracy_reports SET data_json=? WHERE session_id=?',
                                             (json.dumps(saved, ensure_ascii=False), data['session_id']))
                            continue
                        values = (data['path'], data['session_id'], time.time(), json.dumps(data, ensure_ascii=False))
                        if kind == 'checkpoint':
                            conn.execute('INSERT OR REPLACE INTO live_replay_checkpoints VALUES (?,?,?,?)', values)
                        else:
                            conn.execute('INSERT OR REPLACE INTO live_accuracy_reports(path,session_id,updated,data_json) '
                                         'VALUES (?,?,?,?)', values)
                    conn.commit()
        except Exception as exc:
            self.error = str(exc)
