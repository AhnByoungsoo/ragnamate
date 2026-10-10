"""미해석 패킷 가설 검증 1: 전투·자원·아이템 쪽. 각 가설을 '구조가 맞는 비율'로 확인한다."""
import collections
import glob
import io
import os
import statistics
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from rrf import Replay                                      # noqa: E402
from analyzer.normalizer import normalize                    # noqa: E402
from analyzer.gamedata import _NameFile                      # noqa: E402
from config.settings import Settings                         # noqa: E402
from core.items import ItemDB                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
out = io.open(os.path.join(HERE, 'probe1.txt'), 'w', encoding='utf-8')


def say(*a):
    print(*a, file=out, flush=True)


s = Settings()
items = ItemDB(s['ro_dir'], os.path.join(ROOT, 'data', 'item_db.json'))
items.load()
skills = _NameFile(os.path.join(ROOT, 'data', 'skill_names.json'), '스킬',
                   [os.path.join(ROOT, 'data', 'skill_catalog.json'), os.path.join(ROOT, 'data', 'skill_names_official.json')])
R = []
for p in sorted(glob.glob(os.path.join(os.environ.get('RO_REPLAY_FIXTURES', r'C:\Gravity\Ragnarok\Replay'), '*.rrf'))):
    try:
        pr = normalize(Replay(p), p, experimental=True)
    except Exception:
        continue
    t0 = pr.packets[0][0] if pr.packets else 0
    R.append((os.path.basename(p), pr, [(t - t0, struct.unpack_from('<H', b)[0], b) for t, b in pr.packets]))


def each(header, size=None):
    for name, pr, pk in R:
        for i, (t, h, b) in enumerate(pk):
            if h == header and (size is None or len(b) == size):
                yield name, pr, pk, i, t, b


def pct(n, d):
    return '%d/%d (%.1f%%)' % (n, d, n * 100 / d) if d else '0/0'


# ---------------------------------------------------------------- 1. 0x043d 스킬 후딜레이
ok = tot = 0
delays = collections.defaultdict(list)
for name, pr, pk, i, t, b in each(0x043d, 8):
    skill, delay = struct.unpack_from('<HI', b, 2)
    used = {e.data.get('skill') for e in pr.events if e.event_type in ('SKILL_USE', 'SKILL_CAST_START') and e.actor_id == pr.player_aid}
    tot += 1
    ok += skill in used
    delays[skill].append(delay)
say('1) 0x043d [스킬 번호.W 지연ms.L] — 스킬 번호가 내가 쓴 스킬인 비율', pct(ok, tot))
for skill, v in sorted(delays.items(), key=lambda kv: -len(kv[1]))[:8]:
    say('      %-26s n=%-4d 지연 중앙값 %dms (최소 %d 최대 %d) 값 종류 %d' % (skills.name(skill), len(v), statistics.median(v), min(v), max(v), len(set(v))))

