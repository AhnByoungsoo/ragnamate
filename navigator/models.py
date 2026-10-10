"""RO Navigator 데이터 모델. Guide 내용은 코드가 아니라 JSON Guide Pack에 둔다."""
from dataclasses import asdict, dataclass, field
from typing import Optional

GUIDE_TYPES = {
    'QUEST': '퀘스트', 'DUNGEON': '던전', 'INSTANCE': '인스턴스', 'JOB_CHANGE': '전직', 'EPISODE': '에피소드', 'CHAPTER': '챕터',
    'CRAFT': '제작', 'ENCHANT': '인챈트', 'EQUIPMENT': '장비', 'HUNT': '사냥터', 'FARMING': '파밍',
    'DAILY': '일일', 'WEEKLY': '주간', 'SYSTEM': '시스템', 'NPC': 'NPC', 'MAP': '맵',
}
REQ_KINDS = ('LEVEL', 'JOB_LEVEL', 'JOB', 'QUEST', 'ITEM', 'ZENY', 'EPISODE', 'COOLDOWN', 'PARTY', 'EQUIPMENT',
             'STATUS', 'OTHER')
PASS, FAIL, UNKNOWN = 'PASS', 'FAIL', 'UNKNOWN'
RESULT_ICON = {PASS: '✅', FAIL: '❌', UNKNOWN: '?'}

# Guide 상태 / 출처 신뢰도
STATUS = {'VERIFIED': '검증됨', 'NEEDS_REVIEW': '검증 필요', 'COMMUNITY': '커뮤니티', 'LEGACY': '오래된 정보'}
PROVIDER_CONF = {'official': '● 공식 확인', 'ingame': '● 게임 내 확인', 'community_multi': '○ 복수 커뮤니티 확인',
                 'community': '△ 단일 커뮤니티 자료', 'user': '! 사용자 입력(검증 필요)'}

# 퀘스트 상태 3단계
Q_COMPLETED, Q_NOT_COMPLETED, Q_UNKNOWN = 'COMPLETED', 'NOT_COMPLETED', 'UNKNOWN'
Q_ICON = {Q_COMPLETED: '✅ 완료 확인', Q_NOT_COMPLETED: '❌ 미완료 확인', Q_UNKNOWN: '? 확인 불가'}


@dataclass
class Source:
    title: str
    url: str = ''
    provider: str = 'user'          # official | ingame | community_multi | community | user
    checked_at: str = ''            # YYYY-MM-DD
    published_at: str = ''

    @property
    def confidence(self):
        return PROVIDER_CONF.get(self.provider, '! 검증 필요')


@dataclass
class Requirement:
    kind: str                       # REQ_KINDS
    description: str = ''
    min: Optional[int] = None       # LEVEL/JOB_LEVEL 최소, ZENY 금액
    max: Optional[int] = None
    jobs: list = field(default_factory=list)       # JOB: 허용 job_id
    item_id: Optional[int] = None   # ITEM
    item_name: str = ''
    quantity: int = 1
    guide_id: str = ''              # QUEST/EPISODE: 선행 Guide
    source: str = ''                # 근거(공식 텍스트 등)
    optional: bool = False          # '랜덤 요구', '면제 가능' 등 선택 조건


@dataclass
class GuideStep:
    order: int
    title: str
    description: str = ''
    map: str = ''                   # 맵 ID(예: prt_in). 확인 안 되면 빈 값
    x: Optional[int] = None
    y: Optional[int] = None
    npc: str = ''
    action: str = ''
    requirements: list = field(default_factory=list)   # [Requirement]
    dialog_hint: str = ''
    expected_result: str = ''
    optional: bool = False
    source: str = ''
    beginner_tip: str = ''

    @property
    def navi_command(self):
        return navi_command(self.map, self.x, self.y)


@dataclass
class Guide:
    id: str
    type: str
    title: str
    aliases: list = field(default_factory=list)
    description: str = ''
    min_level: Optional[int] = None
    max_level: Optional[int] = None
    required_jobs: list = field(default_factory=list)
    next_jobs: list = field(default_factory=list)
    prerequisites: list = field(default_factory=list)      # 선행 Guide id
    requirements: list = field(default_factory=list)       # 입장/시작 조건 [Requirement]
    start_map: str = ''
    start_x: Optional[int] = None
    start_y: Optional[int] = None
    start_npc: str = ''
    steps: list = field(default_factory=list)              # [GuideStep]
    rewards: list = field(default_factory=list)            # [str]
    strategy: dict = field(default_factory=dict)           # monsters/bosses/cautions/drops
    recommended: list = field(default_factory=list)        # 권장 스펙(공식 입장조건과 분리)
    material_sources: dict = field(default_factory=dict)   # item_id → ["몬스터 드랍: ..."]
    repeat_type: str = ''                                  # ONCE/DAILY/WEEKLY
    cooldown: str = ''
    related_maps: list = field(default_factory=list)       # Replay 기록 연결용 맵 ID
    tags: list = field(default_factory=list)
    sources: list = field(default_factory=list)            # [Source]
    status: str = 'NEEDS_REVIEW'
    verified_at: str = ''
    game_version: str = ''
    version: int = 1
    notes: str = ''
    pack: str = ''                                         # 로드된 Guide Pack 이름

    @property
    def type_label(self):
        return GUIDE_TYPES.get(self.type, self.type)

    @property
    def confidence(self):
        provs = {s.provider for s in self.sources}
        for p in ('official', 'ingame', 'community_multi', 'community', 'user'):
            if p in provs:
                return PROVIDER_CONF[p]
        return '! 검증 필요'

    def all_item_requirements(self):
        """입장 조건 + 단계별 재료(ITEM). 같은 아이템이 여러 단계에 다시 언급되면 합산하지 않고 최대 수량을 쓴다
        (공식 문구에서 같은 재료를 '모아 오라' → '가져다 주면'처럼 반복 언급하는 경우가 많음)."""
        out = {}
        for r in self.requirements + [r for s in self.steps for r in s.requirements]:
            if r.kind == 'ITEM' and (r.item_id or r.item_name):
                key = r.item_id or r.item_name
                cur = out.get(key)
                if cur:
                    cur.quantity = max(cur.quantity, r.quantity)
                    cur.optional = cur.optional and r.optional
                else:
                    out[key] = Requirement(**{**asdict(r)})
        return list(out.values())

    def to_dict(self):
        d = asdict(self)
        d.pop('pack', None)
        return d

    @classmethod
    def from_dict(cls, d, pack=''):
        d = dict(d)
        d['requirements'] = [Requirement(**r) for r in d.get('requirements', [])]
        d['steps'] = [GuideStep(**{**s, 'requirements': [Requirement(**r) for r in s.get('requirements', [])]})
                      for s in d.get('steps', [])]
        d['sources'] = [Source(**s) for s in d.get('sources', [])]
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known and k != 'pack'}, pack=pack)


def navi_command(map_id, x, y):
    """좌표가 있는 경우에만 /navi 명령 생성 (클립보드 복사용)."""
    if not map_id or x is None or y is None:
        return ''
    return '/navi %s %d/%d' % (map_id, x, y)
