"""한국어 Guide 검색: 공식명/별칭/부분일치/초성/오타 허용."""
import difflib
import re

CHO = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
JUNG = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ'
JONG = ' ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ'
_PUNCT = re.compile(r'[\s\-_·:()\[\]/.,!?~\'"]+')


def normalize(s):
    return _PUNCT.sub('', (s or '').lower())


def choseong(s):
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        out.append(CHO[c // 588] if 0 <= c < 11172 else ch)
    return ''.join(out)


def jamo(s):
    """오타 비교용 자모 분해."""
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out += [CHO[c // 588], JUNG[(c % 588) // 28], JONG[c % 28].strip()]
        else:
            out.append(ch)
    return ''.join(out)


def is_choseong_query(q):
    return bool(q) and all(ch in CHO for ch in q)


class SearchEngine:
    def __init__(self, repo):
        self.repo = repo
        self.index = []

    def build(self):
        self.index = []
        for g in self.repo.all():
            names = [g.title] + list(g.aliases)
            keys = [(normalize(n), n) for n in names if n]
            self.index.append((g, keys, [choseong(k) for k, _ in keys], [jamo(k) for k, _ in keys],
                               normalize(' '.join(g.tags))))
        return self

    def search(self, query, types=None, limit=50):
        """반환: [(score, guide, matched_by)] 점수 내림차순."""
        q = normalize(query)
        if not q:
            return []
        qj = jamo(q)
        cho = is_choseong_query(q)
        res = []
        for g, keys, chos, jamos, tags in self.index:
            if types and g.type not in types:
                continue
            best, how = 0, ''
            for (k, orig), c, j in zip(keys, chos, jamos):
                if k == q:
                    s, h = 100, '이름 일치' if orig == g.title else '별칭 "%s"' % orig
                elif k.startswith(q):
                    s, h = 90, '앞부분 일치 "%s"' % orig
                elif q in k:
                    s, h = 80, '부분 일치 "%s"' % orig
                elif cho and q in c:
                    s, h = 70, '초성 "%s"' % orig
                else:
                    r = difflib.SequenceMatcher(None, qj, j).ratio() if len(q) >= 2 else 0
                    s, h = (int(40 + r * 25), '비슷한 이름 "%s"' % orig) if r >= 0.75 else (0, '')
                if s > best:
                    best, how = s, h
            if not best and q in tags:
                best, how = 60, '태그'
            if best:
                res.append((best, g, how))
        res.sort(key=lambda x: (-x[0], x[1].title))
        return res[:limit]
