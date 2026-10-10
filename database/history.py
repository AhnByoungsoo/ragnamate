"""Replay 분석 기록 저장소 (replay_sessions / session_segments / builds)."""
import datetime
import json
import zlib

SESSION_COLS = ['id', 'file_hash', 'filename', 'file_path', 'character', 'job', 'job_id', 'level', 'map',
                'start_time', 'duration_ms', 'window_label', 'window_ms', 'damage', 'combat_dps', 'total_dps',
                'max_hit', 'burst_1s', 'kills', 'base_exp', 'job_exp', 'base_exp_per_hour', 'zeny_net',
                'loot_value', 'consumable_cost', 'profit', 'profit_per_hour', 'top_skill', 'build_name',
                'build_memo', 'created_at']


def _pack(report):
    return zlib.compress(json.dumps(report, ensure_ascii=False, default=str).encode('utf-8'))


def _unpack(blob):
    return json.loads(zlib.decompress(blob).decode('utf-8')) if blob else None


class HistoryRepo:
    def __init__(self, db):
        self.db = db
        self.db._exec('CREATE TABLE IF NOT EXISTS hunting_window_reports (session_id INTEGER NOT NULL, '
                      'segment_idx INTEGER NOT NULL, report_json BLOB NOT NULL, PRIMARY KEY(session_id,segment_idx))')

    def find_by_hash(self, file_hash):
        r = self.db._query('SELECT id FROM replay_sessions WHERE file_hash=?', (file_hash,))
        return r[0][0] if r else None

    def save(self, report, file_hash, per_segment=None):
        """report 저장(같은 해시가 있으면 갱신, 빌드 정보는 유지). per_segment: [(seg dict, report)]"""
        m, w, cb, kpi, ex = report['meta'], report['window'], report['combat'], report['kpi'], report['exp']
        top = next((s['name'] for s in report['skills']['skills'] if s['total_damage']), None) or \
            (report['skills']['skills'][0]['name'] if report['skills']['skills'] else None)
        vals = dict(
            file_hash=file_hash, filename=m['replay_file'].replace('\\', '/').split('/')[-1],
            file_path=m['replay_file'], character=m['character'], job=m['job'], job_id=m['job_id'],
            level=m['level'], map=w.get('map'), start_time=m['start_time'], duration_ms=m['duration_ms'],
            window_label=w['label'], window_ms=w['duration_ms'],
            damage=cb['total_damage'] if cb['available'] else None, combat_dps=cb['dps_combat'],
            total_dps=cb['dps_total'], max_hit=cb['max_hit'], burst_1s=cb['burst']['1']['dps']
            if '1' in cb['burst'] else cb['burst'].get(1, {}).get('dps'),
            kills=kpi['kills'], base_exp=ex.get('base_total'), job_exp=ex.get('job_total'),
            base_exp_per_hour=ex.get('base_per_hour'), zeny_net=kpi['zeny_net'], loot_value=kpi['loot_value'],
            consumable_cost=kpi['consumable_cost'], profit=kpi['profit'], profit_per_hour=kpi['profit_per_hour'],
            top_skill=top, report_json=_pack(report),
            created_at=datetime.datetime.now().isoformat(timespec='seconds'))
        with self.db._lock:
            c = self.db.conn
            sid = self.find_by_hash(file_hash)
            if sid:
                c.execute('UPDATE replay_sessions SET %s WHERE id=?' % ','.join('%s=?' % k for k in vals),
                          list(vals.values()) + [sid])
                c.execute('DELETE FROM session_segments WHERE session_id=?', (sid,))
            else:
                cur = c.execute('INSERT INTO replay_sessions (%s) VALUES (%s)' % (','.join(vals), ','.join('?' * len(vals))),
                                list(vals.values()))
                sid = cur.lastrowid
            rows = []
            c.execute('DELETE FROM hunting_window_reports WHERE session_id=?',(sid,))
            for seg, r in (per_segment or []):
                k = r['kpi']
                rows.append((sid, seg['index'], seg['map'], seg['start_ms'], seg['end_ms'], int(seg['is_hunt']),
                             r['combat']['total_damage'] if r['combat']['available'] else None, k['kills'],
                             r['exp'].get('base_total'), r['exp'].get('job_total'), k['zeny_net'], k['loot_value'],
                             k['consumable_cost'], k['profit']))
                if seg['is_hunt']:
                    c.execute('INSERT INTO hunting_window_reports VALUES (?,?,?)',(sid,seg['index'],_pack(r)))
            c.executemany('INSERT INTO session_segments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            c.commit()
        return sid

    def list(self):
        rows = self.db._query('SELECT %s FROM replay_sessions ORDER BY start_time DESC' % ','.join(SESSION_COLS))
        return [dict(zip(SESSION_COLS, r)) for r in rows]

    def load_report(self, sid):
        r = self.db._query('SELECT report_json FROM replay_sessions WHERE id=?', (sid,))
        return _unpack(r[0][0]) if r else None

    def set_build(self, sid, name, memo):
        self.db._exec('UPDATE replay_sessions SET build_name=?, build_memo=? WHERE id=?', (name, memo, sid))
        if name:
            self.db._exec('INSERT OR IGNORE INTO builds(name, memo) VALUES (?,?)', (name, memo))

    def builds(self):
        return [r[0] for r in self.db._query('SELECT name FROM builds ORDER BY name')]

    def delete(self, sid):
        self.db._exec('DELETE FROM hunting_window_reports WHERE session_id=?',(sid,))
        self.db._exec('DELETE FROM session_segments WHERE session_id=?', (sid,))
        self.db._exec('DELETE FROM replay_sessions WHERE id=?', (sid,))

    def map_stats(self):
        """사냥 구간 기준 맵별 누적 통계."""
        rows = self.db._query("""
            SELECT map, COUNT(DISTINCT session_id), SUM(end_ms - start_ms), SUM(kills), SUM(base_exp), SUM(job_exp),
                   SUM(zeny_net), SUM(loot_value), SUM(consumable_cost), SUM(profit)
            FROM session_segments WHERE is_hunt=1 GROUP BY map ORDER BY SUM(end_ms - start_ms) DESC""")
        out = []
        for m, n, ms, k, be, je, z, lv, cc, pf in rows:
            h = (ms or 0) / 3_600_000
            out.append(dict(map=m, sessions=n, total_ms=ms, kills=k, base_exp=be, job_exp=je, zeny=z, loot_value=lv,
                            consumable_cost=cc, profit=pf,
                            exp_per_hour=be / h if h and be is not None else None,
                            zeny_per_hour=z / h if h and z is not None else None,
                            profit_per_hour=pf / h if h and pf is not None else None,
                            kills_per_hour=k / h if h and k is not None else None))
        return out

    def records(self):
        """개인 기록(Dashboard)."""
        def best(col, label):
            r = self.db._query('SELECT %s, filename, start_time FROM replay_sessions WHERE %s IS NOT NULL '
                               'ORDER BY %s DESC LIMIT 1' % (col, col, col))
            return dict(label=label, value=r[0][0], filename=r[0][1], start_time=r[0][2]) if r else \
                dict(label=label, value=None, filename=None, start_time=None)
        recs = [best('combat_dps', '최고 전투 DPS'), best('burst_1s', '최고 1초 DPS'),
                best('max_hit', '최고 단일 피해'), best('base_exp_per_hour', '최고 시간당 경험치'),
                best('profit_per_hour', '최고 시간당 수익'),
                best('CAST(kills AS REAL) * 3600000 / NULLIF(window_ms, 0)', '최다 시간당 처치')]
        r = self.db._query('SELECT map, SUM(window_ms) FROM replay_sessions WHERE map IS NOT NULL '
                           'GROUP BY map ORDER BY 2 DESC LIMIT 1')
        most_map = r[0][0] if r else None
        r = self.db._query('SELECT top_skill, COUNT(*) FROM replay_sessions WHERE top_skill IS NOT NULL '
                           'GROUP BY top_skill ORDER BY 2 DESC LIMIT 1')
        most_skill = r[0][0] if r else None
        total = self.db._query('SELECT COUNT(*), SUM(duration_ms) FROM replay_sessions')[0]
        return dict(records=recs, most_map=most_map, most_skill=most_skill, sessions=total[0],
                    total_ms=total[1] or 0)