# ---------------------------------------------------------------- 2. 0x0a36 몬스터 HP %
ok = tot = 0
vals = collections.Counter()
kinds = collections.Counter()
for name, pr, pk, i, t, b in each(0x0a36, 7):
    aid, hp = struct.unpack_from('<IB', b, 2)
    a = pr.actors.get(aid) or {}
    tot += 1
    kinds[a.get('obj_type')] += 1
    ok += 0 <= hp <= 100
    vals[hp // 10 * 10] += 1
say('2) 0x0a36 [대상.L HP%.B] — 값이 0~100인 비율', pct(ok, tot), '| 대상 종류(0=PC 5=몬스터)', dict(kinds), '| 분포', dict(sorted(vals.items())))

# ---------------------------------------------------------------- 3. 0x01d0 기구 수
tot = mine = 0
nums = collections.Counter()
for name, pr, pk, i, t, b in each(0x01d0, 8):
    aid, n = struct.unpack_from('<IH', b, 2)
    tot += 1
    if aid == pr.player_aid:
        mine += 1
        nums[n] += 1
say('3) 0x01d0 [대상.L 개수.W] — 내 것', pct(mine, tot), '| 내 개수 분포', dict(sorted(nums.items())))

# ---------------------------------------------------------------- 4. 0x0add 바닥 드랍 + 0x009d + 0x00a1
tot = known = 0
picked = lost = matched_pick = 0
drop_items = collections.Counter()
attr = collections.Counter()
for name, pr, pk in R:
    floor = {}                                    # 바닥 물건 번호 → (아이템, 수량, 시각)
    deaths = [(e.timestamp_ms, e.actor_id, e.data.get('class_id')) for e in pr.events if e.event_type == 'MONSTER_DEATH']
    last_gain = None
    for i, (t, h, b) in enumerate(pk):
        if h == 0x0add and len(b) == 24:
            gid, nameid, typ, ident, x, y, sx, sy, cnt = struct.unpack_from('<IIHBHHBBH', b, 2)
            tot += 1
            known += items.known(nameid)
            floor[gid] = (nameid, cnt, t)
            drop_items[nameid] += cnt
            near = [d for d in deaths if 0 <= t - d[0] <= 600]
            attr['사망 0.6초 안' if near else '사망 없음'] += 1
        elif h == 0x009d and len(b) == 19:
            gid, nameid = struct.unpack_from('<II', b, 2)
            floor.setdefault(gid, (nameid, None, t))
        elif h == 0x00a1 and len(b) == 6:
            gid = struct.unpack_from('<I', b, 2)[0]
            if gid in floor:
                nameid, cnt, t_drop = floor.pop(gid)
                # 내가 주웠나: ±300ms 안에 같은 아이템의 0b41
                mine = any(pk[j][1] == 0x0b41 and len(pk[j][2]) == 70 and struct.unpack_from('<I', pk[j][2], 6)[0] == nameid
                           and abs(pk[j][0] - t) <= 300 for j in range(max(0, i - 8), min(len(pk), i + 8)))
                picked += mine
                lost += not mine
say('4) 0x0add [바닥번호.L 아이템.L 종류.W 감정.B x.W y.W subx.B suby.B 수량.W …] — 아이템 번호가 DB에 있는 비율', pct(known, tot))
say('      드랍 직전 0.6초 안에 몬스터 사망이 있었나:', dict(attr))
say('      바닥에서 사라진 %d건 중 내가 주운 것(같은 아이템 획득 패킷 ±0.3초) %d · 그 외(남이 줍거나 사라짐) %d' % (picked + lost, picked, lost))
say('      많이 떨어진 것:', [(items.name(i), n) for i, n in drop_items.most_common(6)])

# ---------------------------------------------------------------- 5. 0x01c8 아이템 사용
tot = known = mine = fail = 0
used = collections.Counter()
remain_ok = 0
for name, pr, pk, i, t, b in each(0x01c8, 15):
    index, itemid, aid, remaining, result = struct.unpack_from('<HIIHB', b, 2)
    tot += 1
    known += items.known(itemid)
    if aid == pr.player_aid:
        mine += 1
        fail += result == 0
        used[itemid] += 1
say('5) 0x01c8 [칸.W 아이템.L 사용자.L 남은수.W 결과.B] — 아이템 번호가 DB에 있는 비율', pct(known, tot), '| 내가 쓴 것', pct(mine, tot), '| 실패(결과 0)', fail)
say('      내가 쓴 것:', [(items.name(i), n) for i, n in used.most_common(8)])

# ---------------------------------------------------------------- 6. 0x02e1 평타 피해(구형) — 지금 피해 집계에 빠져 있나
tot = mine = at_me = 0
types = collections.Counter()
dmg_mine = 0
for name, pr, pk, i, t, b in each(0x02e1, 33):
    src, dst, tick, sspeed, dspeed, dmg, div, typ, dmg2 = struct.unpack_from('<IIIiiiHBi', b, 2)
    tot += 1
    types[typ] += 1
    if src == pr.player_aid:
        mine += 1
        dmg_mine += max(0, dmg)
    at_me += dst == pr.player_aid
decoded_mine = sum(1 for name, pr, pk in R for e in pr.events if e.event_type == 'DAMAGE' and e.actor_id == pr.player_aid and e.data.get('skill') is None)
say('6) 0x02e1 [공격자.L 대상.L tick.L 공속.L 피격모션.L 피해.L 타수.W 종류.B 왼손피해.L] — 내가 때린 것', pct(mine, tot), '· 내가 맞은 것', at_me,
    '| 종류 분포', dict(types.most_common(8)))
say('      이 패킷의 내 피해 합 %s  ↔  지금 집계되는 내 평타 이벤트 %d건' % (format(dmg_mine, ','), decoded_mine))

# ---------------------------------------------------------------- 7. 0x008a 행동(앉기/줍기 등)
types = collections.Counter()
for name, pr, pk, i, t, b in each(0x008a, 29):
    src = struct.unpack_from('<I', b, 2)[0]
    typ = b[26]
    types[(src == pr.player_aid, typ)] += 1
say('7) 0x008a (29바이트) 종류 자리(byte 26) 분포 (내 것 여부, 종류):', dict(types.most_common(10)))

# ---------------------------------------------------------------- 8. 0x00bd 스탯 창
rows = []
for name, pr, pk, i, t, b in each(0x00bd, 44):
    point = struct.unpack_from('<H', b, 2)[0]
    stats = [b[4 + k * 2] for k in range(6)]
    tail = struct.unpack_from('<12H', b, 16)
    rows.append((name, pr.job_id, point, stats, tail))
say('8) 0x00bd (44바이트) 스탯 포인트.W + [STR 필요포인트]×6 + … 표본:')
for r in rows[:4] + rows[-2:]:
    say('      %-12s job %-5s 남은 포인트 %-4d STR~LUK %s  뒤 12개.W %s' % (r[0], r[1], r[2], r[3], list(r[4])))
say('      STR~LUK 값이 1~130 범위인 비율', pct(sum(all(1 <= v <= 130 for v in r[3]) for r in rows), len(rows)))

# ---------------------------------------------------------------- 9. 스킬 목록 0x010f / 0x0b32
for header, per in ((0x010f, 37), (0x0b32, 15)):
    tot = fit = 0
    sample = None
    for name, pr, pk, i, t, b in each(header):
        tot += 1
        if (len(b) - 4) % per == 0:
            fit += 1
            sample = sample or (name, pr, b)
    say('9) 0x%04x 길이-4 가 %d 의 배수인 비율' % (header, per), pct(fit, tot))
    if sample:
        name, pr, b = sample
        got = []
        for k in range((len(b) - 4) // per):
            o = 4 + k * per
            sid, inf, lv, sp, rng = struct.unpack_from('<HIHHH', b, o)
            got.append((sid, lv, sp, rng))
        named = sum(1 for g in got if not skills.name(g[0]).startswith('스킬'))
        say('      %s job %s: 스킬 %d개, 이름 확인 %d개, 예: %s' % (name, pr.job_id, len(got), named,
            [(skills.name(g[0]), 'Lv%d' % g[1], 'SP%d' % g[2], '사거리%d' % g[3]) for g in got[:5]]))

# ---------------------------------------------------------------- 10. 0x019b 효과(레벨업 등)
types = collections.Counter()
for name, pr, pk, i, t, b in each(0x019b, 10):
    aid, typ = struct.unpack_from('<II', b, 2)
    types[(aid == pr.player_aid, typ)] += 1
say('10) 0x019b [대상.L 종류.L] (내 것 여부, 종류) 분포:', dict(types.most_common(10)))

# ---------------------------------------------------------------- 11. 0x0110 스킬 실패
causes = collections.Counter()
for name, pr, pk, i, t, b in each(0x0110, 14):
    skill, btype, itemid, flag, cause = struct.unpack_from('<HIIBB', b, 2)
    causes[(flag, cause)] += 1
say('11) 0x0110 [스킬.W 구분.L 아이템.L 성공.B 사유.B] (성공, 사유) 분포:', dict(causes.most_common(10)))

# ---------------------------------------------------------------- 12. 0x07fa 길이·사유·칸
lens = collections.Counter(len(b) for name, pr, pk, i, t, b in each(0x07fa))
reasons = collections.Counter()
pair = match = 0
for name, pr, pk, i, t, b in each(0x07fa, 8):
    reason, index, count = struct.unpack_from('<HHH', b, 2)
    reasons[reason] += 1
    if reason == 4:
        nxt = next((pk[j][2] for j in range(i + 1, min(len(pk), i + 4)) if pk[j][1] == 0x0b44), None)
        if nxt is not None:
            pair += 1
            match += struct.unpack_from('<I', nxt, 4)[0] == count          # 0b44: 칸.W 수량.L …
say('12) 0x07fa 길이 분포', dict(lens), '| 사유 분포', dict(reasons), '| 사유 4 다음의 창고 추가(0b44) 수량 일치', pct(match, pair))
out.close()
print(io.open(out.name, encoding='utf-8').read())
