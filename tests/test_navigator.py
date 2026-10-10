"""RO Navigator 테스트."""
import json
import os
import shutil
import tempfile
import unittest

from helpers import ROOT

from core.models import ItemEntry, PriceInfo
from database.database import Database
from navigator import navigation
from navigator.guide_service import GuideService
from navigator.models import FAIL, PASS, UNKNOWN, Guide, GuideStep, Requirement, Source, navi_command
from navigator.player_state import PlayerState, add_entries
from navigator.repository import GuideRepository
from navigator.requirements import evaluate, readiness
from navigator.search import SearchEngine, choseong
from navigator.validation import validate

GUIDE_DIR = os.path.join(ROOT, 'data', 'guides')
SHARD = 22687   # 사념의 파편


class FakeMarket:
    def fetch(self, items, force=False):
        return {i: PriceInfo(i, 83_000, 90_000, 95_000, 4, 0) for i in items}, []


def state(**kw):
    st = PlayerState(character_name='tester', **kw)
    return st


def temp_repo(guides, jobs=None):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, 'test.json'), 'w', encoding='utf-8') as f:
        json.dump({'pack': 'test', 'guides': [g.to_dict() for g in guides]}, f, ensure_ascii=False)
    with open(os.path.join(d, 'job_tree.json'), 'w', encoding='utf-8') as f:
        json.dump({'jobs': jobs or []}, f)
    return GuideRepository(d, Database(os.path.join(d, 't.db'))).load()


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = GuideService(GUIDE_DIR)

    def top(self, q):
        r = self.svc.search(q, log_miss=False)
        return r[0][1].title if r else None

    def test_official_and_alias(self):
        self.assertEqual(self.top('전사자의 무덤'), '전사자의 무덤')
        self.assertEqual(self.top('전사자'), '전사자의 무덤')
        titles = [g.title for _, g, _ in self.svc.search('생던', log_miss=False)]
        self.assertIn('전사자의 무덤', titles)

    def test_partial_choseong_typo(self):
        self.assertEqual(self.top('인퀴'), '인퀴지터 전직')
        self.assertEqual(choseong('아크메이지'), 'ㅇㅋㅁㅇㅈ')
        self.assertEqual(self.top('ㅇㅋㅁㅇㅈ'), '아크 메이지 전직')
        self.assertEqual(self.top('인큐지터'), '인퀴지터 전직')

    def test_no_result(self):
        self.assertEqual(self.svc.search('없는콘텐츠xyz', log_miss=False), [])

    def test_category_filter(self):
        r = self.svc.search('전사자', types={'JOB_CHANGE'}, log_miss=False)
        self.assertTrue(all(g.type == 'JOB_CHANGE' for _, g, _ in r))


class RequirementTest(unittest.TestCase):
    def test_level_job(self):
        st = state(job_id=4077, level=210, job_level=70)
        self.assertEqual(evaluate(Requirement('LEVEL', min=200), st).result, PASS)
        self.assertEqual(evaluate(Requirement('LEVEL', min=250), st).result, FAIL)
        self.assertEqual(evaluate(Requirement('JOB', jobs=[4077]), st).result, PASS)
        self.assertEqual(evaluate(Requirement('JOB', jobs=[4061]), st).result, FAIL)
        self.assertEqual(evaluate(Requirement('LEVEL', min=1), PlayerState()).result, UNKNOWN)

    def test_item_sum_and_shortage(self):
        st = state()
        add_entries(st, [ItemEntry('인벤토리', SHARD, 'x', 3), ItemEntry('창고1', SHARD, 'x', 7),
                         ItemEntry('창고3', SHARD, 'x', 5)])
        self.assertEqual(st.holding(SHARD).total, 15)
        self.assertEqual(st.holding(SHARD).by_source, {'인벤토리': 3, '창고1': 7, '창고3': 5})
        r = evaluate(Requirement('ITEM', item_id=SHARD, item_name='사념의 파편', quantity=20), st)
        self.assertEqual((r.result, r.have, r.need), (FAIL, 15, 20))
        self.assertEqual(evaluate(Requirement('ITEM', item_id=SHARD, quantity=1), PlayerState()).result, UNKNOWN)

    def test_quest_unknown_and_manual(self):
        repo = temp_repo([Guide('q1', 'QUEST', '선행', sources=[Source('x', provider='official')])])
        st = state()
        req = Requirement('QUEST', '선행 완료', guide_id='q1')
        self.assertEqual(evaluate(req, st, repo).result, UNKNOWN)       # 추정으로 완료 처리하지 않음
        repo.set_quest_state('tester', 'q1', 'COMPLETED')
        self.assertEqual(evaluate(req, st, repo).result, PASS)
        repo.set_quest_state('tester', 'q1', 'NOT_COMPLETED')
        self.assertEqual(evaluate(req, st, repo).result, FAIL)

    def test_readiness_excludes_unknown(self):
        class R:
            def __init__(self, res):
                self.result, self.requirement = res, Requirement('OTHER')
        rd = readiness([R(PASS), R(PASS), R(FAIL), R(UNKNOWN)])
        self.assertEqual((rd['percent'], rd['unknown']), (67, 1))


