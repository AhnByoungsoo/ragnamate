"""사용자 화면 이름 계층: 내부 Symbol이 그대로 새어 나오지 않는지."""
import unittest

from helpers import ROOT  # noqa: F401  (sys.path 설정)

from core.display import CONFIRMED, DERIVED, UNKNOWN, DisplayNames, status_text


class FakeNames:
    def __init__(self, names=None, srcs=None):
        self.names, self.srcs = names or {}, srcs or {}

    def name(self, i):
        return self.names.get(i) or '버프 #%d' % i

    def origin(self, i):
        return self.srcs.get(i, '')


class FakeItems:
    def name(self, i):
        return {12883: '올마이티'}.get(i) or '아이템 #%s' % i


class FakeGame:
    def __init__(self, buffs=None):
        self.buffs = buffs or FakeNames()
        self.skills = FakeNames()
        self.items = FakeItems()
        self.monsters = type('M', (), {'name': lambda self, c: {1002: '포링'}.get(c) or 'mob%s' % c})()
        self.maps = type('Mp', (), {'name': lambda self, m: {'prontera': '프론테라'}.get(m) or m})()

    @staticmethod
    def job_name(j):
        return {4262: '인퀴지터'}.get(j, '-')


class GlossaryTest(unittest.TestCase):
    def test_known_patterns(self):
        self.assertEqual(status_text('EFST_FOOD_STR_CASH')[0], '힘 증가 음식(캐시)')
        self.assertEqual(status_text('EFST_CASH_PLUSEXP')[0], '경험치 증가(캐시)')
        self.assertEqual(status_text('EFST_INC_AGI')[0], '민첩 증가')

    def test_internal_states_flagged(self):
        for sym in ('EFST_GET_CNT_UNREAD_RODEX_CHARDB', 'EFST_AID_PERIOD_PLUSEXP', 'EFST_PCCAFE_PLAY_TIME'):
            self.assertTrue(status_text(sym)[2], sym)
        self.assertFalse(status_text('EFST_FOOD_STR_CASH')[2])

    def test_unknown_symbol_not_translated(self):
        self.assertIsNone(status_text('EFST_ZZZ_QQQ')[0])


class ResolverTest(unittest.TestCase):
    def test_user_named_buff_wins(self):
        d = DisplayNames(FakeGame(FakeNames({993: '어깨동무'}, {993: 'user'})))
        n = d.status(993)
        self.assertEqual((n.text, n.confidence), ('어깨동무', CONFIRMED))
        self.assertIn('993', n.raw)

    def test_external_symbol_is_translated_and_marked(self):
        d = DisplayNames(FakeGame(FakeNames({271: 'FOOD_STR_CASH'}, {271: 'rathena'})))
        n = d.status(271)
        self.assertEqual(n.text, '힘 증가 음식(캐시)')
        self.assertEqual(n.confidence, DERIVED)
        self.assertIn('FOOD_STR_CASH', n.raw)          # 원문은 Tooltip/개발자 모드용으로만

    def test_untranslatable_symbol_hidden_from_users(self):
        d = DisplayNames(FakeGame(FakeNames({77: 'ZZZ_QQQ'}, {77: 'rathena'})))
        n = d.status(77)
        self.assertTrue(n.text.startswith('알 수 없는 효과'))   # 영문 Symbol을 화면에 내보내지 않는다
        self.assertNotIn('ZZZ', n.with_raw(False))
        self.assertIn('ZZZ_QQQ', n.with_raw(True))     # 개발자 모드에서는 보여 준다

    def test_missing_name_is_not_a_number(self):
        d = DisplayNames(FakeGame())
        self.assertTrue(d.status(1234).text.startswith('알 수 없는 효과'))
        self.assertEqual(d.skill(999).text, '알 수 없는 스킬')

    def test_item_map_job_monster(self):
        d = DisplayNames(FakeGame())
        self.assertEqual(d.item(12883).text, '올마이티')
        self.assertEqual(d.map('prontera').text, '프론테라')
        self.assertEqual(d.map('unknown_map').confidence, UNKNOWN)
        self.assertEqual(d.job(4262).text, '인퀴지터')
        self.assertEqual(d.monster(1002).text, '포링')
        self.assertEqual(d.monster(9999).text, '알 수 없는 몬스터')

    def test_tooltip_has_source(self):
        d = DisplayNames(FakeGame(FakeNames({271: 'FOOD_STR_CASH'}, {271: 'rathena'})))
        tip = d.status(271).tooltip('남은 시간 12:34')
        self.assertIn('힘 증가 음식(캐시)', tip)
        self.assertIn('확인 필요', tip)
        self.assertIn('남은 시간', tip)


if __name__ == '__main__':
    unittest.main()


class StatusNamesTest(unittest.TestCase):
    """실제 Replay에서 '알 수 없는 효과'로 나오던 것: 내부 상태는 숨기고, 이름은 추정(◐)으로."""

    def test_postdelay_and_period_are_internal(self):
        from core.display import status_text
        for sym in ('POSTDELAY', 'PERIOD_PLUSEXP_2ND', 'ON_PUSH_CART'):
            text, conf, internal = status_text(sym)
            self.assertTrue(internal, sym)
            self.assertTrue(text)

    def test_common_buffs_named_as_estimate(self):
        from core.display import DERIVED, status_text
        self.assertEqual(status_text('BLESSING')[:2], ('블레싱', DERIVED))
        self.assertEqual(status_text('EXPLOSIONSPIRITS')[0], '폭기')
        self.assertEqual(status_text('S_LIFEPOTION')[0], '생명력 회복 물약(소)')
        self.assertFalse(status_text('BLESSING')[2])