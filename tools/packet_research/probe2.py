"""미해석 패킷 가설 검증 2: 글자(채팅·공지·NPC), 파티, 노점 간판, 퀘스트, 장비, 기타."""
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
from config.settings import Settings                         # noqa: E402
from core.items import ItemDB                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
out = io.open(os.path.join(HERE, 'probe2.txt'), 'w', encoding='utf-8')


def say(*a):
    print(*a, file=out, flush=True)


s = Settings()
items = ItemDB(s['ro_dir'], os.path.join(ROOT, 'data', 'item_db.json'))
items.load()
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


def mask(t, keep=14):
    """다른 사람 이름·대화가 보고서에 그대로 실리지 않게 앞부분만."""
    t = t.replace('\n', ' ')
    return t[:keep] + ('…' if len(t) > keep else '')


def pct(n, d):
    return '%d/%d (%.1f%%)' % (n, d, n * 100 / d) if d else '0/0'


# ---- 글자가 들어 있는 패킷: 가변 길이(머리 2 + 길이 2) 뒤가 cp949 로 읽히는가
for header, label, skip in ((0x008d, '주변 채팅', 8), (0x008e, '내 채팅', 4), (0x009a, '공지', 4), (0x01c3, '색 있는 공지', 16),
                            (0x00b4, 'NPC 대화', 8), (0x00b7, 'NPC 선택지', 8), (0x02c1, 'NPC 색 채팅', 12),
                            (0x0109, '파티 채팅', 8), (0x017f, '길드 채팅', 4), (0x09de, '귓말', 8), (0x0097, '귓말(구)', 28)):
    rows = [(name, text(b[skip:])) for name, pr, pk, i, t, b in each(header)]
    fit = sum(1 for name, pr, pk, i, t, b in each(header) if len(b) >= 4 and struct.unpack_from('<H', b, 2)[0] == len(b))
    korean = sum(1 for _, t in rows if any('가' <= c <= '힣' for c in t))
    say('글자 0x%04x %-10s n=%-4d 길이칸 일치 %s · 한글이 읽히는 것 %d · 예: %s' % (
        header, label, len(rows), pct(fit, len(rows)), korean, [mask(t) for _, t in rows[:3]]))

# ---- 파티: HP(0x080e), 위치(0x0107), 구성원(0x0ae4), 이름 조회 응답(0x0a30)
hp = [(struct.unpack_from('<Iii', b, 2)) for name, pr, pk, i, t, b in each(0x080e, 14)]
say('파티 0x080e [대상.L HP.L 최대HP.L] n=%d · HP ≤ 최대HP 인 비율 %s · 대상 %d명' % (
    len(hp), pct(sum(1 for a, h, m in hp if 0 <= h <= m and m > 0), len(hp)), len({a for a, _, _ in hp})))
pos = [struct.unpack_from('<IHH', b, 2) for name, pr, pk, i, t, b in each(0x0107, 10)]
say('파티 0x0107 [대상.L x.W y.W] n=%d · 좌표가 0~511 인 비율 %s' % (len(pos), pct(sum(1 for a, x, y in pos if x < 512 and y < 512), len(pos))))
names = [(text(b[6:30]), text(b[30:54]), text(b[54:78])) for name, pr, pk, i, t, b in each(0x0a30, 106)]
say('이름 0x0a30 [대상.L 이름.24 파티.24 길드.24 직위.24 칭호.L] n=%d · 이름 있음 %d · 파티 이름 있음 %d · 길드 있음 %d' % (
    len(names), sum(1 for n in names if n[0]), sum(1 for n in names if n[1]), sum(1 for n in names if n[2])))
me_in = collections.Counter()
for name, pr, pk, i, t, b in each(0x0a30, 106):
    if struct.unpack_from('<I', b, 2)[0] == pr.player_aid:
        me_in[name] += 1
