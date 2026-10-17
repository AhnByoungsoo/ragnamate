"""레벨업 코치: 코스 자료(data/leveling/routes_kr.json)와 지금 레벨로 '지금 할 일·대안·다음'을 고른다.

같은 입력이면 늘 같은 결과(순수 함수). 코스의 레벨은 영상의 추천 구간이지 입장 제한이 아니다 —
구간을 벗어났다고 '못 간다'고 말하지 않는다. 자료가 없는 레벨은 비워 두고 그렇다고 말한다.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from navigator.models import navi_command

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'leveling', 'routes_kr.json')
KIND_LABEL = {'HUNT': '사냥', 'FARM': '파밍', 'QUEST': '퀘스트', 'TICKET': '티켓', 'JOB': '전직', 'CLAIM': '보상 수령',
              'PREPARE': '준비', 'GUIDE': '영상 챕터'}
CHECK_LABEL = {'sheet': '영상 전체 자막 정리표', 'seen': '영상 표 확인', 'doc': '조사 문서 · 미재확인', 'chapter': '영상 챕터'}


@dataclass
class Step:
    id: str
    min: int
    max: int
    kind: str
    title: str
    src: dict
    t: int
    check: str
    note: str = ''
    level_note: str = ''
    party: str = ''
    navi: Optional[dict] = None
    optional: bool = False                        # [선택] 단계 — 같은 구간의 다른 단계 뒤에 대안으로

    @property
    def time_label(self):
        return '%d:%02d' % divmod(self.t, 60)

    @property
    def todo(self):
        """할 일을 순서대로 나눈 목록('A → B → C')."""
        return [p.strip() for p in self.note.split(' → ') if p.strip()] if self.note else []

    @property
    def video_url(self):
        return '%s&t=%ds' % (self.src['url'], self.t)

    @property
    def navi_text(self):
        return navi_command(self.navi['map'], self.navi['x'], self.navi['y']) if self.navi else ''


@dataclass
class Advice:
    level: Optional[int]
    now: list = field(default_factory=list)       # 이 레벨에 맞는 단계들(앞이 '지금 할 일', 뒤는 대안)
    next: Optional[Step] = None                   # 다음 레벨 구간의 첫 단계
    out_of_range: bool = False                    # 코스 자료 범위(max_level) 밖


class Courses:
    def __init__(self, path=DATA, user_navi=None):
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        self.sources, self.routes = raw['sources'], {}
        shared = raw.get('navi', {})
        user_navi = user_navi or {}
        for key, route in raw['routes'].items():
            items = route['steps']
            steps = []
            for i, s in enumerate(items):
                hi = s.get('max')
                if hi is None:                          # 다음으로 높은 시작 레벨 바로 앞까지
                    later = [x['min'] for x in items if x['min'] > s['min']]
                    hi = min(later) - 1 if later else route['max_level']
                navi = user_navi.get(s['id']) or shared.get(s.get('navi') or '')
                steps.append(Step(s['id'], s['min'], hi, s['kind'], s['title'], self.sources[s['src']], s.get('t', 0),
                                  s['check'], s.get('note', ''), s.get('level_note', ''), s.get('party', ''),
                                  dict(navi) if navi else None, bool(s.get('optional'))))
            self.routes[key] = dict(title=route['title'], summary=route.get('summary', ''), prep=route.get('prep', ''),
                                    materials=route.get('materials', ''), max_level=route['max_level'], steps=steps)

    def advise(self, route, level):
        r = self.routes[route]
        if level is None:
            return Advice(None)
        if level > r['max_level']:
            return Advice(level, out_of_range=True)
        now = sorted((s for s in r['steps'] if s.min <= level <= s.max), key=lambda s: s.optional)
        later = [s for s in r['steps'] if s.min > level]
        return Advice(level, now, min(later, key=lambda s: s.min) if later else None)


def current_level(rp, manual=0, screen=None):
    """(레벨, 근거 글). 화면에서 읽은 레벨 > 리플레이 레벨업 기록 > 녹화 시작 레벨 > 직접 정한 값.

    직접 정한 값은 자동으로 알 수 없을 때만 쓴다(자동 인식을 덮어쓰지 않는다)."""
    if screen:
        return int(screen), '화면 인식'
    rp = rp or {}
    if rp.get('base_level_now'):
        return rp['base_level_now'], '리플레이 레벨업 기록'
    if rp.get('level'):
        return rp['level'], '리플레이 녹화 시작 레벨'
    if manual:
        return int(manual), '직접 입력'
    return None, ''
