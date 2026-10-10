"""공식 룬미드가츠 도서관 퀘스트 목록/상세 → Guide.

- 페이지의 항목(지역/시작NPC/시작레벨/시작아이템/직업/선행퀘스트/보상)만 그대로 옮긴다. 요약·창작 없음.
- 이 페이지에는 맵 ID와 좌표가 없다. 그래서 `/navi` 명령을 만들지 않고 지역명만 남긴다(없는 좌표를 지어내지 않는다).
- 수집은 캐시(JSON)에 남겨 중간에 끊겨도 이어서 받는다.
"""
import datetime
import json
import logging
import os
import re
import time

from navigator.models import Guide, GuideStep, Requirement, Source
from navigator.providers.official_provider import page_text

log = logging.getLogger(__name__)
LIST_URL = 'https://ro.gnjoy.com/guide/runemidgarts/questlist.asp'
VIEW_URL = 'https://ro.gnjoy.com/guide/runemidgarts/questview.asp'
LABELS = ('지역', '시작NPC', '시작레벨', '시작아이템', '직업', '선행퀘스트', '보상')
ROW = re.compile(r'questview\.asp\?[^"]*QuestSeq=(\d+)"[^>]*>(.*?)</a>', re.I | re.S)
CELL = re.compile(r'<th[^>]*>\s*([^<]{2,12}?)\s*</th>\s*<td[^>]*>(.*?)</td>', re.S | re.I)
LEVEL = re.compile(r'(\d{1,3})')
ITEM_QTY = re.compile(r'^(.*?)[\s×x]*(\d{1,4})\s*개$')
NONE_TEXT = ('-', '없음', '전체', '무관', '해당없음')


def parse_list(html):
    """목록 페이지 → [(QuestSeq, 퀘스트명)]. 제목 안에 <br/> 같은 태그가 들어가는 줄도 받는다."""
    return [(int(seq), ' '.join(page_text(name)).strip()) for seq, name in ROW.findall(html)]


def parse_quest(seq, html):
    """상세 페이지의 '퀘스트 안내' 표 → {title, 지역, 시작NPC, ...}. 표를 못 찾으면 빈 dict."""
    block = html[html.find('questDetail'):] if 'questDetail' in html else html
    out = {'seq': seq}
    for label, cell in CELL.findall(block[:20000]):
        if label == '퀘스트명':
            out['title'] = ' '.join(page_text(cell)) or ''
        elif label in LABELS:
            out[label] = ' '.join(page_text(cell))
    return out if out.get('title') else {}


def _clean(text):
    text = (text or '').strip()
    return '' if text in NONE_TEXT else text


def to_guide(rec, item_lookup=None, checked_at=None):
    """수집 기록 → Guide. 확인되지 않은 값은 만들지 않는다."""
    today = checked_at or datetime.date.today().isoformat()
    title = rec.get('title', '').strip()
    if not title:
        return None
    area, npc = _clean(rec.get('지역')), _clean(rec.get('시작NPC'))
    reqs, notes = [], []
    level_text = _clean(rec.get('시작레벨'))
    if level_text:
        m = LEVEL.search(level_text)
        reqs.append(Requirement('LEVEL', '시작 레벨 %s' % level_text, min=int(m.group(1)) if m else None,
                                source='룬미드가츠 도서관 퀘스트 안내: 시작레벨 %s' % level_text))
    job_text = _clean(rec.get('직업'))
    if job_text:
        reqs.append(Requirement('JOB', '직업: %s' % job_text,
                                source='룬미드가츠 도서관 퀘스트 안내: 직업 %s' % job_text))
    prev = _clean(rec.get('선행퀘스트'))
    if prev:
        reqs.append(Requirement('QUEST', '선행 퀘스트: %s' % prev,
                                source='룬미드가츠 도서관 퀘스트 안내: 선행퀘스트 %s' % prev))
    item_text = _clean(rec.get('시작아이템'))
    if item_text:
        m = ITEM_QTY.match(item_text)
        name, qty = (m.group(1).strip(), int(m.group(2))) if m else (item_text, 1)
        item_id = item_lookup(name) if item_lookup else None
        reqs.append(Requirement('ITEM', '시작 아이템: %s' % item_text, item_id=item_id, item_name=name,
                                quantity=qty, source='룬미드가츠 도서관 퀘스트 안내: 시작아이템 %s' % item_text))
        if item_id is None:
            notes.append('시작 아이템 "%s"은 아이템 DB에서 확인하지 못했습니다.' % name)
    reward = _clean(rec.get('보상'))
    step = GuideStep(1, '시작 NPC: %s' % (npc or '확인 필요'),
                     description=('지역: %s' % area if area else '지역 정보 없음')
                                 + '\n공식 페이지에 맵 ID와 좌표가 없어 /navi 명령을 만들지 않았습니다.',
                     npc=npc, source='룬미드가츠 도서관 퀘스트 안내')
    return Guide(
        id='official_quest_%d' % rec['seq'], type='QUEST', title=title,
        description='지역: %s' % area if area else '',
        min_level=next((r.min for r in reqs if r.kind == 'LEVEL' and r.min), None),
        requirements=reqs, start_npc=npc, steps=[step],
        rewards=[reward] if reward else [],
        tags=['공식', '퀘스트'] + ([area] if area else []),
        sources=[Source('룬미드가츠 도서관 - 퀘스트 안내: %s' % title,
                        '%s?QuestSeq=%d' % (VIEW_URL, rec['seq']), 'official', today)],
        status='VERIFIED', verified_at=today,
        notes=' / '.join(notes + ['좌표 정보 없음 — 게임에서 확인 후 Guide Editor로 추가할 수 있습니다.']))


