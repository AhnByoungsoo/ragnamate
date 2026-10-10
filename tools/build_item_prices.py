"""상점(NPC) 판매 기준가 색인(data/rathena/item_prices.json)을 만든다.

rAthena db/re 의 item_db_etc / item_db_usable / item_db_equip 에서 아이템 번호와 Buy·Sell 만 뽑는다.
rAthena 규칙: Sell 이 없으면 Buy 의 절반(내림). Buy·Sell 이 둘 다 없는 아이템은 넣지 않는다(기준가 모름).

    python tools/build_item_prices.py            # GitHub 에서 받아 만든다
    python tools/build_item_prices.py 폴더        # 이미 받아 둔 yml 폴더로 만든다

출처: https://github.com/rathena/rathena · © rAthena Development Team · GPL-3.0-or-later.
외부 서버 구현 자료라 국내 서버 실제 값과 다를 수 있다 — 화면에는 △(미검증)으로 표시한다.
"""
import datetime
import io
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'rathena', 'item_prices.json')
BASE = 'https://raw.githubusercontent.com/rathena/rathena/master/db/re/'
FILES = ('item_db_etc.yml', 'item_db_usable.yml', 'item_db_equip.yml')
_ID, _FIELD = re.compile(r'^  - Id: (\d+)'), re.compile(r'^    (Buy|Sell): (\d+)')


def parse(lines):
    """yml 줄 → {아이템 번호: 상점에 팔 때 기준가}."""
    raw, cur = {}, None
    for line in lines:
        m = _ID.match(line)
        if m:
            cur = raw.setdefault(int(m.group(1)), {})
            continue
        m = _FIELD.match(line)
        if m and cur is not None:
            cur[m.group(1)] = int(m.group(2))
    return {i: v['Sell'] if 'Sell' in v else v['Buy'] // 2 for i, v in raw.items() if v}


def main(folder=None):
    prices = {}
    for name in FILES:
        if folder:
            with io.open(os.path.join(folder, name), encoding='utf-8') as f:
                prices.update(parse(f))
        else:
            req = urllib.request.Request(BASE + name, headers={'User-Agent': 'RagnaMate personal tool'})
            with urllib.request.urlopen(req, timeout=60) as r:
                prices.update(parse(r.read().decode('utf-8').splitlines()))
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(dict(source=BASE, files=list(FILES), built_at=datetime.date.today().isoformat(),
                       note='상점에 팔 때 기준가(Sell, 없으면 Buy//2). rAthena 자료 — 국내 서버와 다를 수 있음',
                       sell={str(i): p for i, p in sorted(prices.items())}), f, ensure_ascii=False, separators=(',', ':'))
    print('%s  아이템 %d종  %d바이트' % (OUT, len(prices), os.path.getsize(OUT)))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
