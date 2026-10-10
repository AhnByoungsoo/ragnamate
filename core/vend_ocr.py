"""게임 '노점 개설' 창을 화면 글자(OCR)로 읽어 줄마다 아이템 이름·판매가를 뽑고, 게임 아이템 이름으로 맞춘다.

리플레이 없이 화면만으로 동작한다. 순수 함수만 두어 OCR 없이 테스트할 수 있다.
- 창 찾기: 제목 '노점 개설' 글자 위치
- 줄 나누기: '판매가 : N' 줄을 기준으로 그 위·아래(이름이 두 줄일 수 있음)의 이름 글자를 모은다
- 이름 맞추기: OCR이 자주 틀리므로(이블커스→미불커스) 자모로 풀어 비슷한 이름을 찾는다(rapidfuzz)
"""
import re
from dataclasses import dataclass, field

TITLE = '노점개설'
# OCR이 '판매가'를 '판OH가'·'판0H가'로 읽기도 한다 → '판 ? 가'로 넓게. 줄 맨 앞의 '판매가 :' 뒤 전부를 가져온다 — 아직 가격을 안 넣은 '판매가: 0'·'판매가 :' 도, 숫자가 깨진 '3.側0.000' 도 한 줄로 본다
PRICE = re.compile(r'^[^\w]{0,2}판\S{0,3}가\s*[:：;•.]?\s*(.{0,20})$')
REAL = re.compile(r'^[^\w]{0,2}실\S{0,2}래\S{0,2}가\s*[:：;•.]?\s*(.{0,20})$')
DIALOG = ('맞습니까', '입력금액', '가격이')                          # '…의 가격이 1억 9천만Zeny 이상입니다. 입력금액이 맞습니까?'
NAME_DX = 20                     # 제목 왼쪽 끝에서 이름 칸 시작까지(아이콘 오른쪽, +23px 에 이름 글자)
SKIP = ('판매가', '실거래가', '상점명', '노점', '※', '천만', '제니를', '획득', '확인', '취소', '가져오기')
REFINE = re.compile(r'^\s*\+\s*(\d{1,2})\s*')
GRADE = re.compile(r'^\s*\[[^\]]{1,3}\]\s*')                    # [A] 같은 등급 표시


@dataclass
class VendRow:
    name_text: str                   # OCR 원문(줄 합침)
    price: int = None
    real: int = None
    y: int = 0                       # 판매가 줄의 y (창 영역 기준)
    x: int = 0                       # 판매가 줄의 x
    h: int = 12                      # 판매가 줄 높이
    refine: int = None               # +9 → 9
    match: str = ''                  # 맞춘 게임 아이템 이름
    prefix: str = ''                 # 이름 앞에 붙은 카드 접두사로 보이는 글자(예: '마술의')
    score: float = 0.0
    candidates: list = field(default_factory=list)
    alts: list = field(default_factory=list)   # 이름 칸만 잘라 3배로 다시 읽은 글자(창 전체 읽기보다 잘 읽힐 때가 많음)

    @property
    def comparable(self):
        """제련·카드 접두사가 없어 이름만으로 같은 아이템이라고 볼 수 있는 줄."""
        return self.refine is None and not self.prefix and bool(self.match)

    @property
    def equipment(self):
        """제련 수치나 카드 접두사가 붙은 장비 — 같은 제련 매물끼리만 견준다."""
        return bool(self.match) and (self.refine is not None or bool(self.prefix))


def _key(s):
    return re.sub(r'\s+', '', s or '')


def _is_title(text):
    k = re.sub(r'[^0-9A-Za-z가-힣]', '', text or '')                  # '-노점 개설' 같은 앞 기호는 뗀다
    return k.startswith('노점개') and len(k) <= 5                    # 1배로 읽으면 '노점 개 촐' 처럼 깨지기도 함. 채팅 속 '노점 개설…' 문장은 아님


def find_title(lines):
    """OCR 줄에서 '노점 개설' 제목 상자 (x, y, w, h). 없으면 None."""
    for l in lines:
        if _is_title(l.text):
            return l.box
    return None


def find_dialog(lines):
    """게임의 '천만 Zeny 이상 … 입력금액이 맞습니까?' 확인 창이 떠 있는지(패널이 가리지 않게 숨긴다)."""
    return any(k in _key(l.text) for l in lines for k in DIALOG)


def find_shop(lines, title_box=None):
    """'상점명 6시' 줄에서 내 노점 이름(시세에서 내 노점을 빼는 데 씀). 못 읽으면 ''.
    '상점명' 글자가 안 읽히면(1배 OCR에서 흔함) 제목 바로 아래 줄(10~35px)을 상점명 칸으로 본다."""
    clean = lambda s: re.sub(r'^[^0-9A-Za-z가-힣]+', '', s).strip()   # noqa: E731
    for l in lines:
        if '상점명' in l.text:
            return clean(l.text.split('상점명', 1)[1])
    if title_box:
        tx, ty = title_box[0], title_box[1]
        below = [l for l in lines if 10 <= l.box[1] - ty <= 35 and l.box[0] >= tx - 10 and not _is_title(l.text)]
        if below:
            return clean(min(below, key=lambda l: l.box[1]).text)
    elif len(lines) == 1 and not _is_title(lines[0].text):     # 상점명 칸만 잘라 읽은 경우
        return clean(lines[0].text)
    return ''


