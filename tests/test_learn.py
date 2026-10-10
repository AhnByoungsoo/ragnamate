"""효과 이름 후보 학습: 내가 쓴 스킬 직후에 생긴 효과만 짝으로 본다."""
import os
import tempfile
import unittest

from helpers import PLAYER, ev  # noqa: F401  (sys.path 설정 포함)

from live.learn import BuffOriginLearner


def learner():
    return BuffOriginLearner(os.path.join(tempfile.mkdtemp(), 'buff_origins.json'))


class ObserveTest(unittest.TestCase):
    def test_pairs_my_skill_with_following_buff(self):
        events = [ev(1000, 'SKILL_USE', PLAYER, skill=5217),
                  ev(1055, 'BUFF_START', PLAYER, efst=10)]
        m = learner()
        self.assertEqual(m.observe_events(events, PLAYER), 1)
        self.assertEqual(m.candidates(10), [(5217, 1, 55)])

    def test_ignores_other_players_and_late_buffs(self):
        events = [ev(1000, 'SKILL_USE', 999, skill=10),          # 남이 쓴 스킬
                  ev(1050, 'BUFF_START', PLAYER, efst=46),
                  ev(2000, 'SKILL_USE', PLAYER, skill=20),
                  ev(9000, 'BUFF_START', PLAYER, efst=47)]       # 한참 뒤 → 짝 아님
        m = learner()
        self.assertEqual(m.observe_events(events, PLAYER), 0)
        self.assertEqual(m.candidates(46), [])
        self.assertEqual(m.candidates(47), [])

    def test_repeats_raise_the_candidate(self):
        m = learner()
        for t in (0, 5000, 10000):
            m.observe_events([ev(t, 'SKILL_USE', PLAYER, skill=7),
                              ev(t + 100, 'BUFF_START', PLAYER, efst=99)], PLAYER)
        m.observe_events([ev(20000, 'SKILL_USE', PLAYER, skill=8),
                          ev(20100, 'BUFF_START', PLAYER, efst=99)], PLAYER)
        self.assertEqual([c[0] for c in m.candidates(99)], [7, 8])       # 많이 본 것이 앞
        self.assertEqual(m.top(99)[1], 3)

    def test_multiple_skills_are_ambiguous(self):
        events = [ev(0, 'SKILL_USE', PLAYER, skill=1), ev(400, 'SKILL_USE', PLAYER, skill=2),
                  ev(450, 'BUFF_START', PLAYER, efst=5)]
        m = learner()
        m.observe_events(events, PLAYER)
        self.assertIsNone(m.top(5))

    def test_saved_and_reloaded(self):
        m = learner()
        m.observe_events([ev(0, 'SKILL_USE', PLAYER, skill=3), ev(60, 'BUFF_START', PLAYER, efst=8)], PLAYER)
        again = BuffOriginLearner(m.path)
        self.assertEqual(again.top(8)[0], 3)

    def test_suggestion_text_is_a_candidate_not_a_name(self):
        m = learner()
        m.observe_events([ev(0, 'SKILL_USE', PLAYER, skill=3), ev(60, 'BUFF_START', PLAYER, efst=8)], PLAYER)
        text = m.suggestion(8, lambda sid: '블레싱')
        self.assertIn('후보', text)
        self.assertIn('블레싱', text)
        self.assertIsNone(m.suggestion(1234))                     # 관측 없으면 제안도 없다


class UnknownLabelTest(unittest.TestCase):
    def test_unknown_effects_are_numbered_for_telling_apart(self):
        from core.display import DisplayNames

        class G:
            buffs = type('B', (), {'name': lambda self, i: '버프 #%d' % i, 'origin': lambda self, i: ''})()
            skills = buffs
            items = None
            monsters = maps = None

            @staticmethod
            def job_name(j):
                return '-'

        d = DisplayNames(G())
        first, second = d.status(111).text, d.status(222).text
        self.assertEqual(first, '알 수 없는 효과 1')
        self.assertEqual(second, '알 수 없는 효과 2')
        self.assertEqual(d.status(111).text, first)               # 같은 효과는 항상 같은 번호


if __name__ == '__main__':
    unittest.main()
