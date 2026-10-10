"""버프 타이머에는 버프만: 타이머 없는 상태(무게 70%·탑승)와 상태이상은 올리지 않고 '재사용!!'도 하지 않는다."""
import unittest
from types import SimpleNamespace

from helpers import PLAYER, ev, sample

from analyzer.events import EventType as E
from core.display import status_kind
from live.buffs import replay_rows
from live.state import NO_TIMER_MS, GameStateReducer
from live.status import BUFF, DEBUFF, EXPIRED_ALERT, STATE, StatusMonitor

WEIGHT, BLESS, DEC_AGI = 35, 10, 13


def reducer(*events):
    s = GameStateReducer(PLAYER)
    for e in events:
        s.apply(e)
    return s


class NoTimerTest(unittest.TestCase):
    def test_9999ms_means_no_timer_not_ten_seconds(self):
        """서버는 끝나는 시각이 없는 상태를 9999ms로 보낸다. 10초 뒤 '끝났다'고 보면 안 된다."""
        s = reducer(ev(0, E.BUFF_START, PLAYER, efst=WEIGHT, remain_ms=NO_TIMER_MS, max_ms=NO_TIMER_MS))
        b = s.buffs[WEIGHT]
        self.assertFalse(b['timed'])
        self.assertIsNone(b['expected_end'])
        s.advance(60_000)                                  # 1분이 지나도 추정 종료로 바뀌지 않는다
        self.assertTrue(s.buffs[WEIGHT]['active'])
        self.assertIsNone(s.buffs[WEIGHT]['ending'])

    def test_real_duration_keeps_its_timer(self):
        s = reducer(ev(0, E.BUFF_START, PLAYER, efst=BLESS, remain_ms=240_000, max_ms=240_000))
        self.assertTrue(s.buffs[BLESS]['timed'])
        self.assertEqual(s.buffs[BLESS]['expected_end'], 240_000)

    def test_real_replay_weight_state_has_no_timer(self):
        path = sample('1weq.rrf')
        if not path:
            self.skipTest('local replay unavailable')
        from analyzer.normalizer import normalize
        from rrf import Replay
        pr = normalize(Replay(path), path, experimental=True)
        s = GameStateReducer(pr.player_aid)
        for e in pr.events:
            if e.event_type == E.BUFF_START and e.actor_id == pr.player_aid:
                s.apply(e)
        untimed = {k for k, b in s.buffs.items() if not b['timed']}
        self.assertIn(WEIGHT, untimed)                     # 무게 70% 이상: 실제로 9999ms로 온다
        self.assertTrue(all(s.buffs[k]['expected_end'] is None for k in untimed))


class KindTest(unittest.TestCase):
    def test_symbol_classification_is_conservative(self):
        self.assertEqual(status_kind('EFST_DEC_AGI'), 'debuff')
        self.assertEqual(status_kind('EFST_CRITICALWOUND'), 'debuff')
        self.assertEqual(status_kind('EFST_BLESSING'), 'buff')
        self.assertEqual(status_kind('SOMETHING_NEW'), 'buff')      # 모르는 것은 숨기지 않는다
        self.assertEqual(status_kind(None), 'buff')

    def test_rows_carry_the_kind(self):
        display = SimpleNamespace(
            status=lambda efst: SimpleNamespace(text='효과%s' % efst, raw='', internal=False, confidence=''),
            status_kind=lambda efst: 'debuff' if efst == DEC_AGI else 'buff')
        snap = dict(duration_ms=1000, buffs=[
            dict(buff_id=WEIGHT, expected_end=None, timed=False, started_at=0),
            dict(buff_id=DEC_AGI, expected_end=31_000, timed=True, started_at=0),
            dict(buff_id=BLESS, expected_end=241_000, timed=True, started_at=0)])
        kinds = {r['efst']: r['kind'] for r in replay_rows(snap, display=display)}
        self.assertEqual(kinds, {WEIGHT: STATE, DEC_AGI: DEBUFF, BLESS: BUFF})

    def test_rows_without_display_default_to_buff(self):
        rows = replay_rows(dict(duration_ms=0, buffs=[dict(buff_id=1, expected_end=5000, started_at=0)]))
        self.assertEqual(rows[0]['kind'], BUFF)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.s, self.fired, self.now = {}, [], 1000.0
        self.m = StatusMonitor(self.s, alert=lambda key, stage, text: self.fired.append((key, stage)),
                               clock=lambda: self.now)

    def row(self, key, kind, remaining=None):
        return dict(key=key, name=key, kind=kind, remaining=remaining, source='REPLAY')

    def test_only_buffs_are_shown_by_default(self):
        items = self.m.sync([self.row('weight', STATE), self.row('slow', DEBUFF, 20), self.row('bless', BUFF, 200)])
        self.assertEqual([i.key for i in self.m.visible(items)], ['bless'])

    def test_setting_or_favorite_brings_them_back(self):
        rows = [self.row('weight', STATE), self.row('bless', BUFF, 200)]
        self.s['status_favorites'] = ['weight']                 # 폭기처럼 타이머는 없지만 보고 싶은 것
        self.assertEqual({i.key for i in self.m.visible(self.m.sync(rows))}, {'weight', 'bless'})
        self.s['status_favorites'] = []
        self.s['status_show_nonbuff'] = True
        self.assertEqual({i.key for i in self.m.visible(self.m.sync(rows))}, {'weight', 'bless'})

    def test_non_buffs_never_ask_for_reuse(self):
        self.m.sync([self.row('weight', STATE), self.row('slow', DEBUFF, 5), self.row('bless', BUFF, 200)])
        self.now += 10
        items = self.m.sync([])                                 # 셋 다 사라짐
        self.assertEqual({i.key: i.state for i in items}, {'bless': EXPIRED_ALERT})   # 버프만 '재사용!!'
        self.assertNotIn('weight', self.m.items)                # 상태·상태이상은 그냥 치운다
        self.assertEqual([k for k, stage in self.fired if stage == EXPIRED_ALERT], ['bless'])


if __name__ == '__main__':
    unittest.main()
