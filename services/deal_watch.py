"""관심 매물 조회·중복 억제. 구매나 게임 입력은 수행하지 않는다."""
import json,time
from core.vend_ocr import is_own_listing
from services.market_insights import MarketInsights,WATCH_INTERVAL_SEC,FRESH_SEC
from services.gnjoy_market import compare,RateLimited

MAX_WATCH_ITEMS=20
DEFAULT_DISCOUNT=15

def validate_watchlist(items):
    """장비는 query(공식 전체 이름: 제련·등급·슬롯 = 국민 옵션 또는 사용자가 바꾼 구성)와 cards(같아야 할 카드·인챈트)를 함께 둔다."""
    if len(items)>MAX_WATCH_ITEMS:raise ValueError('관심 아이템은 최대 20개입니다.')
    seen=set();out=[]
    for item in items:
        iid=item.get('item_id');name=str(item.get('name') or '').strip();percent=float(item.get('percent',DEFAULT_DISCOUNT))
        query=str(item.get('query') or '').strip();cards=item.get('cards')
        if type(iid) is not int or iid<=0 or not name or not 0<percent<100:raise ValueError('아이템과 할인 기준(1~99%)을 확인하세요.')
        if cards is not None and not (isinstance(cards,list) and all(isinstance(c,str) for c in cards)):raise ValueError('카드·인챈트 구성을 확인하세요.')
        if (iid,query) in seen:raise ValueError('같은 아이템(같은 구성)을 두 번 등록할 수 없습니다.')
        seen.add((iid,query));row=dict(item_id=iid,name=name,percent=percent)
        if query:row['query']=query
        if query and cards:row['cards']=list(cards)
        out.append(row)
    return out


def _exact(store,client=None):
    """장비 구성별 목록·거래 이력(정확한 전체 이름, 30분 캐시) — 고가 매물 감시와 같은 캐시."""
    from services.resale_watch import ResaleScanner
    return ResaleScanner(store,client)


def _variant_data(scanner,server,item,stop=None,network=True):
    snap=scanner.fetch(server,dict(item_id=item['item_id'],name=item['name'],query=item['query']),stop=stop,network=network)
    return dict(listings=[dict(l,listing_name=l.get('name')) for l in snap['listings']],history=snap['history'],
                fetched_at=snap['fetched_at'])

class DealWatch:
    def __init__(self,store,client=None,clock=time.time):
        self.store=store;self.market=MarketInsights(store,client,clock);self.clock=clock
        self.exact=_exact(store,self.market.client)
        self._last={}
        with store.lock:
            store.conn.execute('CREATE TABLE IF NOT EXISTS deal_alert_seen (server TEXT,item_id INTEGER,shop TEXT,price INTEGER,qty INTEGER,PRIMARY KEY(server,item_id,shop,price,qty))')
            cols={r[1] for r in store.conn.execute('PRAGMA table_info(deal_alert_seen)')}
            for col,kind in (('seen_at','REAL'),('name','TEXT'),('discount','REAL')):   # 최근 알림 기록 표시용
                if col not in cols:store.conn.execute('ALTER TABLE deal_alert_seen ADD COLUMN %s %s'%(col,kind))
            store.conn.commit()

    def seen(self,alert):
        with self.store.lock:
            return self.store.conn.execute('SELECT 1 FROM deal_alert_seen WHERE server=? AND item_id=? AND shop=? AND price=? AND qty=?',
                                           fingerprint(alert)).fetchone() is not None

    def mark_seen(self,alert):
        """실제로 알린 뒤에만 부른다(화면 스레드). 새로 기록했으면 True."""
        with self.store.lock:
            cur=self.store.conn.execute('INSERT OR IGNORE INTO deal_alert_seen(server,item_id,shop,price,qty,seen_at,name,discount) '
                                        'VALUES(?,?,?,?,?,?,?,?)',fingerprint(alert)+(self.clock(),
                                        alert.get('listing_name') or alert.get('name'),alert.get('discount')))
            self.store.conn.commit()
        return bool(cur.rowcount)

    def check(self,server,items,own=(),stop=None,today=None):
        """아직 알리지 않은 후보 매물 목록. 여기서는 '알림 보냄'을 기록하지 않는다 — 전달 전에 알림을 끄거나
        설정을 바꾸거나 앱이 닫히면 그 매물을 영영 못 받게 되므로, 전달한 뒤 mark_seen 으로 기록한다."""
        alerts=[]
        for item in validate_watchlist(items):
            if stop and stop.is_set():break
            key=(server,item['item_id'])+((item['query'],) if item.get('query') else ());now=self.clock()
            meta='watch_checked:'+':'.join(map(str,key))
            saved=float(self.store.meta(meta,'0'))
            if now-max(self._last.get(key,0),saved)<WATCH_INTERVAL_SEC:continue
            self._last[key]=now
            self.store.set_meta(meta,now)
            if item.get('query'):                 # 장비: 정한 구성(제련·등급·슬롯)만 같은 구성 7일 평균과 비교
                data=_variant_data(self.exact,server,item,stop)
            else:
                data=self.market.fetch(server,item['item_id'],item['name'],listing_age=WATCH_INTERVAL_SEC,stop=stop)
            if stop and stop.is_set():break
            mine=[o for o in own if o.get('server',server)==server and
                  (o.get('item_id')==item['item_id'] or o.get('name')==item['name'])]
            # 상점명까지 맞으면 내 노점으로 뺀다. 내 상점명을 못 읽었으면 가격만 같다고 내 것이라 단정하지 않고 표시만 한다.
            listings=[l for l in data['listings'] if not any(o.get('shop') and is_own_listing(l,o.get('price'),o['shop'])
                                                              for o in mine)]
            avg=compare(data['history'],listings,today)['avg7']
            if not avg:continue
            for listing in listings:
                price=listing.get('price')
                if listing.get('kind')!='sale' or price is None or price<0:continue
                discount=(avg-price)/avg*100
                if discount+1e-9<item['percent']:continue
                alert=dict(item,**listing)
                alert.update(name=item['name'],discount=discount,avg7=avg,server=server,
                             maybe_own=any(not o.get('shop') and is_own_listing(listing,o.get('price'),'') for o in mine))
                if not self.seen(alert):
                    det=self._add_detail(server,alert,stop)
                    if item.get('cards'):
                        # 카드·인챈트까지 정했으면 다른 구성은 비교 대상이 아니다. 상세를 못 받았으면 알리되 확인 못 했다고 적는다.
                        if det is not None and list(det.get('cards') or [])!=item['cards']:continue
                        alert['cards_unknown']=det is None
                    alerts.append(alert)
        return alerts

    def _add_detail(self,server,alert,stop=None):
        """알릴 매물의 상세 보기(맵·카드)를 붙인다 — 새 후보일 때만 매물당 한 번 요청하고 저장해 둔다. → 상세 또는 None"""
        if not alert.get('ssi'):return None
        _,det=self.store.detail(server,alert['ssi'])
        if det is None and not (stop and stop.is_set()):
            try:
                det=self.market.client.deal_view(server,alert.get('listing_name') or alert['name'],alert.get('map_code',''),alert['ssi'])
                self.store.save_detail(server,alert['ssi'],det,now=self.clock())
            except RateLimited:raise
            except Exception:det=None                # 상세를 못 받아도 알림은 보낸다(위치만 없음)
        if det:
            if det.get('map'):alert['location']=det['map']
            if det.get('cards'):alert['cards']=det['cards']
            if det.get('options'):alert['options']=det['options']
        return det

