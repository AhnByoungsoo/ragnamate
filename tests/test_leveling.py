"""레벨업 코치: 코스 자료 검사, 레벨 → 지금 할 일·대안·다음, 지금 레벨의 근거."""
import unittest

import helpers  # noqa: F401  (sys.path 설정)

from leveling.course import Courses, current_level  # noqa: E402

C = Courses()


class DataTest(unittest.TestCase):
    def test_every_step_is_well_formed(self):
        ids = set()
        for key, route in C.routes.items():
            for s in route['steps']:
                self.assertNotIn(s.id, ids)
                ids.add(s.id)
                self.assertLessEqual(s.min, s.max, s.id)
                self.assertIn(s.check, ('sheet', 'seen', 'doc', 'chapter'), s.id)
                self.assertTrue(s.video_url.startswith('https://www.youtube.com/watch?v='), s.id)

    def test_only_sourced_coordinates_become_navi(self):
        navi = {s.id: s.navi_text for r in C.routes.values() for s in r['steps'] if s.navi}
        self.assertEqual(set(navi.values()), {'/navi beach_dun2 248/254'})          # 공식 공지의 루안다 입구뿐
        self.assertEqual(set(navi), {'fs_160', 'ft_160', 'fm_160'})


class AdviceTest(unittest.TestCase):
    def titles(self, route, level):
        a = C.advise(route, level)
        return [s.title for s in a.now], a.next.title if a.next else None

    def test_boundaries_of_the_skip_route(self):
        self.assertEqual(self.titles('fast_skip', 209)[1], '니플헤임 던전 1층')
        self.assertEqual(self.titles('fast_skip', 210)[0], ['니플헤임 던전 1층'])
        self.assertEqual(self.titles('fast_skip', 236)[0], ['아미키티아 연구소 2층'])
        self.assertEqual(self.titles('fast_skip', 237)[0], ['레벨업 티켓 3장'])
        self.assertEqual(self.titles('fast_skip', 240), (['바이오스피어 혹한 / 화염'], None))
        self.assertEqual(self.titles('fast_skip', 160)[0], ['일루전 루안다 → 트윈스'])
        self.assertIn('일루전 구양궁', self.titles('fast_skip', 155)[0])                   # 생략 코스는 EP17 단계 없음

    def test_ticket_route_follows_the_sheet(self):
        self.assertIn('일루전 구양궁 + EP17 보상', self.titles('fast_ticket', 155)[0])
        self.assertEqual(self.titles('fast_ticket', 200)[0], ['4차 전직 → 주력 스킬·장비', '비공정 추락지 [40분]'])
        self.assertEqual(self.titles('fast_ticket', 237)[0], ['레벨업 티켓 3장'])

    def test_optional_illusions_come_after_the_recommended_ones(self):
        now, _ = self.titles('fast_ticket', 147)
        self.assertEqual(now[0], '미로 / 얼어붙은 꼬리(강구)')                  # 145~149는 아직 강구가 '지금'
        self.assertEqual(now[1:4], ['③ 일루전 게펜', '④ 일루전 심해 상층', '⑤ 일루전 테디베어'])
        self.assertEqual(now[4:], ['① 일루전 페이욘 [선택]', '② 일루전 얼음 동굴 [선택]'])

    def test_todo_is_split_into_ordered_steps(self):
        step = C.advise('fast_ticket', 165).now[0]
        self.assertEqual((step.kind, step.time_label), ('TICKET', '13:44'))
        self.assertEqual(C.advise('fast_ticket', 160).now[0].todo[-1], '일루전 트윈스 100마리 토벌')

    def test_overlapping_farm_ranges_stay_as_alternatives(self):
        now, _ = self.titles('farm', 217)
        self.assertEqual(now, ['파괴된 게펜 필드', '파괴된 베루스', '꼬뽀 (영상 표기 약칭)', '브리미르 1층'])
        self.assertIn('브리미르 1층', self.titles('farm', 227)[0])               # 양끝 포함

    def test_gap_in_the_data_is_said_not_filled(self):
        now, nxt = self.titles('farm', 230)
        self.assertEqual((now, nxt), ([], '솟아오른 대지 북부'))

    def test_beyond_the_data(self):
        self.assertTrue(C.advise('farm', 251).out_of_range)
        self.assertIsNone(C.advise('farm', None).level)


