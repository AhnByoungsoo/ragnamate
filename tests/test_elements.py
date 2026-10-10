import json
import os
import tempfile
import unittest

from helpers import PLAYER, ev
from analyzer.events import EventType as E
from core import elements
from live.state import GameStateReducer

MOB_YML = """Header:
  Type: MOB_DB
  Version: 4
Body:
  - Id: 1002
    AegisName: PORING
    Name: Poring
    Level: 1
    Hp: 55
    Size: Medium
    Race: Plant
    Element: Water
    ElementLevel: 1
  - Id: 1036
    AegisName: GHOUL
    Name: Ghoul
    Level: 61
    Hp: 2429
    Race: Undead
    Element: Undead
    ElementLevel: 2
"""
ATTR_YML = """Header:
  Type: ATTRIBUTE_DB
  Version: 1
Body:
  - Level: 1
    Wind:
      Water: 175
    Fire:
      Water: 50
  - Level: 2
    Holy:
      Undead: 175
    Wind:
      Undead: 100
"""
_full = {a: {d: 100 for d in elements.ELEMENTS} for a in elements.ELEMENTS}
OFFICIAL_ATTR = {'1': dict(_full, Wind=dict(_full['Wind'], Water=150)),
                 '4': dict(_full, Dark=dict(_full['Dark'], Holy=200), Holy=dict(_full['Holy'], Holy=0))}
OFFICIAL_MOBS = [
    dict(aegis='PORING', name='포링', grade='일반', level=1, size='Medium', race='Plant', element='Water',
         element_level=1, hp=60, maps=['프론테라 필드']),
    dict(aegis='ANGELGOLT', name='엔젤골트', grade='일반', level=196, size='Small', race='Angel', element='Holy',
         element_level=4, hp=1, maps=['고대 오딘 신전']),
    dict(aegis='ANGELGOLT2', name='엔젤골트', grade='일반', level=196, size='Small', race='Angel', element='Holy',
         element_level=4, hp=1, maps=['고대 오딘 신전']),
    dict(aegis='ODIN_BOSS', name='보스', grade='보스', level=200, size='Large', race='Angel', element='Holy',
         element_level=4, hp=1, maps=['고대 오딘 신전']),
    dict(aegis='TWIN_A', name='쌍둥이', grade='일반', level=1, size=None, race=None, element='Fire', element_level=1, hp=1),
    dict(aegis='TWIN_B', name='쌍둥이', grade='일반', level=1, size=None, race=None, element='Water', element_level=1, hp=1),
]


def write(folder, name, text):
    with open(os.path.join(folder, name), 'w', encoding='utf-8') as f:
        f.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))