def is_own_listing(listing, my_price, my_shop):
    """공식 노점 목록의 한 줄이 지금 여는 내 노점인지. 가격(항상 정확히 읽힘)이 같고,
    상점명이 비슷하거나(작은 글자라 '6시'→'6사'처럼 틀리기도 함) 읽지 못했으면 내 노점으로 본다."""
    if listing.get('kind') != 'sale' or not my_price or listing.get('price') != my_price:
        return False
    if not my_shop:
        return True
    from rapidfuzz import fuzz
    return fuzz.ratio(jamo(my_shop), jamo(listing.get('shop') or '')) >= 50


def window_region(title_box, frame_w, frame_h):
    """제목 위치로 노점 개설 창 영역을 잡는다(여유 있게, 화면 밖은 자른다)."""
    x, y, _w, _h = title_box
    x0, y0 = max(0, x - 40), max(0, y - 12)
    return x0, y0, min(frame_w, x + 420) - x0, min(frame_h, y + 640) - y0      # 12줄 창도 '※' 줄까지


def watch_box(lines):
    """다시 읽을지 판단할 때 비교할 '창 안쪽' 상자(창 영역 기준 x0, y0, x1, y1). 창 밖 게임 배경은 늘 움직이므로 뺀다."""
    tb = find_title(lines)
    if tb is None:
        return None
    tx, ty = tb[0], tb[1]
    notes = [l.box[1] for l in lines if '※' in l.text]
    prices = [l.box[1] for l in lines if PRICE.search(l.text) or REAL.search(l.text)]
    bottom = (min(notes) + 20) if notes else ((max(prices) + 45) if prices else ty + 300)
    return max(0, tx - 30), max(0, ty - 4), tx + 380, bottom


def content_changed(prev, cur, box=None, level=40, min_pixels=5):
    """창 안쪽에서 크게 바뀐 픽셀이 min_pixels 개를 넘으면 True(숫자 한 글자 9→6 = 17픽셀). 평균으로 보면 가격 숫자 몇 글자
    바뀐 것(창의 0.1%)을 놓치므로 개수로 본다."""
    if prev is None or cur is None or prev.shape != cur.shape:
        return True
    if box:
        x0, y0, x1, y1 = box
        prev, cur = prev[y0:y1, x0:x1], cur[y0:y1, x0:x1]
    diff = abs(cur.astype('int16') - prev.astype('int16'))
    return int((diff.max(axis=2) > level).sum()) > min_pixels


def _amount(text):
    """'9,500.OOO'→9500000, '0'·''→0(가격 입력 전), 숫자가 깨졌으면('3.側0.000') None."""
    s = re.sub(r'[Oo]', '0', (text or '').strip())
    if not s:
        return 0
    if re.fullmatch(r'\d{1,3}(?:[,.\s]?\d{3})*', s):
        return int(re.sub(r'\D', '', s))
    return None


