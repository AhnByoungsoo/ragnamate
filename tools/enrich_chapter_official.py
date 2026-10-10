"""챕터 Guide STEP 에 게임 클라이언트 공식 퀘스트 정보(OngoingQuestInfoList_True.lub)를 붙인다.

STEP 제목의 퀘스트 이름('그레고어와 드브리스의 추적 · 그것을 원한다면(3)')마다 공식 기록을 찾아
- 설명 끝에 '공식 퀘스트: 제목 — 목표 → NPC 맵 x,y' 줄을 넣고(다시 돌리면 교체)
- STEP 에 좌표가 없으면 첫 퀘스트의 가야 할 곳으로 채운다(공식 공지·공략글 좌표는 그대로 둔다).
게임 패치 뒤 다시 돌리면 된다:  python tools/enrich_chapter_official.py [RO 폴더]
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from navigator.quest_info import QuestInfoDB, key  # noqa: E402

PACK = os.path.join(ROOT, 'data', 'guides', 'packs', 'chapter_worldtree.json')
MARK = '공식 퀘스트: '
SOURCE = dict(title='게임 클라이언트 퀘스트 정보 (System/OngoingQuestInfoList_True.lub)', url='', provider='ingame')


def pieces(step_title):
    """STEP 제목 → 공식 퀘스트 제목 후보. '세계수의 부름 (1)(2)(3)' → (1),(2),(3) 로 펼친다."""
    t = re.sub(r'^\(이어서\)\s*|^최종:\s*', '', step_title)
    t = re.sub(r'\s*\((?:메모리얼 던전|보스|최종 보스|니르홀트|용지락 가는 길|본격 똥개 훈련)\)', '', t)
    out = []
    for p in (x.strip() for x in t.split('·')):
        nums = re.findall(r'\((\d)\)', p)
        base = re.sub(r'\s*(\(\d\)\s*)+$', '', p).strip()
        if len(nums) > 1:
            out += ['%s(%s)' % (base, n) for n in nums]
        elif p:
            out.append(p)
    return out


def enrich(pack, db, map_name=lambda m: m, checked_at=''):
    report = []
    for g in pack['guides']:
        if g.get('type') != 'CHAPTER':
            continue
        for s in g['steps']:
            found = []
            for p in pieces(s['title']):
                q = db.find(p, min_score=95.0)
                if q and q.id not in [f.id for f in found]:
                    found.append(q)
            desc = '\n'.join(l for l in (s.get('description') or '').split('\n') if not l.startswith(MARK))
            lines = []
            for q in found:
                goal = q.goal
                where = (' → %s%s %d,%d' % (goal[0] + ' · ' if goal[0] else '', map_name(goal[1]), goal[2], goal[3])
                         if goal else '')
                lines.append('%s%s — %s%s' % (MARK, q.title, q.summary or '-', where))
            s['description'] = '\n'.join([desc] + lines) if lines else desc
            goal = next((q.goal for q in found if q.goal), None)
            filled = False
            npc = s.get('npc') or ''
            # 공식 위치는 '그 퀘스트 진행 중 가야 할 곳'이라 STEP 의 NPC와 다를 수 있다 → 같은 NPC(또는 NPC 없음)일 때만 좌표로 쓴다
            same = not npc or (goal and goal[0] and (npc == goal[0] or npc.endswith(' ' + goal[0])
                                                     or goal[0].endswith(' ' + npc)))
            if goal and s.get('x') is None and same:
                s['map'], s['x'], s['y'] = goal[1], goal[2], goal[3]      # NPC 칸은 안 채움('거리'·'동쪽' 같은 장소 이름도 있음)
                filled = True
            report.append((g['id'], s['order'], s['title'], [q.title for q in found], goal, filled))
        srcs = [x for x in g.get('sources', []) if x.get('provider') != 'ingame']
        g['sources'] = srcs + [dict(SOURCE, checked_at=checked_at)]
    return report


def main(ro_dir=r'C:\Gravity\Ragnarok'):
    import datetime
    db = QuestInfoDB(ro_dir).load()
    if not db.quests:
        print('공식 퀘스트 파일을 읽지 못했습니다:', db.path)
        return 1
    try:
        from analyzer.gamedata import MapDB
        maps = MapDB(ro_dir)
        name = lambda m: maps.names.get(m) or m   # noqa: E731
    except Exception:
        name = lambda m: m                        # noqa: E731
    with open(PACK, encoding='utf-8') as f:
        pack = json.load(f)
    report = enrich(pack, db, name, datetime.date.today().isoformat())
    with open(PACK, 'w', encoding='utf-8') as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    for gid, order, title, qs, goal, filled in report:
        print('%-28s %2d %-40s %s %s%s' % (gid, order, title[:40], qs, goal, '  (좌표 채움)' if filled else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
