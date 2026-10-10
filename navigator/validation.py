"""Guide 검사 도구 (Developer Mode)."""
import datetime

from navigator import navigation

STALE_DAYS = 365


def validate(repo, item_db=None, map_db=None, today=None):
    """반환: [(심각도, guide_id, 내용)] 심각도: ERROR/WARN/INFO"""
    today = today or datetime.date.today()
    out = []
    ids = [g.id for g in repo.all()]
    for g in repo.all():
        if not g.sources:
            out.append(('ERROR', g.id, '출처 없음'))
        if not g.steps:
            out.append(('WARN', g.id, '진행 단계 없음'))
        for s in g.steps:
            if (s.x is not None or s.y is not None) and not s.map:
                out.append(('WARN', g.id, 'STEP %d: 좌표는 있으나 맵 ID 없음(/navi 불가)' % s.order))
            if s.map and map_db is not None and map_db.names and s.map not in map_db.names:
                out.append(('ERROR', g.id, 'STEP %d: 존재하지 않는 맵 ID "%s"' % (s.order, s.map)))
        if not any(s.navi_command for s in g.steps) and not g.start_map:
            out.append(('INFO', g.id, 'Navi 가능한 좌표 없음'))
        for r in g.requirements + [r for s in g.steps for r in s.requirements]:
            if r.kind == 'ITEM' and r.item_id and item_db is not None and item_db.items and not item_db.known(r.item_id):
                out.append(('ERROR', g.id, '존재하지 않는 Item ID %s (%s)' % (r.item_id, r.item_name)))
            if r.kind in ('QUEST', 'EPISODE') and r.guide_id and not repo.get(r.guide_id):
                out.append(('ERROR', g.id, '선행 Guide 없음: %s' % r.guide_id))
        for p in g.prerequisites:
            if not repo.get(p):
                out.append(('ERROR', g.id, '잘못된 prerequisite: %s' % p))
        if g.verified_at:
            try:
                age = (today - datetime.date.fromisoformat(g.verified_at[:10])).days
                if age > STALE_DAYS:
                    out.append(('WARN', g.id, '마지막 확인 %s (%d일 경과)' % (g.verified_at[:10], age)))
            except ValueError:
                out.append(('WARN', g.id, 'verified_at 형식 오류'))
        else:
            out.append(('WARN', g.id, '확인일 없음'))
        if g.status not in ('VERIFIED', 'NEEDS_REVIEW', 'COMMUNITY', 'LEGACY'):
            out.append(('WARN', g.id, '알 수 없는 status %s' % g.status))
    dup = {i for i in ids if ids.count(i) > 1}
    for d in dup:
        out.append(('ERROR', d, '중복 Guide ID'))
    for c in navigation.find_cycles(repo):
        out.append(('ERROR', c.split(' → ')[0], '순환 선행관계: ' + c))
    # 직업 트리: 공식 전직 문구에 부모 직업명이 들어있는지 교차검증
    for jid, node in repo.jobs.items():
        g = repo.get('job_change_%d' % jid)
        for p in node.get('parents', []):
            if p not in repo.jobs:
                out.append(('ERROR', 'job_tree', '%s의 부모 %s 없음' % (node['name'], p)))
        if g and node.get('parents') and node['tier'] >= 2:
            names = [n.replace('(전승)', '').replace(' ', '') for p in node['parents'] if p in repo.jobs
                     for n in [repo.jobs[p]['name']] + repo.jobs[p].get('aliases', [])]
            text = g.description.replace(' ', '')
            if not any(n in text for n in names):
                out.append(('WARN', g.id, '공식 조건 문구에 부모 직업명(%s)이 보이지 않음' % ', '.join(names)))
    return out
