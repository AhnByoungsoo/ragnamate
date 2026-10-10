"""Guide 보상 문구 해석: 공식 퀘스트 안내의 '경험치 : 5,000 직업경험치 : 2,500 기타 : …'를 나눠 보여 주고,
보상 아이템으로 콘텐츠를 찾을 수 있게 한다. 형식이 다르면(예: '소드맨(으)로 전직') 문구 전체를 기타로 둔다."""
import re
from dataclasses import dataclass, field

_PAT = re.compile(r'경험치\s*:\s*(?P<exp>.*?)\s*직업경험치\s*:\s*(?P<job>.*?)\s*기타\s*:\s*(?P<etc>.*)$', re.S)


@dataclass
class Rewards:
    exp: str = ''                  # '5,000' (없음이면 빈 값)
    job_exp: str = ''
    items: list = field(default_factory=list)     # ['TE 화이트 슬림 포션 5개'] 또는 ['소드맨(으)로 전직']

    def summary(self):
        parts = []
        if self.exp:
            parts.append('EXP %s' % self.exp)
        if self.job_exp:
            parts.append('Job %s' % self.job_exp)
        parts += self.items
        return ' · '.join(parts)


def _clean(v):
    v = (v or '').strip()
    return '' if v in ('', '없음', '-') else v


def parse_rewards(guide):
    r = Rewards()
    for text in guide.rewards or []:
        m = _PAT.search(text)
        if not m:
            if text.strip():
                r.items.append(text.strip())
            continue
        r.exp = r.exp or _clean(m.group('exp'))
        r.job_exp = r.job_exp or _clean(m.group('job'))
        etc = re.sub(r'^없음\s*', '', m.group('etc').strip())      # '기타 : 없음 TE 포션 5개' 형태
        if _clean(etc):
            r.items.append(etc)
    return r


def matches_item(guide, query):
    """보상(기타) 또는 전략 드랍 목록에 query가 들어 있는지. 띄어쓰기 무시."""
    q = re.sub(r'\s+', '', query or '').lower()
    if not q:
        return True
    texts = parse_rewards(guide).items + [str(x) for x in (guide.strategy or {}).get('drops', [])]
    return any(q in re.sub(r'\s+', '', t).lower() for t in texts)
