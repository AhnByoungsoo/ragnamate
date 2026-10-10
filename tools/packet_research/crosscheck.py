"""해외 공개 자료의 패킷 정의 ↔ 내 리플레이의 실제 길이 대조.

- OpenKore src/Network/Receive/ServerType0.pm : 패킷 번호 → (이름, 풀기 틀, 칸 이름)
- OpenKore tables/kRO/recvpackets.txt        : kRO 패킷 번호 → 길이
- rAthena packets_struct.hpp / packets.hpp   : 패킷 이름
"""
import io
import os
import pickle
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'sources')
URLS = {
    'openkore_ServerType0.pm': 'https://raw.githubusercontent.com/OpenKore/openkore/master/src/Network/Receive/ServerType0.pm',
    'openkore_kRO_recvpackets.txt': 'https://raw.githubusercontent.com/OpenKore/openkore/master/tables/kRO/recvpackets.txt',
    'rathena_packets_struct.hpp': 'https://raw.githubusercontent.com/rathena/rathena/master/src/map/packets_struct.hpp',
    'rathena_packets.hpp': 'https://raw.githubusercontent.com/rathena/rathena/master/src/map/packets.hpp',
    'hercules_packets_struct.h': 'https://raw.githubusercontent.com/HerculesWS/Hercules/stable/src/map/packets_struct.h',
}
os.makedirs(SRC, exist_ok=True)
for _name, _url in URLS.items():                 # 공개 자료는 저장소에 넣지 않고 필요할 때 받는다
    if not os.path.exists(os.path.join(SRC, _name)):
        import urllib.request
        _req = urllib.request.Request(_url, headers={'User-Agent': 'RagnaMate personal research'})
        with open(os.path.join(SRC, _name), 'wb') as _f:
            _f.write(urllib.request.urlopen(_req, timeout=60).read())
census = pickle.load(open(os.path.join(HERE, 'census.pkl'), 'rb'))
SIZES = dict(a=1, A=1, Z=1, C=1, c=1, v=2, s=2, n=2, V=4, l=4, L=4, N=4, x=1, q=8, Q=8, f=4)


def template_size(tpl):
    """perl unpack 틀의 고정 길이. 가변(a*, 그룹 등)이면 None."""
    total = 0
    for code, count in re.findall(r'([a-zA-Z])\s*(\*|\d*)', tpl):
        if count == '*' or code not in SIZES:
            return None
        total += SIZES[code] * (int(count) if count else 1)
    return total


text = io.open(os.path.join(SRC, 'openkore_ServerType0.pm'), encoding='utf-8', errors='replace').read()
kore = {}
for m in re.finditer(r"^\s*'([0-9A-Fa-f]{4})'\s*=>\s*\['(\w+)'(?:,\s*'([^']*)'(?:,\s*\[qw\(([^)]*)\)\])?)?", text, re.M):
    kore[int(m.group(1), 16)] = (m.group(2), m.group(3) or '', (m.group(4) or '').split())
lens = {}
for line in io.open(os.path.join(SRC, 'openkore_kRO_recvpackets.txt'), encoding='utf-8', errors='replace'):
    parts = line.split()
    if len(parts) >= 2 and re.fullmatch(r'[0-9A-Fa-f]{4}', parts[0]):
        lens[int(parts[0], 16)] = int(parts[1])
names = {}
for fname in ('rathena_packets_struct.hpp', 'rathena_packets.hpp', 'hercules_packets_struct.h'):
    for m in re.finditer(r'DEFINE_PACKET_HEADER\((\w+),\s*0x([0-9a-fA-F]+)\)', io.open(os.path.join(SRC, fname), encoding='utf-8', errors='replace').read()):
        names.setdefault(int(m.group(2), 16), m.group(1))
for m in re.finditer(r'^\s*(\w+)\s*=\s*0x([0-9a-fA-F]+),', io.open(os.path.join(SRC, 'hercules_packets_struct.h'), encoding='utf-8', errors='replace').read(), re.M):
    names.setdefault(int(m.group(2), 16), m.group(1))

out = io.open(os.path.join(HERE, 'crosscheck.txt'), 'w', encoding='utf-8')
rows = []
stat = dict(kore=0, fixed_ok=0, fixed_bad=0, var=0, none=0, len_ok=0, len_bad=0)
for pid, n in census['count'].most_common():
    seen = census['lens'][pid]
    k = kore.get(pid)
    size = template_size(k[1]) + 2 if k and k[1] and template_size(k[1]) is not None else None
    verdict = ''
    if k is None:
        stat['none'] += 1
        verdict = '정의 없음'
    else:
        stat['kore'] += 1
        if size is None:
            stat['var'] += 1
            verdict = '가변'
        elif set(seen) == {size}:
            stat['fixed_ok'] += 1
            verdict = '길이 일치'
        else:
            stat['fixed_bad'] += 1
            verdict = '길이 다름(틀 %d)' % size
    tbl = lens.get(pid)
    tbl_v = ''
    if tbl is not None:
        ok = (tbl in (-1, 0) and len(seen) >= 1) or set(seen) == {tbl}
        stat['len_ok' if ok else 'len_bad'] += 1
        tbl_v = '표 %s%s' % (tbl, '' if ok else ' ≠')
    rows.append((pid, n, dict(seen.most_common(2)), k[0] if k else '', names.get(pid, ''), verdict, tbl_v, k[1] if k else '',
                 ' '.join(k[2]) if k else ''))
print('내 리플레이 패킷 %d종 · OpenKore 정의 있음 %d (고정 길이 일치 %d · 길이 다름 %d · 가변 %d) · 정의 없음 %d' % (
    len(rows), stat['kore'], stat['fixed_ok'], stat['fixed_bad'], stat['var'], stat['none']), file=out)
print('kRO 길이표와 일치 %d · 불일치 %d · 표에 없음 %d' % (stat['len_ok'], stat['len_bad'], len(rows) - stat['len_ok'] - stat['len_bad']), file=out)
for r in rows:
    print('0x%04x n=%-6d %-22s %-28s %-34s %-16s %-8s | %s | %s' % (r[0], r[1], str(r[2])[:22], r[3][:28], r[4][:34], r[5], r[6], r[7], r[8]), file=out)
out.close()
pickle.dump(dict(kore=kore, lens=lens, names=names), open(os.path.join(HERE, 'defs.pkl'), 'wb'))
print(io.open(out.name, encoding='utf-8').read())
