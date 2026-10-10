"""공식 시세 공유 캐시. 네트워크 조회는 UI 밖에서 실행한다."""
import datetime
import threading
import time
from services import gnjoy_market as G

FRESH_SEC = 30 * 60
WATCH_INTERVAL_SEC = 10 * 60
VARIANT_PAGES = 5          # 장비: 가격순이라 높은 제련은 뒤 쪽에 있다 → 5쪽(50줄)까지
MAX_DETAILS = 5            # 장비 한 종류당 상세 보기(카드·옵션) 최대 요청 수 — 같은 제련 중 싼 순
_FETCH_LOCK = threading.Lock()

def recent_history(history, days, today=None):
    today=today or datetime.date.today()
    start=(today-datetime.timedelta(days=days-1)).isoformat()
    return [h for h in history if start<=h['day']<=today.isoformat() and (h.get('avg') or 0)>0]

class MarketInsights:
    def __init__(self,store,client=None,clock=time.time):
        self.store=store;self.client=client or G.shared_client();self.clock=clock

    def _unknown_at(self,server,item_id):
        v=self.store.meta('listing_unknown:%s:%s'%(server,item_id))
        return float(v) if v else None

    def cached(self,server,item_id,today=None):
        at,ls=self.store.latest_listings(server,item_id)
        unknown=self._unknown_at(server,item_id)
        # 마지막 확인에서 비슷한 이름의 다른 아이템에 밀려 이 아이템 줄을 못 봤으면 '노점 없음'이 아니라 '확인 불가'
        listing_unknown=bool(unknown and unknown>(at or 0))
        if listing_unknown:at,ls=unknown,[]
        hist=self.store.history(server,item_id)
        return dict(listings=ls,history=hist,comparison=G.compare(hist,ls,today),fetched_at=at,
                    history_at=self.store.history_fetched_at(server,item_id),listing_unknown=listing_unknown,
                    listing_more=self.store.meta('listing_more:%s:%s'%(server,item_id))=='1')

    def details(self,server,listings):
        """캐시된 상세 보기 {ssi: {cards, options, map, ...}} — 요청하지 않음."""
        out={}
        for l in listings:
            if l.get('ssi'):
                _,d=self.store.detail(server,l['ssi'])
                if d is not None:out[l['ssi']]=d
        return out

    def fetch_variant(self,server,item,stop=None):
        """장비: 목록을 5쪽까지 받고, 같은 제련 판매 매물 중 싼 순 최대 5건의 상세(카드·랜덤옵션)를 받는다.
        슬롯도 옵션도 없는 장비는 상세가 필요 없다. 상세는 매물마다 한 번만 받는다(같은 매물 번호면 재사용)."""
        data=self.fetch(server,item['item_id'],item['name'],max_pages=VARIANT_PAGES,stop=stop)
        need_detail=bool(item.get('card_names') or item.get('options'))
        plain=lambda l:G.split_name(l['listing_name'])[2].replace(' ','')==item['name'].replace(' ','')   # noqa: E731
        same=[l for l in data['listings'] if l.get('kind')=='sale' and l.get('ssi') and l.get('listing_name')
              and G.split_name(l['listing_name'])[0]==item.get('refine',0)
              and (item.get('card_names') or plain(l))]          # 카드 없는 장비면 접두사 붙은 매물은 상세를 볼 필요 없음
        for l in sorted(same,key=lambda l:l['price'])[:MAX_DETAILS]:
            if stop and stop.is_set():break
            slots=G.split_name(l['listing_name'])[3]
            if not (need_detail or slots) or self.store.detail(server,l['ssi'])[1] is not None:continue
            try:det=self.client.deal_view(server,l['listing_name'],l.get('map_code',''),l['ssi'])
            except G.RateLimited:raise
            except Exception:continue            # 매물이 그새 사라지는 등: 이 한 건만 건너뜀
            self.store.save_detail(server,l['ssi'],det,now=self.clock())
        return self.cached(server,item['item_id'])

    def fetch(self,server,item_id,name,listing_age=FRESH_SEC,stop=None,max_pages=2):
        # Also prevents two workers checking the same absent cache concurrently.
        with _FETCH_LOCK:
            if stop and stop.is_set():return self.cached(server,item_id)
            now=self.clock();at,_=self.store.latest_listings(server,item_id)
            at=max(at or 0,self._unknown_at(server,item_id) or 0) or None
            pages=int(self.store.meta('listing_pages:%s:%s'%(server,item_id),'2') or 2)
            if at is None or now-at>=listing_age or pages<max_pages:        # 장비는 더 많은 쪽이 필요할 수 있음
                raw,more=self.client.listings(server,name,max_pages=max_pages,with_more=True,stop=stop)
                if stop and stop.is_set():return self.cached(server,item_id)    # 중간에 멈춘 목록은 저장하지 않음
                rows=[r for r in raw if r['item_id']==item_id]
                if not rows and more:
                    # 검색은 '포함' 일치·가격순이라 더 싼 다른 아이템이 2쪽을 채우면 이 아이템은 안 보인다 → 없음으로 저장하지 않음
                    self.store.set_meta('listing_unknown:%s:%s'%(server,item_id),self.clock())
                else:
                    self.store.save_listings(server,item_id,rows,now=self.clock())
                self.store.set_meta('listing_pages:%s:%s'%(server,item_id),max_pages)
                self.store.set_meta('listing_more:%s:%s'%(server,item_id),'1' if more else '0')
            if stop and stop.is_set():return self.cached(server,item_id)
            at=self.store.history_fetched_at(server,item_id)
            if at is None or self.clock()-at>=FRESH_SEC:
                hist,_=self.client.history(server,name,pages=3,stop=stop)
                if stop and stop.is_set():return self.cached(server,item_id)
                self.store.save_history(server,item_id,hist,now=self.clock())
            return self.cached(server,item_id)
