"""ReplaySession + 구간(Segment) 분리. 1차 버전은 MAP_CHANGE 기준."""
import bisect
import datetime
import hashlib
from dataclasses import dataclass, field

from analyzer.events import EventType as E

HUNT_MIN_MS = 10_000     # 이보다 짧은 구간은 사냥 구간으로 보지 않음


@dataclass
class Segment:
    index: int
    map_name: str
    start_ms: int
    end_ms: int
    is_hunt: bool = False
    hunt_reason: str = ''

    @property
    def duration_ms(self):
        return max(0, self.end_ms - self.start_ms)


@dataclass
class ReplaySession:
    session_id: str          # 파일 해시
    replay_file: str
    character: str
    job: str
    job_id: int
    level: int
    job_level: int
    start_time: object
    end_time: object
    duration_ms: int
    maps: list
    server: str
    segments: list = field(default_factory=list)


def file_hash(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def split_segments(pr):
    """MAP_CHANGE 기준 분리 + 사냥 구간 판정(플레이어가 준 피해/처치/몬스터 피격이 있는 구간)."""
    cuts = [(0, None)] + [(e.timestamp_ms, e.map_name) for e in pr.events if e.event_type == E.MAP_CHANGE]
    if len(cuts) == 1:
        cuts = [(0, pr.last_map)]
    elif cuts[1][0] == 0:
        cuts = cuts[1:]
    segs = []
    for i, (start, m) in enumerate(cuts):
        end = cuts[i + 1][0] if i + 1 < len(cuts) else pr.duration_ms
        segs.append(Segment(len(segs), m, start, end))   # m=None: 녹화 시작 맵(패킷에 없음)
    dmg_t = [e.timestamp_ms for e in pr.events
             if e.event_type == E.DAMAGE and e.actor_id == pr.player_aid and not e.data.get('miss')]
    kill_t = [e.timestamp_ms for e in pr.events if e.event_type == E.MONSTER_DEATH]
    count = lambda ts, a, b: bisect.bisect_left(ts, b) - bisect.bisect_left(ts, a)
    for s in segs:
        dealt, kills = count(dmg_t, s.start_ms, s.end_ms), count(kill_t, s.start_ms, s.end_ms)
        if s.duration_ms >= HUNT_MIN_MS and (dealt or kills):
            s.is_hunt = True
            s.hunt_reason = '피해 %d회 / 처치 %d' % (dealt, kills)
    return segs


def build_session(pr, gamedata, session_id):
    segs = split_segments(pr)
    maps = []
    for s in segs:
        if s.map_name and s.map_name not in maps:
            maps.append(s.map_name)
    return ReplaySession(
        session_id=session_id, replay_file=pr.path, character=pr.character, job=gamedata.job_name(pr.job_id),
        job_id=pr.job_id, level=pr.base_level, job_level=pr.job_level, start_time=pr.recorded_at,
        end_time=pr.recorded_at + datetime.timedelta(milliseconds=pr.duration_ms), duration_ms=pr.duration_ms,
        maps=maps, server=pr.server, segments=segs)
