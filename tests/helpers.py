import collections
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analyzer.events import ReplayEvent  # noqa: E402
from analyzer.normalizer import OBJ_MOB, ParsedReplay  # noqa: E402

REPLAY_DIR = os.environ.get('RO_REPLAY_FIXTURES', r'C:\Gravity\Ragnarok\Replay')
RO_DIR = r'C:\Gravity\Ragnarok'
PLAYER = 1000


def sample(name):
    p = os.path.join(REPLAY_DIR, name)
    return p if os.path.exists(p) else None


class Names:
    def __init__(self, label):
        self.label = label

    def name(self, i):
        return '%s%d' % (self.label, i)


class FakeItems:
    def name(self, i):
        return 'item%d' % i


class FakeGameData:
    def __init__(self):
        self.skills, self.buffs = Names('skill'), Names('buff')
        self.monsters = type('M', (), {'observe': lambda self, m: None, 'name': lambda self, c: 'mob%d' % c})()
        self.items = FakeItems()
        self.maps = type('Mp', (), {'name': lambda self, m: m or '?'})()

    @staticmethod
    def job_name(j):
        return 'job%s' % j


def ev(t, typ, actor=None, target=None, map_name='field', **data):
    return ReplayEvent(t, typ, actor, target, map_name, data.pop('x', None), data.pop('y', None), data)


def parsed(events, duration_ms, actors=None, inventory_lists=None, start_zeny=1000):
    return ParsedReplay(
        path='synthetic.rrf', packet_ver='test', recorded_at=datetime.datetime(2026, 9, 20, 12, 0, 0),
        duration_ms=duration_ms, player_aid=PLAYER, character='tester', job_id=4255, base_level=250, job_level=10,
        start_zeny=start_zeny, last_map='field', server='test', events=sorted(events, key=lambda e: e.timestamp_ms),
        packets=[(0, b'\x00\x00')], actors=actors or {}, inventory_lists=inventory_lists or [],
        unknown_counts=collections.Counter(), unsupported_counts=collections.Counter(),
        decoded_counts=collections.Counter(), unmapped_vars=collections.Counter())


def mob_actor(class_id=1002, name='포링'):
    return dict(obj_type=OBJ_MOB, class_id=class_id, name=name, level=1)
