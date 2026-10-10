"""게임 오른쪽 '퀘스트 알림창'(추적 중인 퀘스트 목록)을 화면 글자(OCR)로 읽어 Guide STEP 과 맞춘다.

알림창 모양(2026-09-27 스크린샷):
    그것을 원한다면 (3)          ← 노란 글자 = 퀘스트 이름
    그림자 간수 20체 파괴        ← 흰 글자 = 목표
      그림자 간수 ( 7 / 20 )    ← 흰 글자 + 진행도
순수 함수만 둔다(OCR·캡처 없이 테스트).
"""
import re
from dataclasses import dataclass, field

from core.vend_ocr import jamo

PROGRESS = re.compile(r'\(\s*[^\d\s]{0,2}\s*(\d{1,4})\s*/\s*(\d{1,4})')
MIN_SCORE = 80.0


@dataclass
class Objective:
    text: str
    cur: int = None
    need: int = None

    @property
    def left(self):
        return None if self.cur is None or self.need is None else max(0, self.need - self.cur)

    @property
    def done(self):
        return self.left == 0


@dataclass
class TrackedQuest:
    title: str
    objectives: list = field(default_factory=list)


def is_title_color(bgr):
    """노란 제목(B≈130, G≈200, R≈205)인지. 흰 글자는 세 값이 모두 240 이상."""
    if bgr is None:
        return False
    b, g, r = bgr
    return bool(r > 170 and g > 150 and b < min(r, g) - 45)


def line_color(img, box):
    """글자 상자 안 밝은 픽셀의 평균 색(BGR). 배경(게임 화면)은 어둡거나 섞여 있으므로 밝은 것만."""
    x, y, w, h = box
    px = img[max(0, y):y + h, max(0, x):x + w].reshape(-1, 3).astype(int)
    bright = px[px.max(axis=1) > 150]
    return tuple(bright.mean(axis=0)) if len(bright) >= 4 else None


def _clean(text):
    return re.sub(r'^[^0-9A-Za-z가-힣(]+', '', text or '').strip()


def parse(lines):
    """[(text, box, is_title)] 위에서 아래 순서 → [TrackedQuest]. 제목 전의 흰 글자(화면 속 다른 글자)는 버린다."""
    out = []
    for text, _box, title in sorted(lines, key=lambda t: (t[1][1], t[1][0])):
        text = _clean(text)
        if len(text) < 2:
            continue
        if title:
            out.append(TrackedQuest(text))
        elif out:
            m = PROGRESS.search(text)
            if m and out[-1].objectives and out[-1].objectives[-1].cur is None:
                o = out[-1].objectives[-1]                     # '그림자 간수 20체 파괴' 아래 '(7 / 20)' 줄
                o.cur, o.need = int(m.group(1)), int(m.group(2))
            else:
                o = Objective(PROGRESS.sub('', text).strip(' )'))
                if m:
                    o.cur, o.need = int(m.group(1)), int(m.group(2))
                out[-1].objectives.append(o)
    return out


def _norm(s):
    return jamo(re.sub(r'[^0-9A-Za-z가-힣]', '', s or ''))


def _pieces(step_title):
    """'그레고어와 드브리스의 추적 · 그것을 원한다면(3)' → 각 퀘스트 이름. '(이어서)', 괄호 설명은 뗀다."""
    t = re.sub(r'^\(이어서\)\s*', '', step_title)
    parts = [p.strip() for p in t.split('·')]
    return [p for p in parts if p] + [re.sub(r'\s*\([^)]*[가-힣][^)]*\)\s*$', '', p) for p in parts]


def score(quest_title, step_title):
    from rapidfuzz import fuzz
    q = _norm(quest_title)
    if len(q) < 4:
        return 0.0
    return max((fuzz.ratio(q, _norm(p)) for p in _pieces(step_title) if _norm(p)), default=0.0)


def match(quests, guides, current=None):
    """알림창 퀘스트 → (guide, step_index, quest, 점수) 또는 None.
    current=(guide_id, step_index): 지금 보고 있는 Guide 를 먼저 보고, 같은 점수면 지금 STEP 이후를 고른다.
    guides 는 자동으로 옮겨 갈 수 있는 Guide(보통 챕터)만 넘긴다 — 엉뚱한 일반 퀘스트로 넘어가지 않게."""
    cur_gid, cur_idx = current or (None, 0)
    cands = [(score(q.title, s.title), g, i, q) for q in quests for g in guides for i, s in enumerate(g.steps)]
    cands = [c for c in cands if c[0] >= MIN_SCORE]
    if not cands:
        return None
    top = max(c[0] for c in cands)
    # OCR이 '(3)'을 빠뜨리면 '그것을 원한다면'(12단계)과 '(3)'(16단계)이 비슷한 점수 → 거의 같으면 지금 Guide·지금 STEP 이후 우선
    near = [c for c in cands if c[0] >= top - 6]
    sc, g, i, q = max(near, key=lambda c: (c[1].id == cur_gid, c[1].id == cur_gid and c[2] >= cur_idx, c[0], -c[2]))
    return g, i, q, sc
