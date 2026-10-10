"""Immutable storage candidates and sale-price presentation; never infer item identity from order."""
import sqlite3
from contextlib import closing
import time
from pathlib import Path
from core.storage_grid import inventory, compact_price


def candidates_from_rows(rows,source):
    grouped={}
    for row in inventory(rows,source):
        e=row.entry
        # A mock/demo provider is never a current market price.
        price=row.price if row.price and row.price.provider!='mock' else None
        current=grouped.setdefault(e.item_id,dict(item_id=e.item_id,name=e.name,variants=set(),approximate=False,
                    price=None,updated_at=None,price_source=''))
        current['variants'].add(e.variant_key())
        current['approximate'] |= bool(e.is_equip or e.cards or e.options or e.refine)
        if price and (current['updated_at'] is None or price.updated_at>current['updated_at']):
            current.update(price=price.lowest_price,updated_at=price.updated_at,price_source=price.provider)
    for c in grouped.values():
        c['approximate'] |= len(c.pop('variants'))>1
    return sorted(grouped.values(),key=lambda c:c['item_id'])


def with_official_prices(candidates,path,server):
    """Read cached listings only, matched to the exact server and latest snapshot (including no sales)."""
    servers={'바포메트':'129','이그드라실':'229','다크로드':'529','이프리트':'729'}
    server=servers.get(str(server),str(server))
    if server not in servers.values() or not Path(path).is_file():return candidates
    copies=[dict(c) for c in candidates]
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.15)) as db:
            for c in copies:
                row=db.execute('SELECT MAX(fetched_at) FROM listings WHERE server=? AND item_id=?',(server,c['item_id'])).fetchone()
                at=row[0] if row else None
                if at is None or at<(c['updated_at'] or 0):continue
                price=db.execute("SELECT MIN(price) FROM listings WHERE server=? AND item_id=? AND fetched_at=? AND kind='sale'",(server,c['item_id'],at)).fetchone()[0]
                c.update(price=price,updated_at=at,price_source='공식 판매 노점 · '+server)
    except sqlite3.Error:
        return candidates
    return copies


def price_labels(slots,now=None):
    now=time.time() if now is None else now
    out=[]
    for slot in slots:
        item=slot.get('item')
        if item is None:
            out.append(dict(slot,text='?',price=None,name='구분 안 됨',stale=False,approximate=False));continue
        price=item['price']
        text='-' if price is None else compact_price(price)+('~' if item.get('approximate') else '')
        out.append(dict(slot,text=text,price=price,name=item['name'],approximate=item.get('approximate',False),
                        stale=price is not None and (not item.get('updated_at') or now-item['updated_at']>=86400)))
    return out
