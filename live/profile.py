"""ScreenProfile / Region: 해상도·UI 배치별 화면 영역 정의 (DB 저장).

기본 프리셋(1080p/1440p/4K)은 좌표를 지어내지 않고 '미보정' 상태로 시작한다.
사용자가 Calibration(드래그)으로 지정하면 저장되고, 다른 해상도에서는 비율로 환산한 값을
'환산값(확인 필요)'으로만 제안한다.
"""
import json
import logging
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

REGION_KINDS = {
    'HP_REGION': ('HP 바', 'bar'), 'SP_REGION': ('SP 바', 'bar'), 'AP_REGION': ('AP 게이지', 'bar'),
    'EXP_REGION': ('Base EXP 표시', 'text'), 'JOB_EXP_REGION': ('Job EXP 표시', 'text'),
    'WEIGHT_REGION': ('무게 표시', 'text'), 'CHAT_REGION': ('채팅창', 'chat'), 'MAP_REGION': ('맵 이름', 'text'),
    'BUFF_REGION': ('버프 아이콘', 'icons'), 'ITEM_MESSAGE_REGION': ('아이템 메시지', 'chat'),
}
PRESETS = {'1920x1080': (1920, 1080), '2560x1440': (2560, 1440), '3840x2160': (3840, 2160)}


@dataclass
class Region:
    kind: str
    x: int
    y: int
    w: int
    h: int
    bar_color: list = field(default_factory=list)      # 바 채움 색(BGR), Calibration 때 샘플링
    note: str = ''

    def crop(self, frame):
        H, W = frame.shape[:2]
        x0, y0 = max(0, self.x), max(0, self.y)
        x1, y1 = min(W, self.x + self.w), min(H, self.y + self.h)
        if x1 <= x0 or y1 <= y0:
            return None
        return frame[y0:y1, x0:x1]


@dataclass
class ScreenProfile:
    name: str
    width: int
    height: int
    dpi_scale: float = 1.0
    regions: dict = field(default_factory=dict)       # kind → Region
    calibrated: bool = False
    derived_from: str = ''                             # 환산 원본 프로필

    def to_json(self):
        d = asdict(self)
        d['regions'] = {k: asdict(v) for k, v in self.regions.items()}
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, s):
        d = json.loads(s)
        d['regions'] = {k: Region(**v) for k, v in d.get('regions', {}).items()}
        return cls(**d)

    def scaled_to(self, w, h):
        """다른 해상도로 비율 환산(UI가 비율대로 배치된다는 보장은 없으므로 확인 필요 표시)."""
        sx, sy = w / self.width, h / self.height
        regs = {k: Region(r.kind, int(r.x * sx), int(r.y * sy), int(r.w * sx), int(r.h * sy), list(r.bar_color),
                          '비율 환산값 — 확인 필요') for k, r in self.regions.items()}
        return ScreenProfile('%dx%d' % (w, h), w, h, self.dpi_scale, regs, False, self.name)


class ProfileStore:
    def __init__(self, db):
        self.db = db

    def get(self, w, h):
        name = '%dx%d' % (w, h)
        r = self.db._query('SELECT data FROM screen_profiles WHERE name=?', (name,))
        if r:
            try:
                return ScreenProfile.from_json(r[0][0])
            except Exception:
                log.exception('프로필 손상: %s', name)
        # 같은 해상도 프로필이 없으면 보정된 다른 프로필을 비율 환산해 제안
        for (data,) in self.db._query('SELECT data FROM screen_profiles'):
            try:
                p = ScreenProfile.from_json(data)
            except Exception:
                continue
            if p.calibrated:
                return p.scaled_to(w, h)
        return ScreenProfile(name, w, h)

    def save(self, profile):
        self.db._exec('INSERT OR REPLACE INTO screen_profiles VALUES (?,?)', (profile.name, profile.to_json()))

    def all(self):
        out = []
        for (data,) in self.db._query('SELECT data FROM screen_profiles'):
            try:
                out.append(ScreenProfile.from_json(data))
            except Exception:
                pass
        return out
