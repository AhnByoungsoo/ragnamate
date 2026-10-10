"""리플레이 전체의 패킷 종류를 센다. 결과는 pickle 로도 남겨 다음 조사에서 다시 쓴다."""
import collections, glob, io, os, pickle, struct, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); os.chdir(ROOT)
from rrf import Replay
from analyzer.normalizer import normalize
from analyzer import packets as P
HERE = os.path.dirname(os.path.abspath(__file__))
defs = P.PACKET_SETS[P.DEFAULT_PACKET_VER]
count, files, lens = collections.Counter(), collections.defaultdict(set), collections.defaultdict(collections.Counter)
replays = {}
total = 0
for p in sorted(glob.glob(os.path.join(os.environ.get('RO_REPLAY_FIXTURES', r'C:\Gravity\Ragnarok\Replay'), '*.rrf'))):
    try: pr = normalize(Replay(p), p, experimental=True)
    except Exception as e: print('skip', os.path.basename(p), e); continue
    name = os.path.basename(p)
    replays[name] = dict(job=pr.job_id, character=pr.character, duration=pr.duration_ms, packets=len(pr.packets), player=pr.player_aid)
    for t, b in pr.packets:
        h = struct.unpack_from('<H', b)[0]
        count[h] += 1; files[h].add(name); lens[h][len(b)] += 1; total += 1
pickle.dump(dict(count=count, files={k: sorted(v) for k, v in files.items()}, lens=lens, replays=replays), open(os.path.join(HERE, 'census.pkl'), 'wb'))
out = io.open(os.path.join(HERE, 'census.txt'), 'w', encoding='utf-8')
known = sum(c for h, c in count.items() if h in defs and defs[h].verified)
cand = sum(c for h, c in count.items() if h in defs and not defs[h].verified)
print('리플레이 %d개 · 패킷 %d개 · 종류 %d' % (len(replays), total, len(count)), file=out)
print('검증된 정의 %d종 %.1f%% · 후보 %d종 %.1f%% · 미해석 %d종 %.1f%%' % (
    sum(1 for h in count if h in defs and defs[h].verified), known * 100 / total,
    sum(1 for h in count if h in defs and not defs[h].verified), cand * 100 / total,
    sum(1 for h in count if h not in defs), (total - known - cand) * 100 / total), file=out)
for h, c in count.most_common():
    d = defs.get(h)
    tag = ('●' if d.verified else '△') + d.name if d else ''
    print('0x%04x n=%-7d files=%-2d len=%-34s %s' % (h, c, len(files[h]), str(dict(lens[h].most_common(3)))[:34], tag), file=out)
out.close(); print(io.open(out.name, encoding='utf-8').read())
