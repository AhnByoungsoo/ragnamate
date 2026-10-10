"""Guide 저장소: JSON Guide Pack 로드 + 사용자 상태(SQLite).

Guide Pack 로드 순서(뒤가 앞을 덮어씀):
  data/guides/*.json (seed/official) → data/guides/packs/*.json (공유 Guide Pack, 예: ro_guides_kr.json)
  → data/guides/user/*.json (Guide Editor에서 저장한 사용자 Guide)
앱 업데이트와 Guide 데이터 업데이트가 분리된다.
"""
import datetime
import glob
import json
import logging
import os

from navigator.models import Guide, Q_UNKNOWN

log = logging.getLogger(__name__)
SKIP_FILES = {'job_tree.json'}


def now():
    return datetime.datetime.now().isoformat(timespec='seconds')


class GuideRepository:
    def __init__(self, guide_dir, db=None):
        self.dir = guide_dir
        self.db = db
        self.guides = {}
        self.jobs = {}
        self.load_errors = []

    # ---------------------------------------------------------------- 로드
    def load(self):
        self.guides, self.load_errors = {}, []
        files = [f for f in sorted(glob.glob(os.path.join(self.dir, '*.json'))) if os.path.basename(f) not in SKIP_FILES]
        files += sorted(glob.glob(os.path.join(self.dir, 'packs', '*.json')))
        files += sorted(glob.glob(os.path.join(self.dir, 'user', '*.json')))
        for f in files:
            self._load_pack(f)
        self._load_jobs()
        return self

    def _load_pack(self, path):
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            name = data.get('pack') or os.path.splitext(os.path.basename(path))[0]
            for d in data.get('guides', []):
                try:
                    g = Guide.from_dict(d, pack=name)
                    self.guides[g.id] = g
                except Exception as e:
                    self.load_errors.append('%s: %s (%s)' % (os.path.basename(path), d.get('id'), e))
        except Exception as e:
            log.exception('Guide Pack 로드 실패: %s', path)
            self.load_errors.append('%s: %s' % (os.path.basename(path), e))

    def _load_jobs(self):
        path = os.path.join(self.dir, 'job_tree.json')
        try:
            with open(path, encoding='utf-8') as fh:
                self.jobs = {j['id']: j for j in json.load(fh)['jobs']}
        except Exception:
            log.exception('job_tree.json 로드 실패')
            self.jobs = {}

    def get(self, gid):
        return self.guides.get(gid)

    def all(self):
        return list(self.guides.values())

    # ---------------------------------------------------------------- 사용자 Guide 저장/Import/Export
    def save_user_guide(self, guide, filename='user_guides.json'):
        path = os.path.join(self.dir, 'user', filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {'pack': 'user', 'version': 1, 'guides': []}
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        data['guides'] = [g for g in data.get('guides', []) if g.get('id') != guide.id] + [guide.to_dict()]
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        guide.pack = 'user'
        self.guides[guide.id] = guide

    def export_pack(self, path, guide_ids=None, name='exported'):
        guides = [g.to_dict() for g in self.all() if guide_ids is None or g.id in guide_ids]
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'pack': name, 'version': 1, 'exported_at': now(), 'guides': guides}, fh,
                      ensure_ascii=False, indent=1)
        return len(guides)

    def import_pack(self, path):
        """외부 Guide Pack을 data/guides/packs/ 로 복사하고 다시 로드. 반환: 가져온 Guide 수"""
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        guides = data.get('guides')
        if not isinstance(guides, list):
            raise ValueError('Guide Pack 형식이 아닙니다 (guides 배열 없음)')
        for d in guides:
            Guide.from_dict(d)                     # 형식 검사
        dst = os.path.join(self.dir, 'packs', os.path.basename(path))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        self.load()
        return len(guides)

    # ---------------------------------------------------------------- 사용자 상태(SQLite)
    def _q(self, sql, args=()):
        return self.db._query(sql, args) if self.db else []

    def _x(self, sql, args=()):
        if self.db:
            self.db._exec(sql, args)

    def progress(self, character, gid):
        r = self._q('SELECT current_step, done_steps FROM guide_progress WHERE character=? AND guide_id=?',
                    (character or '', gid))
        return (r[0][0], set(json.loads(r[0][1]))) if r else (0, set())

    def set_progress(self, character, gid, current, done):
        self._x('INSERT OR REPLACE INTO guide_progress VALUES (?,?,?,?,?)',
                (character or '', gid, current, json.dumps(sorted(done)), now()))

    def quest_state(self, character, gid):
        r = self._q('SELECT state FROM quest_states WHERE character=? AND guide_id=?', (character or '', gid))
        return r[0][0] if r else Q_UNKNOWN

    def set_quest_state(self, character, gid, state, source='manual'):
        self._x('INSERT OR REPLACE INTO quest_states VALUES (?,?,?,?,?)', (character or '', gid, state, source, now()))

    def favorites(self):
        return [r[0] for r in self._q('SELECT guide_id FROM guide_favorites ORDER BY created_at DESC')]

    def toggle_favorite(self, gid):
        if gid in self.favorites():
            self._x('DELETE FROM guide_favorites WHERE guide_id=?', (gid,))
            return False
        self._x('INSERT OR REPLACE INTO guide_favorites VALUES (?,?)', (gid, now()))
        return True

    def add_history(self, gid):
        self._x('INSERT INTO guide_history(guide_id, viewed_at) VALUES (?,?)', (gid, now()))

    def recent(self, n=10):
        rows = self._q('SELECT guide_id, MAX(viewed_at) m FROM guide_history GROUP BY guide_id ORDER BY m DESC LIMIT ?',
                       (n,))
        return [r[0] for r in rows]

    def log_miss(self, query):
        q = (query or '').strip()
        if q:
            self._x('INSERT INTO guide_search_misses VALUES (?,1,?) ON CONFLICT(query) DO UPDATE SET '
                    'count=count+1, last_at=excluded.last_at', (q, now()))

    def search_misses(self):
        return self._q('SELECT query, count, last_at FROM guide_search_misses ORDER BY count DESC, last_at DESC')
