"""별칭 CSV 가져오기: guide_id,alias[,alias...]  → data/guides/user/aliases.json 에 병합.

Guide 본문을 바꾸지 않고 검색어만 늘릴 때 사용(예: 검색 실패 로그에서 찾은 약칭 등록).
"""
import csv
import json
import os


def import_aliases(repo, path):
    added = 0
    user_path = os.path.join(repo.dir, 'user', 'aliases.json')
    data = {}
    if os.path.exists(user_path):
        with open(user_path, encoding='utf-8') as f:
            data = json.load(f)
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f):
            if len(row) < 2 or not repo.get(row[0].strip()):
                continue
            cur = set(data.get(row[0].strip(), []))
            new = {a.strip() for a in row[1:] if a.strip()} - cur
            added += len(new)
            data[row[0].strip()] = sorted(cur | new)
    os.makedirs(os.path.dirname(user_path), exist_ok=True)
    with open(user_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    apply_aliases(repo)
    return added


def apply_aliases(repo):
    user_path = os.path.join(repo.dir, 'user', 'aliases.json')
    if not os.path.exists(user_path):
        return
    with open(user_path, encoding='utf-8') as f:
        for gid, aliases in json.load(f).items():
            g = repo.get(gid)
            if g:
                g.aliases = sorted(set(g.aliases) | set(aliases))