class ServiceTest(unittest.TestCase):
    def test_tomb_materials_and_cost(self):
        svc = GuideService(GUIDE_DIR, market=FakeMarket())
        st = state(level=210, job_id=4077)
        add_entries(st, [ItemEntry('인벤토리', SHARD, 'x', 3), ItemEntry('창고1', SHARD, 'x', 7),
                         ItemEntry('창고3', SHARD, 'x', 5)])
        svc.state = st
        d = svc.detail('dungeon_tomb_of_the_fallen')
        m = [x for x in d['materials'] if x['item_id'] == SHARD][0]
        self.assertEqual((m['need'], m['have'], m['missing'], m['unit'], m['cost']), (20, 15, 5, 83_000, 415_000))
        self.assertEqual(d['prep_cost'], 415_000)
        kinds = {r.requirement.kind: r.result for r in d['results']}
        self.assertEqual(kinds, {'LEVEL': PASS, 'ITEM': FAIL, 'ZENY': UNKNOWN})
        self.assertTrue(any('사념의 파편' in x for x in d['diagnosis']))

    def test_next_job_inquisitor(self):
        svc = GuideService(GUIDE_DIR)
        svc.state = state(job_id=4077, level=200, job_level=70)       # 수라(전승)
        nxt = svc.next_jobs()
        self.assertEqual([n['job']['id'] for n in nxt], [4262])
        g = nxt[0]['guide']
        self.assertEqual(g.title, '인퀴지터 전직')
        self.assertEqual(g.steps[0].navi_command, '/navi prt_in 250/129')
        self.assertTrue(all(r.result == PASS for r in svc.evaluate(g)))
        self.assertEqual(g.sources[0].provider, 'official')

    def test_no_guide_for_jobs_without_official_page(self):
        svc = GuideService(GUIDE_DIR)
        self.assertIsNone(svc.repo.get('job_change_4077'))      # 전승 수라: 공식 페이지 없음 → 지어내지 않음
        self.assertIsNone(svc.repo.get('job_change_4303'))


class NavigationTest(unittest.TestCase):
    def test_navi_command(self):
        self.assertEqual(navi_command('lighthalzen', 123, 184), '/navi lighthalzen 123/184')
        self.assertEqual(navi_command('', 1, 2), '')
        self.assertEqual(GuideStep(1, 't', map='prt_in', x=250, y=129).navi_command, '/navi prt_in 250/129')

    def test_dependency_order_and_cycle(self):
        a = Guide('a', 'DUNGEON', 'A', prerequisites=['b'])
        b = Guide('b', 'QUEST', 'B', requirements=[Requirement('EPISODE', guide_id='c')])
        c = Guide('c', 'EPISODE', 'C')
        repo = temp_repo([a, b, c])
        self.assertEqual(navigation.prerequisite_order(repo, 'a'), ['c', 'b'])
        repo.guides['c'].prerequisites = ['a']
        with self.assertRaises(navigation.CycleError):
            navigation.prerequisite_order(repo, 'a')
        self.assertTrue(navigation.find_cycles(repo))

    def test_job_tree(self):
        repo = GuideRepository(GUIDE_DIR).load()
        self.assertEqual({j['id'] for j in navigation.next_jobs(repo.jobs, 4061)}, {4255})
        self.assertEqual({j['id'] for j in navigation.next_jobs(repo.jobs, 5)}, {10, 18})
        path = [j['id'] for j in navigation.job_path(repo.jobs, 4255)]
        self.assertEqual(path, [4010, 4061, 4255])       # 하이 위저드 → 워록(전승) → 아크 메이지 (전생 경로는 가이드 없음)


