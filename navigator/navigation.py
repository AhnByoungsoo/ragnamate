"""Guide 선행관계 Dependency Graph + 직업 트리 탐색."""


class CycleError(Exception):
    pass


def prerequisite_order(repo, gid):
    """gid를 하기 전에 해야 하는 Guide 순서(먼저 할 것부터). 순환이면 CycleError."""
    order, state = [], {}

    def visit(g, path):
        if state.get(g) == 'done':
            return
        if state.get(g) == 'visiting':
            raise CycleError(' → '.join(path + [g]))
        state[g] = 'visiting'
        guide = repo.get(g)
        deps = list(guide.prerequisites) if guide else []
        if guide:
            deps += [r.guide_id for r in guide.requirements if r.kind in ('QUEST', 'EPISODE') and r.guide_id]
        for d in dict.fromkeys(deps):
            visit(d, path + [g])
        state[g] = 'done'
        order.append(g)

    visit(gid, [])
    return order[:-1]


def find_cycles(repo):
    cycles = []
    for g in repo.guides:
        try:
            prerequisite_order(repo, g)
        except CycleError as e:
            cycles.append(str(e))
    return cycles


def relation_tree(repo, gid, depth=0, seen=None):
    """TreeView용 {'id','title','children'} (선행 → 이 Guide 방향 트리)."""
    seen = seen or set()
    g = repo.get(gid)
    node = dict(id=gid, title=g.title if g else '%s (Guide 없음)' % gid, children=[])
    if gid in seen or depth > 8:
        return node
    seen = seen | {gid}
    deps = list(g.prerequisites) if g else []
    if g:
        deps += [r.guide_id for r in g.requirements if r.kind in ('QUEST', 'EPISODE') and r.guide_id]
    node['children'] = [relation_tree(repo, d, depth + 1, seen) for d in dict.fromkeys(deps)]
    return node


# ---------------------------------------------------------------- 직업 트리
def next_jobs(jobs, job_id):
    """현재 직업에서 전직 가능한 다음 직업 노드 목록."""
    return [j for j in jobs.values() if job_id in j.get('parents', [])]


def job_path(jobs, job_id):
    """루트부터 현재 직업까지의 대표 경로(첫 번째 부모 기준)."""
    path, cur, seen = [], jobs.get(job_id), set()
    while cur and cur['id'] not in seen:
        seen.add(cur['id'])
        path.append(cur)
        cur = jobs.get(cur['parents'][0]) if cur.get('parents') else None
    return list(reversed(path))