class LevelSourceTest(unittest.TestCase):
    def test_level_up_seen_in_replay_beats_the_recording_start(self):
        self.assertEqual(current_level(dict(level=223, base_level_now=226)), (226, '리플레이 레벨업 기록'))
        self.assertEqual(current_level(dict(level=223)), (223, '리플레이 녹화 시작 레벨'))
        self.assertEqual(current_level(dict(level=223, base_level_now=226), manual=230),     # 직접 값은 덮지 않는다
                         (226, '리플레이 레벨업 기록'))
        self.assertEqual(current_level(None, manual=230), (230, '직접 입력'))
        self.assertEqual(current_level(dict(level=223), manual=10, screen=115), (115, '화면 인식'))
        self.assertEqual(current_level(None), (None, ''))

    def test_saved_location_becomes_navi(self):
        c = Courses(user_navi={'fs_210': dict(map='niflheim', x=200, y=180, label='직접 저장')})
        step = next(s for s in c.routes['fast_skip']['steps'] if s.id == 'fs_210')
        self.assertEqual(step.navi_text, '/navi niflheim 200/180')


class FakeOcr:
    """창 전체(1.5배)·작은 자리(2배)에 각각 정해 둔 글을 돌려준다."""

    def __init__(self):
        self.full, self.small, self.calls = [], [], []

    def recognize(self, img, scale=1.0):
        from live.ocr import OcrLine
        self.calls.append(scale)
        return [OcrLine(t, 1.0, (16, 86, 64, 8)) for t in (self.full if scale == 1.5 else self.small)]


class ScreenLevelTest(unittest.TestCase):
    def test_parse_handles_what_windows_ocr_returned_on_the_real_screen(self):
        from live.level_watch import parse_level
        for text, want in (('Base Lv. 115', 115), ('Base 115', 115), ('Base 나,. 115', 115), ('Job Lv. 26', None),
                           ('Base Lv. 999', None), ('6시 싸요', None)):
            self.assertEqual(parse_level(text), want, text)

    def test_locate_once_then_read_the_small_spot_and_confirm_on_two_equal_reads(self):
        import numpy as np
        from live.level_watch import LevelWatcher
        ocr = FakeOcr()
        w = LevelWatcher(lambda d: None, ocr=ocr)
        img = np.zeros((200, 300, 3), np.uint8)
        ocr.full = ['기본정보', 'Base Lv. 115', 'Job Lv. 26']
        self.assertEqual(w.observe(1, img, now=0), (None, '화면 인식'))       # 한 번 읽음 — 아직 확정 전
        ocr.small = ['Base 115']
        self.assertEqual(w.observe(1, img, now=1), (115, '화면 인식'))        # 같은 숫자 두 번 → 확정
        self.assertEqual(ocr.calls, [1.5, 2.0])                               # 전체는 처음 한 번만
        ocr.small = ['Base 178']                                              # 한 번 잘못 읽음
        self.assertEqual(w.observe(1, img, now=2)[0], 115)
        ocr.small = ['Base 116']
        w.observe(1, img, now=3)
        self.assertEqual(w.observe(1, img, now=4)[0], 116)

    def test_hidden_window_keeps_the_last_level_and_says_why(self):
        import numpy as np
        from live.level_watch import LevelWatcher
        ocr = FakeOcr()
        w = LevelWatcher(lambda d: None, ocr=ocr)
        img = np.zeros((200, 300, 3), np.uint8)
        ocr.full, ocr.small = ['Base Lv. 115'], ['Base 115']
        w.observe(1, img, now=0)
        w.observe(1, img, now=1)
        ocr.full, ocr.small = [], []
        level, status = w.observe(1, img, now=2)
        self.assertEqual(level, 115)
        self.assertIn('찾지 못함', status)
        w.observe(1, img, now=3)
        self.assertEqual(ocr.calls.count(1.5), 1)                             # 전체 다시 찾기는 5초에 한 번까지


@unittest.skipUnless(helpers.sample('321321.rrf'), '리플레이 표본 없음')
class ReplayLevelTest(unittest.TestCase):
    def test_level_ups_in_a_real_replay_reach_the_live_state(self):
        from analyzer.normalizer import normalize
        from live.state import GameStateReducer
        from rrf import Replay
        path = helpers.sample('321321.rrf')
        pr = normalize(Replay(path), path)
        s = GameStateReducer(pr.player_aid)
        ups = []
        for e in pr.events:
            s.apply(e)
            if e.event_type == 'LEVEL_CHANGE' and e.data['kind'] == 'base':
                ups.append(e.data['level'])
        self.assertEqual((pr.base_level, ups, s.view()['base_level_now']), (223, [224, 225, 226], 226))


if __name__ == '__main__':
    unittest.main()
