"""공식 퀘스트 정보: 게임 클라이언트의 System/OngoingQuestInfoList_True.lub (Gravity 제공, 게임 패치 때 갱신).

퀘스트마다 제목(게임 퀘스트 알림창 제목과 같음) · 요약(알림창의 목표 줄) · 설명 · NPC 위치가 들어 있다.
- NpcNavi/NpcPosX/NpcPosY: 대화할 NPC 위치
- 설명 속 <NAVI>[이름]<INFO>맵,x,y,…</INFO></NAVI>: 가야 할 곳(돌아갈 NPC 등)
파일을 읽기만 한다(게임에 입력·메모리 접근 없음). 읽기 약 0.1초.
"""
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

NAVI = re.compile(r'<NAVI>\[([^\]]*)\]<INFO>([^,<]+),(\d+),(\d+)[^<]*</INFO></NAVI>')
COLOR = re.compile(r'\^[0-9a-fA-F]{6}')
TALK = re.compile(r'^(.+?)(?:와|과|에게|이랑|랑)\s*대화')


@dataclass
class QuestInfo:
    id: int
    title: str
    summary: str = ''
    text: str = ''                                  # 설명(태그를 뗀 글)
    npc_map: str = ''
    npc_x: int = None
    npc_y: int = None
    targets: list = field(default_factory=list)     # [(이름, 맵, x, y)] — 설명 속 길찾기

    @property
    def goal(self):
        """가야 할 곳 (이름, 맵, x, y) 또는 None. 대화할 NPC 위치가 있으면 그것, 없으면 설명 속 첫 길찾기."""
        if self.npc_map and self.npc_x is not None:
            m = TALK.match(self.summary or '')
            return (m.group(1).strip() if m else ''), self.npc_map, self.npc_x, self.npc_y
        return self.targets[0] if self.targets else None

    @property
    def navi(self):
        g = self.goal
        return '/navi %s %d/%d' % (g[1], g[2], g[3]) if g else ''


def _text(d):
    if isinstance(d, dict):
        d = ' '.join(str(d[k]) for k in sorted(d, key=lambda k: float(k)))
    s = COLOR.sub('', str(d or ''))
    s = NAVI.sub(r'\1', s)
    s = re.sub(r'<[^>]+>', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _order(key):
    """번호 열쇠는 번호대로, 글자 열쇠는 뒤로 보낸다(섞여 있어도 읽기를 멈추지 않는다)."""
    try:
        return (0, float(key), '')
    except (TypeError, ValueError):
        return (1, 0.0, str(key))


def parse_table(tbl):
    """QuestInfoList 표 → {id: QuestInfo}."""
    out = {}
    for k, v in tbl.items():
        qid = _num(k)
        if qid is None or not isinstance(v, dict):
            continue
        raw = v.get('Description')
        # 설명은 보통 {1: '…', 2: '…'} 번호 표지만, 최신 클라이언트에는 'IconName' 같은 글자 열쇠도 섞여 있다
        raw = ' '.join(str(raw[x]) for x in sorted(raw, key=_order)) if isinstance(raw, dict) else str(raw or '')
        targets = [(n.strip(), m.replace('.gat', ''), int(x), int(y)) for n, m, x, y in NAVI.findall(COLOR.sub('', raw))]
        out[qid] = QuestInfo(qid, COLOR.sub('', str(v.get('Title') or '')).strip(),
                             COLOR.sub('', str(v.get('Summary') or '')).strip(), _text(raw),
                             str(v.get('NpcNavi') or '').replace('.gat', ''), _num(v.get('NpcPosX')),
                             _num(v.get('NpcPosY')), targets)
    return out


def key(title):
    """비교용: 공백·기호를 뗀다('그것을 원한다면 (3)' = '그것을원한다면3')."""
    return re.sub(r'[^0-9A-Za-z가-힣]', '', title or '')


class QuestInfoDB:
    def __init__(self, ro_dir):
        self.path = os.path.join(ro_dir or '', 'System', 'OngoingQuestInfoList_True.lub')
        self.quests = None
        self.by_key = {}

    def load(self):
        if self.quests is not None:
            return self
        self.quests = {}
        try:
            if os.path.exists(self.path):
                import iteminfo
                with open(self.path, 'rb') as f:
                    tbl = iteminfo._run(*iteminfo._function(iteminfo._Reader(f.read())))['QuestInfoList']
                self.quests = parse_table(tbl)
        except Exception:
            log.exception('OngoingQuestInfoList 읽기 실패')
        for q in self.quests.values():
            self.by_key.setdefault(key(q.title), []).append(q)
        return self

    def find(self, title, summary='', min_score=90.0):
        """제목 → QuestInfo. OCR 오타는 자모 유사도로. 같은 제목이 여럿이면(예: '사념정화' — 아네스/세실리아 퇴치)
        알림창의 목표 줄(summary)이 가장 비슷한 것, 그다음 가야 할 곳이 있는 것, 번호가 큰 최신 것."""
        self.load()
        k = key(title)
        if len(k) < 2:
            return None
        cands = self.by_key.get(k)
        if not cands:
            from rapidfuzz import fuzz, process
            from core.vend_ocr import jamo
            if not hasattr(self, '_jamo'):
                self._keys = list(self.by_key)
                self._jamo = [jamo(x) for x in self._keys]
            got = process.extractOne(jamo(k), self._jamo, scorer=fuzz.ratio)
            if not got or got[1] < min_score:
                return None
            cands = self.by_key[self._keys[got[2]]]
        if summary and len(cands) > 1:
            from rapidfuzz import fuzz
            return max(cands, key=lambda q: (round(fuzz.ratio(key(summary), key(q.summary)) / 10), q.goal is not None, q.id))
        return max(cands, key=lambda q: (q.goal is not None, q.id))