def fingerprint(alert):
    return (alert['server'],alert['item_id'],alert.get('shop') or '',alert['price'],alert.get('qty') or 0)

def alert_text(alert):
    # 공식 노점 목록에는 위치 칸이 없다(2026-09-27 실제 응답 확인) — 위치는 목록에 실제로 있을 때만 붙인다.
    place=alert.get('location') or ''
    extra=''.join('\n%s: %s'%(k,', '.join(alert[f])) for k,f in (('카드','cards'),('옵션','options')) if alert.get(f))
    return '%s · %s Z · 7일 평균보다 %.1f%% 저렴\n상점: %s%s%s%s%s'%(alert.get('listing_name') or alert['name'],format(alert['price'],','),alert['discount'],
            alert.get('shop') or '이름 없음',(' · 위치: '+place) if place else '',extra,
            '\n(내 노점과 가격이 같음 — 내 상점명을 못 읽어 내 것인지 확인 못 함)' if alert.get('maybe_own') else '',
            '\n(카드·인챈트가 정한 구성과 같은지 확인 못 함)' if alert.get('cards_unknown') else '')


def recent_alerts(store,server,limit=30):
    """알린 매물 기록(최근 순). 표시만 한다."""
    with store.lock:
        try:
            rows=store.conn.execute('SELECT item_id,name,price,qty,shop,discount,seen_at FROM deal_alert_seen WHERE server=? '
                                    'AND seen_at IS NOT NULL ORDER BY seen_at DESC LIMIT ?',(server,limit)).fetchall()
        except Exception:
            return []
    return [dict(item_id=i,name=n or str(i),price=p,qty=q,shop=s,discount=d,seen_at=t) for i,n,p,q,s,d,t in rows]

def deal_candidates(store,server,items,today=None):
    """관심 아이템마다 저장된 최신 목록에서 가장 싼 판매 매물과 7일 평균 대비 할인율(요청 없음).
    상태: 추천(기준 이상 쌈) / 기준 미달 / 평균 없음 / 목록 없음 / 확인 불가."""
    from services.market_insights import MarketInsights
    market=MarketInsights(store,client=object())             # cached()만 쓴다 — 요청하지 않음
    exact=_exact(store,client=object())
    out=[]
    for item in items:
        if item.get('query'):
            data=_variant_data(exact,server,item,network=False)
            data['comparison']=compare(data['history'],data['listings'],today)
        else:
            data=market.cached(server,item['item_id'],today)
        avg=data['comparison']['avg7'];at=data['fetched_at']
        sales=sorted((l for l in data['listings'] if l.get('kind')=='sale' and l.get('price')),key=lambda l:l['price'])
        row=dict(item_id=item['item_id'],name=item.get('query') or item['name'],percent=item['percent'],avg7=avg,fetched_at=at,
                 price=None,shop='',listing_name='',discount=None,age='')
        if at and time.time()-at>=FRESH_SEC:                      # 오래된 자료는 몇 분·시간 전인지 함께 보인다
            sec=time.time()-at;row['age']='%d분 전'%(sec//60) if sec<3600 else '%d시간 전'%(sec//3600)
        if data.get('listing_unknown'):row['state']='확인 불가'
        elif at is None:row['state']='아직 안 받음'
        elif not sales:row['state']='판매 노점 없음'
        else:
            best=sales[0];row.update(price=best['price'],shop=best.get('shop') or '',listing_name=best.get('listing_name') or '')
            if not avg:row['state']='평균 없음'
            else:
                row['discount']=(avg-best['price'])/avg*100
                row['state']='추천' if row['discount']+1e-9>=item['percent'] else '기준 미달'
        out.append(row)
    order={'추천':0,'기준 미달':1,'평균 없음':2,'판매 노점 없음':3,'확인 불가':4,'아직 안 받음':5}
    return sorted(out,key=lambda r:(order[r['state']],-(r['discount'] or -1e9)))