class RathenaFallbackTest(unittest.TestCase):
    """공식 자료가 없을 때: rAthena 자료만으로 계산."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write(self.tmp.name, 'mob_db.yml', MOB_YML)
        write(self.tmp.name, 'attr_fix.yml', ATTR_YML)
        self.a = elements.ElementAdvisor(self.tmp.name, official_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_and_defaults(self):
        self.assertEqual(self.a.info(1002)['element'], 'Water')
        self.assertEqual(self.a.info(1002)['source'], elements.SOURCE_RATHENA)
        self.assertEqual(self.a.info(1036)['size'], 'Small')          # 빠진 칸은 rAthena 기본값
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, 'mob_index.json')))
        again = elements.ElementAdvisor(self.tmp.name, official_dir=self.tmp.name)
        self.assertEqual(again.info(1002)['hp'], 55)                   # 캐시로 다시 읽기
        self.assertIsNone(self.a.info(424242))
        self.assertEqual(self.a.modifier('Neutral', 'Water', 1), 100)  # 표에 없는 칸 = 100

    def test_weighted_recommendation(self):
        rec = self.a.recommend([dict(class_id=1002, name='포링', weight=3), dict(class_id=1036, name='구울', weight=1),
                                dict(class_id=424242, name='신규', weight=2), dict(class_id=1002, name='x', weight=0)])
        self.assertTrue(rec['available'])
        self.assertEqual(rec['attr_source'], elements.SOURCE_RATHENA)
        by = {r['element']: r for r in rec['elements']}
        self.assertAlmostEqual(by['Wind']['avg'], (175 * 3 + 100) / 4)
        self.assertAlmostEqual(by['Holy']['avg'], (100 * 3 + 175) / 4)
        self.assertEqual(by['Fire']['worst'], 50)
        self.assertEqual(rec['elements'][0]['element'], 'Wind')
        text = elements.summary_text(rec)
        self.assertIn('추천 공격 속성: 풍 156%', text)
        self.assertIn('피할 속성: 화 ', text)
        self.assertIn('rAthena 속성표 기준 추정', text)
        self.assertIn('rAthena 자료로 추정', text)
        self.assertIn('자료 없어 제외(33%): 신규', text)

    def test_nothing_observed_or_mostly_unknown(self):
        self.assertIn('관측한 몬스터 없음', elements.summary_text(self.a.recommend([])))
        # 풀 한 포기만 자료가 있고 실제 사냥 몬스터는 자료가 없으면 추천하지 않는다
        rec = self.a.recommend([dict(class_id=1002, name='포링', weight=1), dict(class_id=424242, name='신규', weight=80)])
        self.assertFalse(rec['available'])
        text = elements.summary_text(rec)
        self.assertIn('계산하지 않음', text)
        self.assertIn('자료 있는 비율 1%', text)
        self.assertIn('자료 없음: 신규', text)


class OfficialDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.off = os.path.join(self.tmp.name, 'official')
        os.makedirs(self.off)
        write(self.tmp.name, 'mob_db.yml', MOB_YML)
        write(self.tmp.name, 'attr_fix.yml', ATTR_YML)
        write(self.off, 'attr_table.json', dict(table=OFFICIAL_ATTR))
        write(self.off, 'monsters.json', dict(monsters=OFFICIAL_MOBS))
        self.a = elements.ElementAdvisor(self.tmp.name, official_dir=self.off)

    def tearDown(self):
        self.tmp.cleanup()

    def test_official_first_by_aegis_then_name(self):
        i = self.a.info(1002)                                   # rAthena Id → AegisName → 공식
        self.assertEqual((i['source'], i['name'], i['hp']), (elements.SOURCE_OFFICIAL, '포링', 60))
        i = self.a.info(20605, '엔젤 골트')                       # rAthena 에 없는 신규: 한글 이름(공백 무시)
        self.assertEqual((i['element'], i['element_level']), ('Holy', 4))
        self.assertIsNone(self.a.info(99999, '쌍둥이'))           # 같은 이름인데 속성이 다르면 쓰지 않는다
        self.assertEqual(self.a.info(1036)['source'], elements.SOURCE_RATHENA)
        self.assertEqual(elements.traits_text(self.a.info(20605, '엔젤골트')), '성4 · 천사형 · 소형')
        self.assertIn('rAthena 추정', elements.traits_text(self.a.info(1036)))

    def test_official_attr_and_map_list(self):
        mobs = self.a.map_monsters('고대오딘 신전')
        self.assertEqual(sorted(m['class_id'] for m in mobs), ['ANGELGOLT', 'ANGELGOLT2'])   # 보스 제외
        rec = self.a.recommend(mobs)
        self.assertEqual(rec['attr_source'], elements.SOURCE_OFFICIAL)
        self.assertEqual(rec['elements'][0]['element'], 'Dark')
        self.assertEqual(rec['elements'][0]['avg'], 200)
        text = elements.summary_text(rec, basis='공식 출현 목록')
        self.assertIn('추천 공격 속성: 암 200%', text)
        self.assertIn('피할 속성: 성 0%', text)
        self.assertIn('공식 출현 목록 2종 평균(보스 제외)', text)
        self.assertIn('공식 홈페이지', text)
        self.assertEqual(self.a.map_monsters('없는 맵'), [])


class MapMonstersTest(unittest.TestCase):
    def test_counts_per_class_and_resets_on_map_change(self):
        s = GameStateReducer(PLAYER)
        s.apply(ev(10, E.MONSTER_SPAWN, 101, class_id=1002, name='포링'))
        s.apply(ev(20, E.MONSTER_SPAWN, 102, class_id=1002, name='포링'))
        s.apply(ev(30, E.MONSTER_MOVE, 101, class_id=1002, x=5, y=5))   # 같은 몬스터 이동은 한 마리
        s.apply(ev(40, E.MONSTER_SPAWN, 103, class_id=1036, name='구울'))
        s.apply(ev(50, E.MONSTER_DEATH, 103))                             # 죽어도 이 맵에서 본 몬스터
        s.apply(ev(60, E.NPC_SPAWN, 900, class_id=50, name='NPC'))
        self.assertEqual([(m['class_id'], m['weight']) for m in s.view()['monsters']], [(1002, 2), (1036, 1)])
        s.apply(ev(70, E.MAP_CHANGE, PLAYER, map_name='other'))
        self.assertEqual(s.view()['monsters'], [])


if __name__ == '__main__':
    unittest.main()
