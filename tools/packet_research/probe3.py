"""미해석 패킷 가설 검증 3: 남은 빈도 높은 것들의 모양을 본다(표본 + 칸별 값 종류)."""
import collections
import glob
import io
import os
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
out = io.open(os.path.join(HERE, 'probe3.txt'), 'w', encoding='utf-8')


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


def text(b):
    return b.split(b'\0', 1)[0].decode('cp949', 'replace')


def kind(pr, aid):
    if aid == pr.player_aid:
        return '나'
    return {0: 'PC', 5: '몬스터', 1: 'NPC', 6: 'NPC'}.get((pr.actors.get(aid) or {}).get('obj_type'), '?')


# ---- 메모리얼 던전 0x02cd (71) / 0x02ce (10) / 0x02cb (65)
for name, pr, pk, i, t, b in each(0x02cd, 71):
    say('인던 0x02cd @%ds 이름=%r 나머지=%s' % (t // 1000, text(b[2:63]), struct.unpack_from('<II', b, 63)))
for name, pr, pk, i, t, b in each(0x02cb, 65):
    say('인던 0x02cb @%ds 이름=%r 순번=%d' % (t // 1000, text(b[2:63]), struct.unpack_from('<H', b, 63)[0]))
vals = collections.Counter(struct.unpack_from('<I', b, 2)[0] for name, pr, pk, i, t, b in each(0x02ce, 10))
say('인던 0x02ce [종류.L 값.L] 종류 분포', dict(vals), '예', [struct.unpack_from('<II', b, 2) for name, pr, pk, i, t, b in each(0x02ce, 10)][:4])

# ---- 기간제 0x0298, 회복 0x0a27, 카트 0x0121
say('기간제 0x0298:', [(items.name(struct.unpack_from('<I', b, 2)[0]), struct.unpack_from('<I', b, 6)[0]) for name, pr, pk, i, t, b in each(0x0298, 10)])
rec = collections.Counter(struct.unpack_from('<H', b, 2)[0] for name, pr, pk, i, t, b in each(0x0a27, 8))
say('회복 0x0a27 [종류.W 양.L] 종류 분포(5=HP 7=SP 가설)', dict(rec), '예', [struct.unpack_from('<Hi', b, 2) for name, pr, pk, i, t, b in each(0x0a27, 8)][:6])
cart = [struct.unpack_from('<HHii', b, 2) for name, pr, pk, i, t, b in each(0x0121, 14)]
say('카트 0x0121 [칸수.W 최대칸.W 무게.L 최대무게.L] n=%d 예 %s' % (len(cart), cart[:3] + cart[-2:]))

# ---- 시전: 0x013e (24) ↔ 0x07fb (25, 검증됨) 누가 쓰나
for header, size in ((0x013e, 24), (0x07fb, 25)):
    who = collections.Counter()
    for name, pr, pk, i, t, b in each(header, size):
        src, dst = struct.unpack_from('<II', b, 2)
        who[(kind(pr, src), kind(pr, dst) if dst else '땅')] += 1
    say('시전 0x%04x (시전자 → 대상) 분포: %s' % (header, dict(who.most_common(8))))
rows = [struct.unpack_from('<IIHHHII', b, 2) for name, pr, pk, i, t, b in each(0x013e, 24)]
say('      0x013e 예 [시전자 대상 x y 스킬 속성 시간]:', [(skills.name(r[4]), r[6]) for r in rows[:6]])

# ---- 장판 0x0117 (18) / 0x0120 (6)
g = [struct.unpack_from('<HIHHHI', b, 2) for name, pr, pk, i, t, b in each(0x0117, 18)]
say('장판 0x0117 [스킬.W 시전자.L 레벨.W x.W y.W tick.L] n=%d 스킬: %s' % (len(g), collections.Counter(skills.name(r[0]) for r in g).most_common(6)))

# ---- 아이템 스킬 0x0147 (39)
a = [(struct.unpack_from('<H', b, 2)[0], text(b[14:38])) for name, pr, pk, i, t, b in each(0x0147, 39)]
say('아이템스킬 0x0147 n=%d: %s' % (len(a), collections.Counter((skills.name(x[0]), x[1]) for x in a).most_common(5)))

# ---- 모양만 보는 것들: 표본 3개 + 칸별 값 종류 수
for header in (0x0bc8, 0x0b30, 0x08ae, 0x0c3a, 0x08cd, 0x0b36, 0x0b05, 0x0b5e, 0x0b73, 0x0b33, 0x0b31, 0x01f3, 0x0a3b,
               0x013a, 0x0a15, 0x0a24, 0x0b8d, 0x0a8a, 0x0441, 0x02f7, 0x0985, 0x0b67, 0x01b0, 0x0139):
    rows = [(name, pr, t, b) for name, pr, pk, i, t, b in each(header)]
    if not rows:
        continue
    size = collections.Counter(len(r[3]) for r in rows).most_common(1)[0][0]
    same = [r for r in rows if len(r[3]) == size]
    distinct = [len({r[3][k] for r in same}) for k in range(min(size, 40))]
    first_aid = collections.Counter(kind(r[1], struct.unpack_from('<I', r[3], 2)[0]) for r in same if size >= 6)
    say('0x%04x n=%d 길이 %s 파일 %d개 · 앞 4바이트가 가리키는 것 %s' % (
        header, len(rows), dict(collections.Counter(len(r[3]) for r in rows).most_common(3)), len({r[0] for r in rows}),
        dict(first_aid.most_common(4))))
    say('      칸별 값 종류:', distinct)
    for r in same[:3]:
        say('      %s @%ds %s' % (r[0][:10], r[2] // 1000, r[3][:40].hex()))
out.close()
print(io.open(out.name, encoding='utf-8').read())
