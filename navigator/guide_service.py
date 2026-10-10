"""Navigator 서비스: 검색 → 상세 → 준비도(창고/시세 연동) → 진행."""
import logging
import os

from core.analysis import unit_price
from navigator import navigation
from navigator.models import FAIL, PASS, UNKNOWN
from navigator.player_state import PlayerState
from navigator.repository import GuideRepository
from navigator.requirements import diagnose, evaluate, readiness
from navigator.search import SearchEngine

log = logging.getLogger(__name__)


class GuideService:
    def __init__(self, guide_dir, db=None, market=None, history=None, item_db=None, map_db=None, settings=None):
        self.repo = GuideRepository(guide_dir, db).load()
        self.search_engine = SearchEngine(self.repo).build()
        self.market = market
        self.history = history
        self.item_db = item_db
        self.map_db = map_db
        self.settings = settings or {}
        self.state = PlayerState()

    def reload(self):
        self.repo.load()
        self.search_engine.build()

    # ---------------------------------------------------------------- 검색
    def search(self, query, types=None, log_miss=True):
        res = self.search_engine.search(query, types)
        if not res and log_miss:
            self.repo.log_miss(query)
        return res

    # ---------------------------------------------------------------- 상세
    def evaluate(self, guide, st=None):
        st = st or self.state
        return [evaluate(r, st, self.repo) for r in guide.requirements]

    def detail(self, gid, st=None):
        st = st or self.state
        g = self.repo.get(gid)
        if not g:
            return None
        results = self.evaluate(g, st)
        mats = self.materials(g, st)
        try:
            prereq = navigation.prerequisite_order(self.repo, gid)
            cycle = None
        except navigation.CycleError as e:
            prereq, cycle = [], str(e)
        entry_mats = [m for m in mats if m['entry']]
        return dict(
            guide=g, results=results, readiness=readiness(results), diagnosis=diagnose(results, self.repo),
            materials=mats, prep_cost=sum(m['cost'] or 0 for m in mats if not m['optional']),
            entry_cost=sum(m['cost'] or 0 for m in entry_mats), unpriced=sum(1 for m in mats if m['missing'] and m['cost'] is None),
            prerequisites=prereq, cycle=cycle, relation=navigation.relation_tree(self.repo, gid),
            records=self.my_records(g), progress=self.repo.progress(st.character_name, gid))

    def materials(self, g, st=None):
        """필요 재료 × (인벤토리+카트+창고 보유) × 부족분 시세."""
        st = st or self.state
        entry_ids = {r.item_id or r.item_name for r in g.requirements if r.kind == 'ITEM'}
        reqs = g.all_item_requirements()
        rows = []
        for r in reqs:
            h = st.holding(r.item_id) if r.item_id else None
            have = h.total if h else 0
            missing = max(0, r.quantity - have)
            rows.append(dict(item_id=r.item_id, name=r.item_name or (self.item_db.name(r.item_id) if self.item_db else ''),
                             need=r.quantity, have=have if (st.inventory_known or st.storage_known) else None,
                             by_source=h.by_source if h else {}, missing=missing, optional=r.optional,
                             entry=(r.item_id or r.item_name) in entry_ids, source=r.source, price=None,
                             unit=None, cost=None, where=g.material_sources.get(str(r.item_id), [])))
        ids = {m['item_id']: m['name'] for m in rows if m['item_id'] and m['missing']}
        prices = {}
        if ids and self.market:
            try:
                prices = self.market.fetch(ids)[0]
            except Exception:
                logging.getLogger('market').exception('Navigator 시세 조회 실패')
        basis = self.settings.get('price_basis', 'lowest')
        for m in rows:
            p = prices.get(m['item_id'])
            m['price'] = p
            m['unit'] = unit_price(p, basis)
            m['cost'] = m['unit'] * m['missing'] if m['unit'] is not None and m['missing'] else (0 if not m['missing'] else None)
        return rows

    def why_cant_enter(self, gid, st=None):
        g = self.repo.get(gid)
        results = self.evaluate(g, st)
        fix = [r for r in results if r.result in (FAIL, UNKNOWN) and not r.requirement.optional]
        return dict(results=results, diagnosis=diagnose(results, self.repo),
                    fix_guides=[r.link_guide for r in fix if r.link_guide and self.repo.get(r.link_guide)])

    # ---------------------------------------------------------------- 진행
    def set_step(self, gid, current, done):
        self.repo.set_progress(self.state.character_name, gid, current, done)

    # ---------------------------------------------------------------- 전직 / 콘텐츠
    def next_jobs(self, job_id=None):
        jid = self.state.job_id if job_id is None else job_id
        if jid is None:
            return []
        out = []
        for node in navigation.next_jobs(self.repo.jobs, jid):
            out.append(dict(job=node, guide=self.repo.get('job_change_%d' % node['id'])))
        return out

    def available_content(self, st=None):
        """레벨/직업 조건이 PASS(또는 조건 없음)인 Guide. 추천 순위는 매기지 않는다."""
        st = st or self.state
        out = []
        for g in self.repo.all():
            if g.type == 'JOB_CHANGE':
                continue
            rs = [evaluate(r, st, self.repo) for r in g.requirements if r.kind in ('LEVEL', 'JOB')]
            if all(r.result == PASS for r in rs):
                out.append(g)
        return sorted(out, key=lambda g: (g.type, g.title))

    def find_content(self, level_from=None, level_to=None, item='', only_available=False, st=None):
        """콘텐츠 찾기: 시작 레벨 구간 + 보상 아이템. 레벨 순으로 정렬한다.
        레벨 정보가 없는 Guide는 레벨 구간을 고르면 빠지고, 보상 검색만 할 때는 목록 끝에 붙는다.
        only_available이면 레벨/직업 조건이 PASS인 것만(available_content와 같은 기준)."""
        from navigator.rewards import matches_item
        pool = self.available_content(st) if only_available else [g for g in self.repo.all() if g.type != 'JOB_CHANGE']
        ranged = level_from is not None or level_to is not None
        out = []
        for g in pool:
            lv = g.min_level
            if lv is None and ranged:
                continue
            if lv is not None and ((level_from is not None and lv < level_from) or
                                   (level_to is not None and lv > level_to)):
                continue
            if matches_item(g, item):
                out.append(g)
        return sorted(out, key=lambda g: (g.min_level is None, g.min_level or 0, g.title))

    def characters(self):
        """Replay 기록에서 최근 캐릭터 목록."""
        if not self.history:
            return []
        seen = {}
        for s in self.history.list():
            if s['character'] and s['character'] not in seen:
                seen[s['character']] = s
        return list(seen.values())

    def my_records(self, g):
        if not self.history or not g.related_maps:
            return []
        maps = set(g.related_maps)
        out = []
        for s in self.history.list():
            ms = {m.strip() for m in (s['map'] or '').split(',')}
            if ms & maps:
                out.append(s)
        return out

    def map_guides(self, map_id):
        """Replay 맵 → 관련 Guide (related_maps / start_map / 단계 맵)."""
        return [g for g in self.repo.all()
                if map_id in g.related_maps or g.start_map == map_id or any(s.map == map_id for s in g.steps)]
