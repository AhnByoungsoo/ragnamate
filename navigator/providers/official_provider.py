"""공식 룬미드가츠 도서관(ro.gnjoy.com) 전직 퀘스트 페이지 → Guide.

- 단계 문장은 공식 텍스트 그대로 보존한다(요약/창작 없음).
- 좌표는 '(맵ID x y)' 형식일 때만 /navi를 만든다. '(x, y)'처럼 맵이 없으면 좌표만 설명에 남긴다.
- 재료는 'OOO N개' 문구 중 아이템 DB와 이름이 정확히 일치하는 것만 ITEM 조건으로 만든다.
- 공식 페이지가 없는 직업 코드는 서버가 소드맨 페이지를 돌려주므로 제목으로 걸러낸다.
"""
import datetime
import html as htmlmod
import logging
import re
import time

from navigator.models import Guide, GuideStep, Requirement, Source

log = logging.getLogger(__name__)
URL = 'https://ro.gnjoy.com/guide/runemidgarts/popup/jobChangeQuest.asp?jobgamecode=%d'
COORD_MAP = re.compile(r'\(\s*([a-z][a-z0-9_]{2,15})\s+(\d{1,3})\s*[ ,/]\s*(\d{1,3})\s*\)')
STEP = re.compile(r'^(\d{1,2})\.\s*(.+)')
ITEM_QTY = re.compile(r'([가-힣A-Za-z0-9\'\-\[\] ]{2,30}?)["”]?\s*(\d{1,4})\s*개')
LEVELS = re.compile(r'(?:베이스 레벨|B\.Lv)\s*(\d+)', re.I)
JOBLV = re.compile(r'(?:잡 레벨|J\.Lv|Job LV|직업 레벨)\s*(\d+)', re.I)
BOTHLV = re.compile(r'B\.Lv\s*/\s*J\.Lv\s*(\d+)', re.I)
NPC_BEFORE = re.compile(r'([가-힣A-Za-z ]{2,30}?)\s*(?:NPC)?\s*\(\s*[a-z][a-z0-9_]{2,15}\s+\d')
QUOTED = re.compile(r'["“”]([^"“”]{1,20})["“”]')
ZENY = re.compile(r'(\d{1,3}(?:,\d{3})+|\d{3,})\s*(?:z|Z|제니|zeny)(?![a-zA-Z])')
OPTIONAL_HINTS = ('랜덤', '중 하나', '면제')
GIVEN_HINTS = ('지급', '줍니다', '받을 수', '받게', '건네')   # NPC가 주는 아이템(준비물 아님)


def page_text(raw_html):
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', raw_html, flags=re.S | re.I)
    t = re.sub(r'(?i)<br\s*/?>', '\n', t)
    t = re.sub(r'(?i)</(p|li|tr|h\d|div|dt|dd|td|th)>', '\n', t)
    t = htmlmod.unescape(re.sub('<[^>]+>', '', t)).replace('\xa0', ' ')
    return [re.sub(r'\s+', ' ', l).strip() for l in t.split('\n') if l.strip()]


