"""분석 공통: 분석 구간(Window)과 이벤트 필터."""
from dataclasses import dataclass, field

NO_DATA = '리플레이에 해당 데이터가 없음'


@dataclass
class Context:
    pr: object                  # ParsedReplay
    start_ms: int
    end_ms: int
    gamedata: object
    settings: dict = field(default_factory=dict)
    events: list = field(default_factory=list)

    @classmethod
    def build(cls, pr, gamedata, settings, start_ms=0, end_ms=None):
        end = pr.duration_ms if end_ms is None else end_ms
        evs = [e for e in pr.events if start_ms <= e.timestamp_ms <= end]
        return cls(pr, start_ms, end, gamedata, settings or {}, evs)

    @property
    def player(self):
        return self.pr.player_aid

    @property
    def duration_ms(self):
        return max(0, self.end_ms - self.start_ms)

    def of(self, *types):
        return [e for e in self.events if e.event_type in types]


def per_hour(value, ms):
    return value * 3_600_000 / ms if ms and value is not None else None


def per_sec(value, ms):
    return value * 1000 / ms if ms and value is not None else None