def price_from_real(real):
    """실거래가로 판매가 되짚기(판매가 숫자가 깨졌을 때). 1천만 이하면 같고, 넘으면 95% 를 되돌린다. 모호하면 None."""
    if not real:
        return None
    got = set()
    if real <= 10_000_000:
        got.add(real)
    over = [p for p in range(real * 100 // 95, real * 100 // 95 + 3) if p > 10_000_000 and real_amount(p) == real]
    if over:
        got.add(min(over))
    return got.pop() if len(got) == 1 else None


def parse_rows(lines):
    """창 영역 OCR 줄 → [VendRow] (위에서 아래 순서).

    판매가 줄 하나가 한 아이템이다(가격을 아직 안 넣은 줄도 price=0 으로 포함). 이름은 판매가 줄보다 왼쪽에 있고,
    판매가 줄 y 기준 -10 px ~ 다음 판매가 줄 사이(이름이 두 줄이면 둘째 줄 포함)에 있다.
    판매가 숫자가 깨졌으면 바로 아래 실거래가로 되짚고, 그것도 안 되면 price=None.
    """
    prices = []
    for l in lines:
        m = PRICE.search(l.text)
        if m and '※' not in l.text:
            prices.append((l.box[1], l.box[0], l.box[3], _amount(m.group(1))))
    reals = [(l.box[1], _amount(REAL.search(l.text).group(1))) for l in lines if REAL.search(l.text)]
    prices.sort()
    rows = []
    for i, (py, px, ph, price) in enumerate(prices):
        top, bottom = py - 10, (prices[i + 1][0] - 10 if i + 1 < len(prices) else py + 30)
        parts = [l for l in lines if l.box[0] < px - 20 and top <= l.box[1] < bottom
                 and not any(s in l.text for s in SKIP) and len(_key(l.text)) >= 2]
        parts.sort(key=lambda l: l.box[1])
        name = ' '.join(l.text.strip() for l in parts)
        real = next((r for ry, r in reals if 0 < ry - py <= 24), None)
        if price is None:
            price = price_from_real(real)
        row = VendRow(name, price, real, py, px, ph)
        m = REFINE.match(name)
        if m:
            row.refine = int(m.group(1))
        rows.append(row)
    return rows


def name_box(row, next_y, title_x):
    """이름 칸만 따로 읽을 상자(창 영역 기준 x0, y0, x1, y1): 아이콘 오른쪽 ~ 판매가 글자 왼쪽, 두 줄 이름까지."""
    return title_x + NAME_DX, max(0, row.y - 10), row.x - 6, min(row.y + 32, next_y - 10) if next_y else row.y + 32


def real_amount(price):
    """받는 금액(실거래가): 판매가가 1천만을 넘으면 5%를 뗀다(게임 화면 관찰: 40,000,000→38,000,000,
    10,000,000→10,000,000)."""
    if price is None:
        return None
    return price * 95 // 100 if price > 10_000_000 else price


# ---------------------------------------------------------------- 이름 맞추기

_CHO = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
_JUNG = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ'
_JONG = ' ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ'


def jamo(s):
    out = []
    for ch in _key(s):
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out += [_CHO[c // 588], _JUNG[c % 588 // 28]]
            if c % 28:
                out.append(_JONG[c % 28])
        else:
            out.append(ch.lower())
    return ''.join(out)


def clean_name(text):
    """OCR 이름에서 제련 수치·등급 표시·깨진 기호를 떼어 비교용으로."""
    s = REFINE.sub('', text or '')
    s = GRADE.sub('', s)
    s = re.sub(r'[^0-9A-Za-z가-힣()\[\]]', '', s)
    return s


class NameMatcher:
    """게임 아이템 이름 목록(item_db) 중 OCR 글자와 가장 비슷한 이름. 같은 이름은 하나로 본다."""

    def __init__(self, names, preferred=()):
        self.names = sorted({n for n in names if n})
        self.jamos = [jamo(n) for n in self.names]
        self.preferred = set(preferred)                         # 리플레이 카트 아이템 등 — 동점이면 우선

    def match(self, text, limit=3, min_score=70.0):
        """→ (이름, 점수 0~100, 후보[(이름, 점수)]) — 점수가 낮으면 이름은 ''."""
        from rapidfuzz import fuzz, process
        q = jamo(clean_name(text))
        if len(q) < 3:
            return '', 0.0, []
        got = process.extract(q, self.jamos, scorer=fuzz.ratio, limit=limit + 5)
        cands = []
        for _s, score, idx in got:
            name = self.names[idx]
            bonus = 6.0 if name in self.preferred else 0.0
            cands.append((name, min(100.0, score + bonus)))
        cands.sort(key=lambda c: -c[1])
        cands = cands[:limit]
        best, score = cands[0] if cands else ('', 0.0)
        # 1·2등이 너무 비슷하면(예: (상단)/(하단)) 확정하지 않는다
        if len(cands) > 1 and cands[0][1] - cands[1][1] < 2.0 and cands[0][0] != cands[1][0]:
            return '', score, cands
        # 읽은 글자가 이름보다 많이 짧으면(뒷부분이 잘림) 비슷한 이름 중 어느 것인지 알 수 없다
        if best and len(jamo(best)) > len(q) * 1.25:
            return '', score, cands
        return (best if score >= min_score else ''), score, cands

    def match_prefixed(self, text, max_drop=3, prefix_min_score=85.0, **kw):
        """카드가 박힌 장비는 이름 앞에 접두사가 붙는다('마술의 글레이시아 가드').

        통째로 맞지 않으면 앞 낱말을 하나씩 떼어 다시 맞춰 보고, 떼어낸 부분을 접두사로 돌려준다.
        → (이름, 점수, 후보, 접두사)
        """
        name, score, cands = self.match(text, **kw)
        if name:
            return name, score, cands, ''
        body = GRADE.sub('', REFINE.sub('', text or '')).strip()
        words = [w for w in re.split(r'\s+', body) if w]
        for drop in range(1, min(max_drop, len(words) - 1) + 1):
            rest = ' '.join(words[drop:])
            if len(_key(rest)) < 3:
                break
            name, score, cands = self.match(rest, **dict(kw, min_score=prefix_min_score))
            if name:                       # 떼어낸 앞부분은 '카드 접두사로 보임'일 뿐 확정이 아니다
                return name, score, cands, ' '.join(words[:drop])
        return '', score, cands, ''

    def best_prefixed(self, texts, **kw):
        got = [self.match_prefixed(t, **kw) for t in texts if t]
        if not got:
            return '', 0.0, [], ''
        return max(got, key=lambda g: (bool(g[0]), not g[3], g[1]))     # 접두사 없이 맞은 쪽을 먼저

    def best(self, texts, **kw):
        """여러 번 읽은 글자 중 가장 잘 맞는 결과(맞춘 것이 있으면 그중 최고점)."""
        got = [self.match(t, **kw) for t in texts if t]
        if not got:
            return '', 0.0, []
        return max(got, key=lambda g: (bool(g[0]), g[1]))
