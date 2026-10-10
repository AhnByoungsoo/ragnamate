"""공식 퀘스트 정보(게임 클라이언트 OngoingQuestInfoList) 읽기와 챕터 STEP 보강."""
import os
import unittest

from helpers import ROOT  # noqa: F401  (sys.path 설정)

from navigator.quest_info import QuestInfoDB, key, parse_table

RO = r'C:\Gravity\Ragnarok'
# 실제 파일(2026-09-16 패치) 기록을 그대로 옮긴 것
TABLE = {
    8937.0: {'Title': '그것을 원한다면 (3)', 'Summary': '그림자 간수 20체 파괴', 'Description': {
        '1': '브리미르의 영은 그림자 간수를 20체 파괴하고 돌아오면 <NAVI>[일]<INFO>uknw_ruin,160,160,0,101,0</INFO></NAVI>을 깨울 수 있을 것이라고 한다.'}},
    18369.0: {'Title': '세계수의 부름(2)', 'Summary': '샤샤와 대화', 'NpcSpr': '4_CH1_DELEGACY02', 'NpcNavi': 'ygg_fruit',
              'NpcPosX': 80.0, 'NpcPosY': 122.0, 'Description': {'1': '라피네 샤샤를 만나보자.'}},
    12479.0: {'Title': '사념정화', 'Summary': '아네스 퇴치', 'Description': {'1': '사념 퇴치'}},
    12481.0: {'Title': '사념정화', 'Summary': '세실리아 퇴치', 'Description': {'1': '사념 퇴치'}},
    26030.0: {'Title': '^0000ff카르노스 전직', 'Summary': '벨레도르 이동', 'Description': {
        '1': '<NAVI>[벨레도르 안쪽]<INFO>veledor.gat,76,256,0,101,0</INFO></NAVI>으로 안내받았다.'}},
}


def db_from(table):
    db = QuestInfoDB('')
    db.quests = parse_table(table)
    for q in db.quests.values():
        db.by_key.setdefault(key(q.title), []).append(q)
    return db


class QuestInfoTest(unittest.TestCase):
    def test_parse_goal_and_navi(self):
        db = db_from(TABLE)
        q = db.find('그것을 원한다면 (3)')
        self.assertEqual((q.id, q.summary, q.goal, q.navi),
                         (8937, '그림자 간수 20체 파괴', ('일', 'uknw_ruin', 160, 160), '/navi uknw_ruin 160/160'))
        self.assertIn('[일]', '[' + q.targets[0][0] + ']')
        self.assertNotIn('<NAVI>', q.text)
        s = db.find('세계수의 부름(2)')
        self.assertEqual(s.goal, ('샤샤', 'ygg_fruit', 80, 122))                 # NPC 칸 + '샤샤와 대화'
        self.assertEqual(db.find('카르노스 전직').goal, ('벨레도르 안쪽', 'veledor', 76, 256))   # 색 코드·.gat 제거

    def test_find_typo_and_same_title(self):
        db = db_from(TABLE)
        self.assertEqual(db.find('그것를 원한다면 (3)').id, 8937)                # OCR 오타
        self.assertEqual(db.find('사념정화', '아네스 퇴치').id, 12479)            # 같은 제목은 목표 줄로
        self.assertEqual(db.find('사념정화', '세실리아 퇴치').id, 12481)
        self.assertIsNone(db.find('전혀 없는 퀘스트 이름'))

    @unittest.skipUnless(os.path.exists(os.path.join(RO, 'System', 'OngoingQuestInfoList_True.lub')), '게임 설치 없음')
    def test_real_client_file(self):
        db = QuestInfoDB(RO).load()
        self.assertGreater(len(db.quests), 10000)
        self.assertEqual(db.find('세계수의 부름(1)').goal[1:], ('prt_fild05', 353, 252))   # 공식 공지 좌표와 같음
        self.assertEqual(db.find('그것을 원한다면 (3)').navi, '/navi uknw_ruin 160/160')

    def test_enrich_pieces(self):
        from tools.enrich_chapter_official import pieces
        self.assertEqual(pieces('세계수의 부름 (1)(2)(3) · 뒤틀린 암흑의 땅'),
                         ['세계수의 부름(1)', '세계수의 부름(2)', '세계수의 부름(3)', '뒤틀린 암흑의 땅'])
        self.assertEqual(pieces('그레고어와 드브리스의 추적 · 그것을 원한다면(3)'), ['그레고어와 드브리스의 추적', '그것을 원한다면(3)'])
        self.assertEqual(pieces('정체불명의 지진 (니르홀트)'), ['정체불명의 지진'])


if __name__ == '__main__':
    unittest.main()