class ImportExportValidationTest(unittest.TestCase):
    def test_roundtrip(self):
        g = Guide('x1', 'CRAFT', '테스트 제작', aliases=['테제'], steps=[GuideStep(1, 's', map='prontera', x=1, y=2)],
                  requirements=[Requirement('ITEM', item_id=909, item_name='젤로피', quantity=3, optional=True)],
                  sources=[Source('t', provider='user')])
        repo = temp_repo([g])
        out = os.path.join(tempfile.mkdtemp(), 'pack.json')
        self.assertEqual(repo.export_pack(out), 1)
        repo2 = temp_repo([])
        self.assertEqual(repo2.import_pack(out), 1)
        g2 = repo2.get('x1')
        self.assertEqual((g2.aliases, g2.steps[0].navi_command, g2.requirements[0].optional),
                         (['테제'], '/navi prontera 1/2', True))
        with self.assertRaises(ValueError):
            bad = os.path.join(tempfile.mkdtemp(), 'bad.json')
            json.dump({'x': 1}, open(bad, 'w'))
            repo2.import_pack(bad)

    def test_validation_flags(self):
        g = Guide('v1', 'QUEST', 'V', prerequisites=['missing'], steps=[GuideStep(1, 's', x=1, y=2)])
        issues = validate(temp_repo([g]))
        text = ' '.join(i[2] for i in issues)
        self.assertIn('출처 없음', text)
        self.assertIn('잘못된 prerequisite', text)
        self.assertIn('맵 ID 없음', text)

    def test_seed_packs_valid(self):
        repo = GuideRepository(GUIDE_DIR).load()
        self.assertEqual(repo.load_errors, [])
        errors = [i for i in validate(repo) if i[0] == 'ERROR']
        self.assertEqual(errors, [])
        self.assertTrue(all(g.sources for g in repo.all()))

    def test_official_parser_fixture(self):
        from navigator.providers.official_provider import parse_job_page
        html = ('<h3>인퀴지터 전직 퀘스트</h3><p>베이스 레벨 200, 잡 레벨 70 상태의 전승 수라는 전직할 수 있습니다.</p>'
                '<p>1. 프론테라의 여관 종업원 NPC(prt_in 250 129)에게 대화를 걸어 퀘스트를 수락합니다.</p>'
                '<p>2. 젤로피 20개를 구해옵니다.</p><p>3. 보우 1개를 지급해줍니다.</p>')
        tree = {4262: dict(id=4262, name='인퀴지터', parents=[4077], official_title='인퀴지터'),
                4077: dict(id=4077, name='수라(전승)', parents=[])}
        g = parse_job_page(4262, html, lambda n: {'젤로피': 909, '보우': 1701}.get(n), tree, '2026-09-20')
        self.assertEqual((g.min_level, g.start_map, g.start_x, g.start_y, g.start_npc), (200, 'prt_in', 250, 129, '여관 종업원'))
        items = [(r.item_id, r.quantity) for s in g.steps for r in s.requirements]
        self.assertEqual(items, [(909, 20)])            # NPC가 '지급'하는 아이템은 준비물이 아님
        self.assertIsNone(parse_job_page(4077, html, lambda n: None, tree))   # 공식 제목 불일치 → 없음


LIST_HTML = """<table class="questMain"><tbody>
<tr><td><a href="/guide/runemidgarts/questview.asp?curpage=13&QuestSeq=123">수렵 1과 일일 퀘스트 <br/>(사나운 날벌레)</a></td></tr>
<tr><td><a href="/guide/runemidgarts/questview.asp?curpage=13&QuestSeq=124">한방약 퀘스트</a></td></tr>
</tbody></table>"""

VIEW_HTML = """<table class="questDetail"><caption>퀘스트 안내</caption>
<tr><th scope="row">퀘스트명</th><td colspan="3" class="merger">아루나펠츠를 위하여</td></tr>
<tr><th scope="row">지역</th><td><a href="#">사막도시 모로크 <br/>(피라미드앞)</a></td></tr>
<tr><th scope="row">시작NPC</th><td>이국의 상인</td></tr>
<tr><th scope="row">시작레벨</th><td>40이상</td></tr>
<tr><th scope="row">시작아이템</th><td>붉은 허브 3개</td></tr>
<tr><th scope="row">직업</th><td>-</td></tr>
<tr><th scope="row">선행퀘스트</th><td>돌 줍는 노인</td></tr>
<tr><th scope="row">보상</th><td>경험치 : 250,000</td></tr>
</table>"""


