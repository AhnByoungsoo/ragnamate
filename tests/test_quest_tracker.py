"""퀘스트 알림창 읽기: 2026-09-27 게임 스크린샷을 Windows OCR(3배)로 읽은 결과(tests/data/quest_tracker_lines.json)."""
import json
import os
import unittest

from helpers import ROOT

from core import quest_tracker as Q
from navigator.guide_service import GuideService

HERE = os.path.dirname(os.path.abspath(__file__))


def quests():
    with open(os.path.join(HERE, 'data', 'quest_tracker_lines.json'), encoding='utf-8') as f:
        return Q.parse([(d['text'], tuple(d['box']), d['title']) for d in json.load(f)])


class QuestTrackerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chapters = [g for g in GuideService(os.path.join(ROOT, 'data', 'guides')).repo.all() if g.type == 'CHAPTER']

    def test_parse_titles_and_progress(self):
        qs = quests()
        self.assertEqual(len(qs), 5)                                  # 노란 제목 5개
        q = next(q for q in qs if q.title.startswith('그것을'))
        self.assertEqual(q.title, '그것을 원한다면 (3)')
        o = q.objectives[0]
        self.assertEqual((o.text, o.cur, o.need, o.left, o.done), ('그림자 간수 20체 파괴', 7, 20, 13, False))

    def test_colors(self):
        self.assertTrue(Q.is_title_color((132, 200, 205)))            # 노란 제목
        self.assertFalse(Q.is_title_color((255, 253, 252)))           # 흰 목표
        self.assertFalse(Q.is_title_color(None))

    def test_match_step(self):
        g, i, q, sc = Q.match(quests(), self.chapters)
        self.assertEqual((g.id, i + 1, q.title, sc), ('chapter2_shard_of_end', 16, '그것을 원한다면 (3)', 100))
        # OCR이 '(3)'을 빠뜨리면 지금 STEP 이후를 우선
        bare = [Q.TrackedQuest('그것을 원한다면')]
        self.assertEqual(Q.match(bare, self.chapters, ('chapter2_shard_of_end', 15))[1] + 1, 16)
        self.assertEqual(Q.match(bare, self.chapters, ('chapter2_shard_of_end', 11))[1] + 1, 12)
        self.assertIsNone(Q.match([Q.TrackedQuest('낙원단의 단장')], self.chapters))    # 가이드에 없는 퀘스트

    def test_objective_line_forms(self):
        qs = Q.parse([('A 퀘스트', (0, 0, 9, 9), True), ('목표 설명', (0, 10, 9, 9), False),
                      ('몬스터 ( 20 / 20 )', (0, 20, 9, 9), False), ('다른 몬스터 (3/5)', (0, 30, 9, 9), False)])
        o1, o2 = qs[0].objectives
        self.assertTrue(o1.done)
        self.assertEqual((o2.text, o2.cur, o2.need), ('다른 몬스터', 3, 5))
        self.assertEqual(Q.parse([('제목 없는 흰 글자', (0, 0, 9, 9), False)]), [])


if __name__ == '__main__':
    unittest.main()