class QuestCollector:
    """목록 → 상세를 순서대로 수집. cache_path에 받은 원본 기록을 남겨 이어받기가 된다."""

    def __init__(self, cache_path, delay=0.7, session=None):
        self.cache_path = cache_path
        self.delay = delay
        self.session = session
        self.records = {}
        try:
            with open(cache_path, encoding='utf-8') as f:
                data = json.load(f)
            self.records = {int(k): v for k, v in data.get('quests', {}).items()}
        except Exception:
            self.records = {}

    def _session(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
            self.session.headers['User-Agent'] = 'Mozilla/5.0 (RagnarokPersonalAssistant guide collector)'
        return self.session

    def _get(self, url, params):
        r = self._session().get(url, params=params, timeout=20)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.text

    def list_all(self, progress=None, max_pages=200, stop_after_empty=3):
        """목록 전체 페이지 → {seq: 제목}. 빈 페이지 한 번에 멈추지 않는다(일시적 실패/빈 줄 대비)."""
        found, page, empty = {}, 1, 0
        while page <= max_pages and empty < stop_after_empty:
            try:
                rows = parse_list(self._get(LIST_URL, {'curpage': page}))
            except Exception:
                log.exception('퀘스트 목록 %d쪽 실패', page)
                rows = []
            empty = 0 if rows else empty + 1
            found.update(dict(rows))
            if progress:
                progress(len(found), 0)
            page += 1
        return found

    def collect(self, progress=None, cancel=None, refresh=False):
        """목록의 모든 퀘스트 상세를 받아 캐시에 저장. 반환: 수집한 기록 수"""
        titles = self.list_all(progress)
        todo = [s for s in sorted(titles) if refresh or s not in self.records]
        for i, seq in enumerate(todo, 1):
            if cancel is not None and cancel.is_set():
                break
            try:
                rec = parse_quest(seq, self._get(VIEW_URL, {'QuestSeq': seq}))
            except Exception:
                log.exception('퀘스트 %d 수집 실패', seq)
                continue
            if rec:
                rec.setdefault('title', titles.get(seq, ''))
                self.records[seq] = rec
            if progress:
                progress(i, len(todo))
            if i % 20 == 0:
                self.save()
        self.save()
        return len(self.records)

    def save(self):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        tmp = self.cache_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(dict(source=LIST_URL, updated_at=datetime.datetime.now().isoformat(timespec='seconds'),
                           quests={str(k): v for k, v in sorted(self.records.items())}), f,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, self.cache_path)

    def guides(self, item_lookup=None):
        out = []
        for seq in sorted(self.records):
            g = to_guide(self.records[seq], item_lookup)
            if g:
                out.append(g)
        return out