class OfficialQuestTest(unittest.TestCase):
    """공식 퀘스트 수집: 페이지에 있는 것만 옮기고, 없는 좌표는 만들지 않는다."""

    def test_parse_list_with_markup_in_title(self):
        from navigator.providers.official_quests import parse_list
        rows = parse_list(LIST_HTML)
        self.assertEqual(rows[0], (123, '수렵 1과 일일 퀘스트 (사나운 날벌레)'))
        self.assertEqual(rows[1][1], '한방약 퀘스트')

    def test_parse_quest_fields(self):
        from navigator.providers.official_quests import parse_quest
        rec = parse_quest(51, VIEW_HTML)
        self.assertEqual(rec['title'], '아루나펠츠를 위하여')
        self.assertEqual(rec['시작NPC'], '이국의 상인')
        self.assertEqual(rec['지역'], '사막도시 모로크 (피라미드앞)')
        self.assertEqual(parse_quest(1, '<html>표 없음</html>'), {})

    def test_to_guide_requirements_and_no_coordinates(self):
        from navigator.providers.official_quests import parse_quest, to_guide
        g = to_guide(parse_quest(51, VIEW_HTML), item_lookup=lambda n: 507 if n == '붉은 허브' else None)
        self.assertEqual(g.id, 'official_quest_51')
        self.assertEqual(g.min_level, 40)
        self.assertEqual(g.start_npc, '이국의 상인')
        kinds = {r.kind: r for r in g.requirements}
        self.assertEqual(kinds['ITEM'].item_id, 507)
        self.assertEqual(kinds['ITEM'].quantity, 3)
        self.assertEqual(kinds['QUEST'].description, '선행 퀘스트: 돌 줍는 노인')
        self.assertNotIn('JOB', kinds)                       # '-'는 조건으로 만들지 않는다
        self.assertEqual(g.steps[0].navi_command, '')        # 좌표가 없으므로 /navi 없음
        self.assertEqual(g.sources[0].provider, 'official')


if __name__ == '__main__':
    unittest.main()


class RewardsAndFindTest(unittest.TestCase):
    """콘텐츠 찾기(시작 레벨 순 · 보상 아이템)와 보상 문구 해석."""

    def test_parse_official_reward_text(self):
        from navigator.rewards import parse_rewards
        g = Guide(id='q', type='QUEST', title='도둑 퇴치1',
                  rewards=['경험치 : 5,000 직업경험치 : 2,500 기타 : 없음 TE 화이트 슬림 포션 5개'])
        r = parse_rewards(g)
        self.assertEqual((r.exp, r.job_exp, r.items), ('5,000', '2,500', ['TE 화이트 슬림 포션 5개']))
        self.assertEqual(r.summary(), 'EXP 5,000 · Job 2,500 · TE 화이트 슬림 포션 5개')
        none = parse_rewards(Guide(id='n', type='QUEST', title='x', rewards=['경험치 : 없음 직업경험치 : 없음 기타 : 없음']))
        self.assertEqual(none.summary(), '')
        other = parse_rewards(Guide(id='j', type='JOB_CHANGE', title='x', rewards=['소드맨(으)로 전직']))
        self.assertEqual(other.items, ['소드맨(으)로 전직'])

    def test_find_content_level_range_and_item(self):
        svc = GuideService(GUIDE_DIR)
        gs = svc.find_content(70, 80)
        self.assertTrue(gs)
        self.assertTrue(all(70 <= g.min_level <= 80 for g in gs))
        self.assertEqual([g.min_level for g in gs], sorted(g.min_level for g in gs))      # 레벨 순
        pot = svc.find_content(item='포션')
        self.assertTrue(pot)
        from navigator.rewards import matches_item
        self.assertTrue(all(matches_item(g, '포션') for g in pot))
        self.assertFalse(any(g.type == 'JOB_CHANGE' for g in svc.find_content()))
    def test_chapter_guides(self):
        """챕터 분류: 인벤 정리글 단계 + 공식 좌표. 미리 준비할 재료만 필수, 진행 중 얻는 재료는 선택."""
        svc = GuideService(GUIDE_DIR)
        ch = {g.id: g for g in svc.repo.all() if g.type == 'CHAPTER'}
        self.assertEqual(set(ch), {'chapter1_song_of_worldtree', 'chapter1_side_daily', 'chapter2_shard_of_end',
                                   'chapter2_side_daily'})
        c1, c2 = ch['chapter1_song_of_worldtree'], ch['chapter2_shard_of_end']
        self.assertEqual(c1.type_label, '챕터')
        self.assertGreaterEqual(len(c1.steps), 20)
        self.assertEqual(c1.steps[0].navi_command, '/navi prt_fild05 353/252')
        need = lambda g: {(r.item_name, r.quantity) for r in g.all_item_requirements() if not r.optional}  # noqa: E731
        self.assertEqual(need(c1), {('드래곤의 비늘', 5)})
        self.assertEqual(need(c2), {('사과', 5)})
        self.assertIn('prep', c2.strategy)
        self.assertEqual({s.provider for s in c2.sources}, {'official', 'community', 'ingame'})   # 공지·공략글·게임 데이터
        self.assertTrue(any(l.startswith('공식 퀘스트: ') for s in c2.steps for l in s.description.splitlines()))
        titles = [g.title for _, g, _ in svc.search('챕터', types={'CHAPTER'}, log_miss=False)]
        self.assertIn('챕터 2 종말의 파편', titles)