say('      그중 내 캐릭터에 대한 응답이 있는 리플레이 %d개' % len(me_in))
mem = [(struct.unpack_from('<I', b, 2)[0], len(b)) for name, pr, pk, i, t, b in each(0x0ae4)]
say('파티 0x0ae4 (89바이트) n=%d · 대상 %d명' % (len(mem), len({a for a, _ in mem})))

# ---- 노점 간판 0x0131 [주인.L 상점명.80], 구매 노점 0x0814
shops = [(struct.unpack_from('<I', b, 2)[0], text(b[6:])) for name, pr, pk, i, t, b in each(0x0131, 86)]
say('노점 0x0131 [주인.L 상점명.80] n=%d · 주인 %d명 · 한글 상점명 %d · 예: %s' % (
    len(shops), len({a for a, _ in shops}), sum(1 for _, t in shops if any('가' <= c <= '힣' for c in t)),
    [mask(t, 10) for _, t in shops[:4]]))
buy = [text(b[6:]) for name, pr, pk, i, t, b in each(0x0814, 86)]
say('구매노점 0x0814 n=%d 예: %s' % (len(buy), [mask(t, 10) for t in buy[:3]]))

# ---- 퀘스트: 0x0afe (6 + 16k?), 0x08fe (4 + 12k?), 0x02b4, 0x0b0c
for header, base, per in ((0x0afe, 6, 16), (0x08fe, 4, 12)):
    rows = [b for name, pr, pk, i, t, b in each(header)]
    fit = sum(1 for b in rows if (len(b) - base) % per == 0)
    say('퀘스트 0x%04x n=%d · (길이-%d) 가 %d 의 배수 %s' % (header, len(rows), base, per, pct(fit, len(rows))))
    shown = 0
    for b in rows:
        if len(b) > base and shown < 4:
            shown += 1
            say('      ', len(b), b[:base].hex(), [struct.unpack_from('<IIIhh' if per == 16 else '<IIhh', b, base + k * per)
                                                 for k in range((len(b) - base) // per)])
rows = [(len(b), struct.unpack_from('<I', b, 2)[0]) for name, pr, pk, i, t, b in each(0x0b0c)]
say('퀘스트 0x0b0c (155바이트) n=%d · 앞 4바이트(퀘스트 번호?) 예: %s' % (len(rows), sorted({q for _, q in rows})[:8]))
rows = [struct.unpack_from('<I', b, 2)[0] for name, pr, pk, i, t, b in each(0x02b4, 6)]
say('퀘스트 0x02b4 [퀘스트.L] n=%d 예: %s' % (len(rows), sorted(set(rows))[:8]))
# 퀘스트 번호가 공식 퀘스트 목록에 있는가
try:
    from navigator import quest_info
    known = set()
    for attr in ('QUESTS', 'load', 'all_quests'):
        pass
except Exception as exc:
    say('   (퀘스트 목록 대조 생략: %s)' % exc)

# ---- 장비 착용/해제 0x0999 (11) / 0x099a (9), 장비 창 0x0a09?
for header, size, label in ((0x0999, 11, '착용'), (0x099a, 9, '해제')):
    rows = [struct.unpack_from('<HI', b, 2) + (b[-1],) for name, pr, pk, i, t, b in each(header, size)]
    say('장비 0x%04x %s [칸.W 부위.L … 결과.B] n=%d · 결과 분포 %s · 부위 값 예 %s' % (
        header, label, len(rows), dict(collections.Counter(r[2] for r in rows)), sorted({r[1] for r in rows})[:8]))

# ---- MVP 0x010c [대상.L] / 0x010a [아이템.W?] / 0x010b [경험치.L]
for header in (0x010c, 0x010a, 0x010b):
    rows = [(name, b.hex()) for name, pr, pk, i, t, b in each(header)]
    say('MVP 0x%04x n=%d %s' % (header, len(rows), rows[:3]))
for name, pr, pk, i, t, b in each(0x010a):
    say('      0x010a 아이템 후보(.L):', items.name(struct.unpack_from('<I', b, 2)[0]), '| (.W):', items.name(struct.unpack_from('<H', b, 2)[0]))

# ---- 시전 취소 0x01b9 [대상.L], 퀘스트 표시 0x0446, 빠른 이동 0x08d2, 스킬 장판 0x0117/0x0120, 창고 0x0b44/0x00f6
mine = sum(1 for name, pr, pk, i, t, b in each(0x01b9, 6) if struct.unpack_from('<I', b, 2)[0] == pr.player_aid)
say('시전 취소 0x01b9 [대상.L] n=%d · 내 것 %d' % (sum(1 for _ in each(0x01b9, 6)), mine))
q = [struct.unpack_from('<IHHHH', b, 2) for name, pr, pk, i, t, b in each(0x0446, 14)]
say('퀘스트 표시 0x0446 [NPC.L x.W y.W 효과.W 색.W] n=%d · 좌표 0~511 %s · 효과 분포 %s · 색 분포 %s' % (
    len(q), pct(sum(1 for r in q if r[1] < 512 and r[2] < 512), len(q)), dict(collections.Counter(r[3] for r in q)),
    dict(collections.Counter(r[4] for r in q))))
fm = [(struct.unpack_from('<IHH', b, 2), pr.player_aid) for name, pr, pk, i, t, b in each(0x08d2, 10)]
say('빠른 이동 0x08d2 [대상.L x.W y.W] n=%d · 내 것 %d · 좌표 0~511 %s' % (
    len(fm), sum(1 for (a, x, y), me in fm if a == me), pct(sum(1 for (a, x, y), me in fm if x < 512 and y < 512), len(fm))))
st = [struct.unpack_from('<HI', b, 2) + (struct.unpack_from('<I', b, 8)[0],) for name, pr, pk, i, t, b in each(0x0b44, 58)]
say('창고 넣기 0x0b44 [칸.W 수량.L 아이템.L …] n=%d · 아이템 번호 DB 일치 %s · 예 %s' % (
    len(st), pct(sum(1 for r in st if items.known(r[2])), len(st)), [(items.name(r[2]), r[1]) for r in st[:4]]))

# ---- 0x0a36 HP 값의 정확한 분포, 0x0141 스탯, 0x00be
vals = collections.Counter(b[6] for name, pr, pk, i, t, b in each(0x0a36, 7))
say('0x0a36 값 분포(정확):', dict(sorted(vals.items())))
st = collections.Counter(struct.unpack_from('<I', b, 2)[0] for name, pr, pk, i, t, b in each(0x0141, 14))
say('0x0141 [스탯번호.L 기본.L 추가.L] 스탯번호 분포:', dict(sorted(st.items())))
sample = [struct.unpack_from('<Iii', b, 2) for name, pr, pk, i, t, b in each(0x0141, 14)][:6]
say('      예:', sample)

# ---- 0x02e1 로 맞은 피해 ↔ HP 감소: 내 HP가 줄어든 양과 비슷한가(대략 검증)
for name, pr, pk in R:
    hits = [(t, struct.unpack_from('<i', b, 22)[0]) for t, h, b in pk if h == 0x02e1 and len(b) == 33
            and struct.unpack_from('<I', b, 6)[0] == pr.player_aid and b[28] in (0, 4, 8, 9, 10)]
    if len(hits) < 100:
        continue
    hp = [(e.timestamp_ms, e.data['value']) for e in pr.events if e.event_type == 'HP_CHANGE' and 'value' in e.data]
    drops = sum(max(0, a[1] - b[1]) for a, b in zip(hp, hp[1:]))
    say('피격 %-12s 0x02e1 로 맞은 %d회 피해 합 %s  ↔  HP 감소량 합 %s (HP 패킷 %d개)' % (
        name, len(hits), format(sum(d for _, d in hits), ','), format(drops, ','), len(hp)))
out.close()
print(io.open(out.name, encoding='utf-8').read())
