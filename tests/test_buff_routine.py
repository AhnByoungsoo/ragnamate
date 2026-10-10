"""버프 루틴: 리플레이에서 '실제로 버프가 걸린 스킬'만 골라 순서를 만든다(게임 입력 없음)."""
import unittest
from types import SimpleNamespace

from helpers import ev  # noqa: F401  (sys.path 설정 포함)

from core import buff_routine as BR

ME = 1000
PC, MOB = 0, 5


def parsed(events, actors=None):
    return SimpleNamespace(player_aid=ME, events=sorted(events, key=lambda e: e.timestamp_ms),
                           actors=actors or {})


def use(t, skill, target=None):
    return ev(t, 'SKILL_USE', ME, target, skill=skill)


def buff(t, who, efst=1, ms=60000):
    return ev(t, 'BUFF_START', who, efst=efst, max_ms=ms, remain_ms=ms)


class AnalyzeTest(unittest.TestCase):
    def test_only_skills_followed_by_a_state_count_as_buffs(self):
        """이름이 아니라 '직후에 상태가 생겼는가'로 판정한다."""
        a = BR.analyze(parsed([use(0, 34), buff(100, ME),          # 상태가 따라옴 → 버프
                               use(5000, 99), use(9000, 99)]),     # 아무 상태도 안 생김 → 확인 못 함
                       skill_name=lambda s: {34: '블레싱', 99: '수수께끼'}[s])
        self.assertEqual([r['name'] for r in a['party']], ['블레싱'])
        self.assertEqual([(r['name'], r['count'], r['reason']) for r in a['unknown']], [('수수께끼', 2, '상태 없음')])

    def test_monster_targets_are_not_buffs(self):
        a = BR.analyze(parsed([use(0, 500, 2000), buff(50, 2000)], actors={2000: dict(obj_type=MOB, name='포링')}))
        self.assertEqual(a['party'], [])
        self.assertEqual(a['target'], [])

    def test_party_member_targets_are_collected(self):
        actors = {2001: dict(obj_type=PC, name='홍길동'), 2002: dict(obj_type=PC, name='김철수')}
        events = []
        for i, who in enumerate((2001, 2002, 2001)):
            events += [use(i * 1000, 34, who), buff(i * 1000 + 80, who)]
        a = BR.analyze(parsed(events, actors), skill_name=lambda s: '블레싱')
        self.assertEqual(a['target'][0]['targets'], ['홍길동', '김철수'])
        self.assertEqual(a['members'], ['홍길동', '김철수'])
        self.assertEqual(a['party'], [])

    def test_short_states_from_attack_skills_are_not_buffs(self):
        """공격 스킬도 0.1초짜리 상태를 남긴다 — 실측 지속시간이 짧으면 버프 루틴에 넣지 않는다."""
        events = []
        for i in range(10):
            events += [use(i * 1000, 2327), buff(i * 1000 + 30, ME, ms=90)]       # 천라지망: 실측 90ms
        a = BR.analyze(parsed(events), skill_name=lambda s: '천라지망')
        self.assertEqual(a['party'], [])
        self.assertEqual([(r['count'], r['reason']) for r in a['unknown']], [(10, '너무 짧음')])

    def test_measured_duration_is_reported_and_never_guessed(self):
        a = BR.analyze(parsed([use(0, 34), buff(100, ME, ms=240000),
                               use(5000, 34), buff(5100, ME, ms=120000)]), skill_name=lambda s: '블레싱')
        self.assertEqual(a['party'][0]['remain_ms'], 180000)                      # 중앙값
        b = BR.analyze(parsed([use(0, 34), ev(100, 'BUFF_START', ME, efst=1),
                               use(1000, 34), ev(1100, 'BUFF_START', ME, efst=1)]))
        self.assertIsNone(b['party'][0]['remain_ms'])                             # 모르면 None (추정 금지)

    def test_occasional_overlap_is_not_a_buff(self):
        """공격 스킬을 10번 썼는데 한 번만 상태가 겹쳤다면 버프로 보지 않는다."""
        events = [use(i * 1000, 77) for i in range(10)] + [buff(80, ME)]
        a = BR.analyze(parsed(events))
        self.assertEqual(a['party'], [])
        self.assertEqual(a['unknown'][0]['count'], 10)


class RoutineTest(unittest.TestCase):
    def analysis(self):
        return dict(party=[dict(skill_id=34, name='블레싱', count=5, scope=BR.PARTY)],
                    target=[dict(skill_id=29, name='민첩성 증가', count=4, scope=BR.TARGET, targets=['홍길동'])],
                    members=['홍길동', '김철수'], unknown=[])

    def test_party_first_then_each_member(self):
        r = BR.build(self.analysis(), hotkeys={34: 'F1', 29: 'F2'})
        self.assertEqual([(s.scope, s.target) for s in r.steps],
                         [(BR.PARTY, ''), (BR.TARGET, '홍길동'), (BR.TARGET, '김철수')])
        self.assertEqual(r.steps[1].label, '민첩성 증가 → 홍길동  [F2]')

    def test_saved_and_loaded(self):
        r = BR.build(self.analysis(), hotkeys={34: 'F1'})
        again = BR.Routine.from_dict(r.to_dict())
        self.assertEqual([s.label for s in again.steps], [s.label for s in r.steps])

    def test_macro_export_leaves_out_targeted_buffs(self):
        """파티·자신 버프만 키 순서로 — 파티원을 자동으로 고르는 동작은 만들지 않는다."""
        r = BR.build(self.analysis(), hotkeys={34: 'F1', 29: 'F2'})
        steps = BR.macro_steps(r, gap_ms=500)
        self.assertEqual(steps, [dict(type='tap', key='F1')])
        r.steps.insert(1, BR.Step(42, '속도증가', BR.PARTY, '', 'F3'))
        self.assertEqual(BR.macro_steps(r, gap_ms=500),
                         [dict(type='tap', key='F1'), dict(type='wait', ms=500), dict(type='tap', key='F3')])

    def test_macro_export_skips_steps_without_a_hotkey(self):
        r = BR.build(self.analysis())                       # 단축키를 적지 않음
        self.assertEqual(BR.macro_steps(r), [])


if __name__ == '__main__':
    unittest.main()