def parse_job_page(job_id, raw_html, item_lookup, job_tree, checked_at=None):
    """반환: Guide 또는 None(공식 페이지 없음)."""
    lines = page_text(raw_html)
    title_line = next((l for l in lines if l.endswith('전직 퀘스트')), None)
    node = job_tree.get(job_id)
    if not title_line or not node:
        return None
    job_name = title_line[:-len('전직 퀘스트')].strip()
    if not node.get('official_title') or node['official_title'] != job_name:
        return None          # 공식 페이지가 없는 직업(서버가 다른 직업 페이지를 대신 돌려줌)
    body = lines[lines.index(title_line) + 1:]
    body = body[:next((i for i, l in enumerate(body) if l.startswith('※홈페이지') or '(주) 그라비티' in l),
                      len(body))]
    intro, steps, cur = [], [], None
    for l in body:
        m = STEP.match(l)
        if m:
            cur = GuideStep(order=int(m.group(1)), title=_short(m.group(2)), description=m.group(2),
                            source='official')
            steps.append(cur)
        elif cur is not None:
            cur.description += '\n' + l
        else:
            intro.append(l)
    for s in steps:
        m = COORD_MAP.search(s.description)
        if m:
            s.map, s.x, s.y = m.group(1), int(m.group(2)), int(m.group(3))
        s.npc = _npc_name(s.description)
        seen = set()
        for line in s.description.split('\n'):
            optional = any(k in line for k in OPTIONAL_HINTS)
            given = any(k in line for k in GIVEN_HINTS)
            for name, qty in ([] if given else ITEM_QTY.findall(line)):
                name = name.strip().strip('"“”')
                iid = item_lookup(name)
                if iid is None and ' ' in name:         # '… 천조각' 처럼 앞 문장이 붙은 경우 마지막 단어로 재시도
                    name = name.split(' ')[-1]
                    iid = item_lookup(name)
                if iid is not None and iid not in seen:
                    seen.add(iid)
                    s.requirements.append(Requirement(
                        'ITEM', item_id=iid, item_name=name, quantity=int(qty), optional=optional,
                        source='공식 텍스트: "%s"' % line[:120]))
            for z in ZENY.findall(line):
                s.requirements.append(Requirement('ZENY', '%s Zeny' % z, min=int(z.replace(',', '')),
                                                  optional=optional, source='공식 텍스트: "%s"' % line[:120]))
    intro_text = '\n'.join(intro)
    reqs = []
    both = BOTHLV.search(intro_text)
    lv, jl = (both, both) if both else (LEVELS.search(intro_text), JOBLV.search(intro_text))
    if lv:
        reqs.append(Requirement('LEVEL', '베이스 레벨 %s 이상' % lv.group(1), min=int(lv.group(1)),
                                source='공식: ' + intro[0][:80]))
    if jl:
        reqs.append(Requirement('JOB_LEVEL', '잡 레벨 %s 이상' % jl.group(1), min=int(jl.group(1)),
                                source='공식: ' + intro[0][:80]))
    for l in intro:
        if '먼저' in l and ('퀘스트' in l or '진행' in l):
            reqs.append(Requirement('QUEST', l.lstrip('※ ').strip(), source='공식: ' + l[:80]))
    parents = node.get('parents', [])
    if parents:
        reqs.append(Requirement('JOB', '현재 직업: ' + ', '.join(job_tree[p]['name'] for p in parents if p in job_tree),
                                jobs=list(parents), source='직업 트리(job_tree.json) + 공식 조건 문구'))
    first = next((s for s in steps if s.map), None)
    today = checked_at or datetime.date.today().isoformat()
    return Guide(
        id='job_change_%d' % job_id, type='JOB_CHANGE', title='%s 전직' % node['name'],
        aliases=_aliases(job_name, node), description=intro_text,
        min_level=int(lv.group(1)) if lv else None, required_jobs=list(parents), next_jobs=[job_id],
        requirements=reqs, start_map=first.map if first else '', start_x=first.x if first else None,
        start_y=first.y if first else None, start_npc=first.npc if first else '', steps=steps,
        rewards=['%s(으)로 전직' % node['name']], repeat_type='ONCE',
        related_maps=sorted({s.map for s in steps if s.map}), tags=['전직', node['name']],
        sources=[Source('룬미드가츠 도서관 - %s' % title_line, URL % job_id, 'official', today)],
        status='VERIFIED', verified_at=today, game_version='kRO',
        notes='공식 홈페이지 전직 안내 원문. 게임 업데이트로 달라질 수 있음(공식 사이트 고지).')


def _npc_name(text):
    """'…프론테라의 여관 종업원 NPC(prt_in 250 129)' → '여관 종업원'. 없으면 따옴표 속 첫 이름."""
    m = NPC_BEFORE.search(text)
    if m:
        name = re.split(r'의\s|에서\s|에게\s', m.group(1).strip())[-1].strip()
        if name and not name.endswith(('마을', '정원', '지역', '필드')):
            return name
    q = QUOTED.search(text)
    return q.group(1).strip() if q else ''


def _short(text):
    t = re.split(r'(?<=[.!?])\s', text)[0]
    return t if len(t) <= 60 else t[:57] + '…'


def _aliases(name, node):
    out = {name, name.replace(' ', ''), name + '전직', name.replace(' ', '') + '전직', node['name'],
           node['name'].replace(' ', '')}
    out.update(node.get('aliases', []))
    return sorted(a for a in out if a)


def fetch_job_pages(job_ids, delay=1.0, session=None):
    """공식 페이지 HTML을 가져온다(요청 간격 제한). {job_id: html}"""
    import requests
    s = session or requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0'
    out = {}
    for jid in job_ids:
        try:
            r = s.get(URL % jid, timeout=20)
            r.raise_for_status()
            out[jid] = r.content.decode('utf-8', 'replace')
        except Exception:
            log.exception('공식 전직 페이지 조회 실패: %s', jid)
        time.sleep(delay)
    return out
