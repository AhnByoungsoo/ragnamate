"""채팅창 감시 + 메시지 → GameEvent.

- 채팅 영역이 바뀐 경우에만 OCR (변화 감지).
- 이전 OCR 줄들과 정렬해서 '새로 추가된 줄'만 처리 (같은 줄을 여러 번 읽지 않음).
- 메시지 형식은 data/chat_patterns.json 에만 둔다(코드에 정규식 하드코딩 금지).
  실제 게임 화면 문구를 확인하기 전에는 패턴을 지어내지 않고, 사용자가 화면에서 고른 줄로 학습한다.
"""
import datetime
import difflib
import json
import logging
import os
import re

from live.events import GE, HIGH, LOW, MEDIUM, SCREEN_OCR, GameEvent, message_hash
from live.ocr import normalize_number, normalize_text
from live.vision import changed

log = logging.getLogger(__name__)


class ChatMonitor:
    def __init__(self, ocr, scale=2.0):
        self.ocr = ocr
        self.scale = scale
        self.prev_img = None
        self.prev_lines = []
        self.seq = 0

    def update(self, region_img):
        """반환: 새로 추가된 줄 [(seq, OcrLine)]"""
        if region_img is None or not changed(self.prev_img, region_img, threshold=2.0):
            return []
        self.prev_img = region_img.copy()
        lines = [l for l in self.ocr.recognize(region_img, scale=self.scale) if l.text.strip()]
        lines.sort(key=lambda l: (l.box[1], l.box[0]))
        texts = [normalize_text(l.text) for l in lines]
        k = _overlap(self.prev_lines, texts)
        new = lines[k:] if (k or not self.prev_lines) else [l for l, t in zip(lines, texts)
                                                             if not any(_same(t, p) for p in self.prev_lines)]
        self.prev_lines = texts
        out = []
        for l in new:
            self.seq += 1
            out.append((self.seq, l))
        return out


def _same(a, b):
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def _overlap(prev, cur):
    """prev의 끝부분과 cur의 앞부분이 겹치는 최대 줄 수."""
    for k in range(min(len(prev), len(cur)), 0, -1):
        if all(_same(p, c) for p, c in zip(prev[-k:], cur[:k])):
            return k
    return 0


class PatternDB:
    def __init__(self, path):
        self.path = path
        self.patterns = []
        self.load()

    def load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                self.patterns = json.load(f).get('patterns', [])
        except FileNotFoundError:
            self.patterns = []
        except Exception:
            log.exception('chat_patterns.json 읽기 실패')
            self.patterns = []
        self._compiled = []
        for p in self.patterns:
            try:
                loose = re.compile(_regex(p['template'], loose=True)) if p.get('template') else None
                self._compiled.append((p, re.compile(p['regex']), loose))
            except re.error:
                log.warning('잘못된 패턴: %s', p.get('id'))

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'version': 1, 'note': '실제 게임 화면 문구에서 학습한 패턴만 등록. verified=false는 참고용.',
                       'patterns': self.patterns}, f, ensure_ascii=False, indent=1)
        self.load()

    def match(self, text):
        """정확 일치 우선. 실패하면 고정 문구 OCR 오탈자 허용(고정 글자 80% 이상 일치) → groups['_tolerant']=True"""
        for p, rx, _ in self._compiled:
            m = rx.search(text)
            if m:
                return p, m.groupdict()
        for p, _, loose in self._compiled:
            m = loose.search(text) if loose else None
            if m and _fixed_similarity(p['template'], m) >= 0.8:
                g = m.groupdict()
                g['_tolerant'] = True
                return p, g
        return None, None

    def learn(self, line_text, event_type, item_text=None, qty_text=None, value_text=None):
        """화면에서 고른 실제 줄로 패턴 생성. 고정 문구는 그대로, 아이템/수량/값 부분만 변수로 바꾼다."""
        text = normalize_text(line_text)
        spans = []
        for part, group in ((item_text, 'item'), (qty_text, 'qty'), (value_text, 'value')):
            if part:
                part = normalize_text(part)
                i = text.find(part)
                if i < 0:
                    raise ValueError('"%s"이(가) 줄에 없습니다' % part)
                spans.append((i, i + len(part), group))
        spans.sort()
        template, pos = [], 0
        for a, b, group in spans:
            if a < pos:
                raise ValueError('변수 구간이 겹칩니다')
            if text[pos:a]:
                template.append(['fixed', text[pos:a]])
            template.append(['var', group])
            pos = b
        if text[pos:]:
            template.append(['fixed', text[pos:]])
        rx = _regex(template)
        pid = '%s_%d' % (event_type.lower(), len(self.patterns) + 1)
        self.patterns.append(dict(id=pid, event=event_type, regex=rx, template=template, example=text, verified=True,
                                  source='user_learned', created_at=datetime.date.today().isoformat()))
        self.save()
        return pid


