"""실행된 스킬과 뒤따른 효과의 시간적 상관만 기록한다. 인과관계나 이름을 확정하지 않는다."""
import hashlib
import json
import logging
import os
import time
from collections import deque

from analyzer.events import MEASURED
from core.identifiers import numeric_id

log = logging.getLogger(__name__)
WINDOW_MS = 500
MIN_COUNT = 1


class BuffOriginLearner:
    def __init__(self, path, is_internal=None):
        self.path = path
        self.is_internal = is_internal or (lambda efst: efst == 46)
        self.contexts, self.seen, self.legacy = {}, {}, {}
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if data.get('version') == 2:
                self.contexts = data.get('contexts', {})
                self.seen = {k: set(v) for k, v in data.get('seen', {}).items()}
                self.legacy = data.get('legacy_origins', {})
            else:
                self.legacy = data.get('origins', {})
        except FileNotFoundError:
            pass
        except Exception:
            log.exception('효과 후보 파일 읽기 실패: %s', path)

    @property
    def map(self):
        return self.contexts.setdefault('unknown', {})

    def observe_events(self, events, player_aid, window_ms=WINDOW_MS, recording_id=None, context='unknown'):
        if not player_aid:
            return 0
        events = sorted(events, key=lambda e: e.timestamp_ms)
        context = context or 'unknown'
        scope = hashlib.sha256(('%s|%s|%s' % (context, player_aid, recording_id or 'unscoped')).encode()).hexdigest()
        seen = self.seen.setdefault(scope, set())
        pending, added = deque(), 0
        for e in events:
            while pending and pending[0].timestamp_ms < e.timestamp_ms - window_ms:
                pending.popleft()
            if e.event_type == 'SKILL_USE' and e.confidence == MEASURED:
                if numeric_id(e.data.get('skill')) is not None and e.data.get('success') is not False:
                    pending.append(e)
            if e.event_type != 'BUFF_START' or e.actor_id != player_aid or e.confidence != MEASURED:
                continue
            efst = numeric_id(e.data.get('efst'))
            if efst is None or self.is_internal(efst) or e.data.get('in_view'):
                continue
            nearby = [s for s in pending if s.map_name == e.map_name]
            if any(s.actor_id != player_aid and s.target_id == player_aid for s in nearby):
                continue
            own = [s for s in nearby if s.actor_id == player_aid and s.target_id in (None, 0, player_aid)]
            ids = {numeric_id(s.data['skill']) for s in own}
            if len(ids) != 1:
                continue
            best = own[-1]
            # 한 번의 스킬 뒤 같은 효과의 패킷이 여러 종류로 와도 한 번의 관측이다.
            signature = json.dumps([best.timestamp_ms, best.raw_packet, best.data['skill'], efst, e.map_name])
            fingerprint = hashlib.sha256(signature.encode()).hexdigest()
            if fingerprint in seen:
                continue
            self.add(efst, next(iter(ids)), e.timestamp_ms - best.timestamp_ms, context)
            seen.add(fingerprint)
            added += 1
        if added:
            self.save()
        return added

    def add(self, efst, skill_id, gap_ms, context='unknown'):
        origins = self.contexts.setdefault(context or 'unknown', {})
        row = origins.setdefault(str(efst), {}).setdefault(str(skill_id), dict(count=0, last_gap_ms=gap_ms))
        row['count'] += 1
        row['last_gap_ms'] = gap_ms
        row['updated'] = time.strftime('%Y-%m-%d')

    def candidates(self, efst, min_count=MIN_COUNT, context='unknown'):
        rows = self.contexts.get(context or 'unknown', {}).get(str(efst), {})
        out = [(int(sid), r.get('count', 0), r.get('last_gap_ms')) for sid, r in rows.items()
               if r.get('count', 0) >= min_count]
        return sorted(out, key=lambda r: (-r[1], r[0]))

    def top(self, efst, context='unknown'):
        cands = self.candidates(efst, context=context)
        return cands[0] if cands else None

    def suggestion(self, efst, skill_name=None, context='unknown'):
        cands = self.candidates(efst, context=context)
        if not cands:
            return None
        skill_id, count, gap = cands[0]
        name = skill_name(skill_id) if callable(skill_name) else (skill_name or '스킬 #%s' % skill_id)
        competing = ' · 다른 후보 %d개' % (len(cands) - 1) if len(cands) > 1 else ''
        return '후보: %s 사용 뒤 (%d회 관측, %.2f초 차)%s · 원인 미확정' % (name, count, gap / 1000, competing)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(dict(version=2, contexts=self.contexts, seen={k: sorted(v) for k, v in self.seen.items()},
                           legacy_origins=self.legacy,
                           note='서버별 시간적 상관 후보. 이전 관측은 보존하지만 점수에서 제외. 이름·원인 미확정.'),
                      f, ensure_ascii=False, indent=1)
        os.replace(self.path + '.tmp', self.path)