VAR_RX = {'item': r'(?P<item>.+?)', 'qty': r'(?P<qty>[\d,OoIl]+)', 'value': r'(?P<value>[\d,OoIl.]+)'}


def _regex(template, loose=False):
    """템플릿 → 정규식. 공백은 \s*(OCR 띄어쓰기 흔들림). loose면 고정 글자를 아무 글자(.)로 허용하고
    고정 구간을 그룹으로 잡아 나중에 원문과 유사도를 확인한다."""
    out = []
    for n, (kind, val) in enumerate(template):
        if kind == 'var':
            out.append(r'\s*' + VAR_RX[val] + r'\s*')
        elif loose:
            body = ''.join(r'\s*' if ch == ' ' else '.' for ch in val)
            out.append('(?P<_f%d>%s)' % (n, body))
        else:
            out.append(''.join(r'\s*' if ch == ' ' else re.escape(ch) for ch in val))
    return ''.join(out)


def _fixed_similarity(template, m):
    want = ''.join(v.replace(' ', '') for k, v in template if k == 'fixed')
    got = ''.join((m.group('_f%d' % n) or '').replace(' ', '') for n, (k, v) in enumerate(template) if k == 'fixed')
    if not want:
        return 1.0
    return sum(1 for a, b in zip(want, got) if a == b) / len(want)


class AliasDB:
    """OCR 오인식 교정 사전 (item_ocr_alias.json / map_ocr_alias.json)."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding='utf-8') as f:
                self.map = json.load(f)
        except FileNotFoundError:
            self.map = {}
        except Exception:
            log.exception('alias 읽기 실패: %s', path)
            self.map = {}

    def get(self, text):
        return self.map.get(normalize_text(text))

    def add(self, wrong, right):
        self.map[normalize_text(wrong)] = right
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.map, f, ensure_ascii=False, indent=1)


class ItemMatcher:
    """아이템명(OCR) → item_id. 정확 일치 > OCR 별칭 > 유사도(검토 필요)."""

    def __init__(self, item_db, aliases):
        self.aliases = aliases
        self.by_name = {}
        for iid, it in (item_db.items or {}).items():
            self.by_name.setdefault(it['name'], []).append(iid)
        self.names = list(self.by_name)

    def match(self, raw):
        name = normalize_text(raw).strip('\'"[]')
        fixed = self.aliases.get(name)
        if fixed:
            name, via = fixed, 'alias'
        else:
            via = 'exact'
        ids = self.by_name.get(name)
        if ids and len(ids) == 1:
            return ids[0], name, HIGH if via == 'exact' else MEDIUM, via
        if ids:
            return None, name, LOW, 'ambiguous'          # 같은 이름 여러 개: UNKNOWN_ITEM으로 보존
        close = difflib.get_close_matches(name, self.names, n=1, cutoff=0.8)
        if close and len(self.by_name[close[0]]) == 1:
            return self.by_name[close[0]][0], close[0], LOW, 'fuzzy'
        return None, name, LOW, 'unknown'


class ChatParser:
    def __init__(self, patterns, matcher):
        self.patterns = patterns
        self.matcher = matcher

    def parse(self, seq, line, map_name=None):
        text = normalize_text(line.text)
        p, g = self.patterns.match(text)
        if not p:
            return None
        et = p['event']
        meta = dict(pattern=p['id'], text=text, line_seq=seq, ocr_conf=line.confidence, tolerant=bool(g.get('_tolerant')),
                    message_hash=message_hash('%d|%s' % (seq, text)), pattern_verified=p.get('verified', False))
        conf = MEDIUM if p.get('verified') else LOW
        if et == GE.ITEM_GAIN:
            iid, name, mconf, via = self.matcher.match(g.get('item', ''))
            qty = normalize_number(g.get('qty')) or 1
            meta.update(match=via, needs_review=mconf == LOW)
            return GameEvent(GE.ITEM_GAIN, SCREEN_OCR, min(conf, mconf, key=_rank), item_id=iid, item_name=name,
                             quantity=qty, map=map_name, metadata=meta, raw_data=line.text)
        if et in (GE.ZENY_CHANGE, GE.BASE_EXP_GAIN, GE.JOB_EXP_GAIN):
            v = normalize_number(g.get('value') or g.get('qty'))
            if v is None:
                return None
            return GameEvent(et, SCREEN_OCR, conf, value=v, map=map_name, metadata=meta, raw_data=line.text)
        return GameEvent(et, SCREEN_OCR, conf, map=map_name, metadata=meta, raw_data=line.text)


def _rank(c):
    return {HIGH: 2, MEDIUM: 1, LOW: 0}[c]
