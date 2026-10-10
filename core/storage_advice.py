"""창고 정리 참고용 추천. 거래량은 공식 날짜별 기록 수로만 판단한다."""
import re,time
from services.gnjoy_market import compare,recommend_price,split_name
from services.market_insights import recent_history,FRESH_SEC
from core.vend_ocr import real_amount,is_own_listing

SELL_RISE_PCT=10
NO_SELL_FALL_PCT=15
MIN_HISTORY_RECORDS=3

def untradeable(item_db,item_id):
    item=item_db.get(item_id) or {}
    text=re.sub(r'\^[0-9a-fA-F]{6}|\s+','',item.get('description') or '')
    return any(x in text for x in ('거래불가','거래불가능','귀속아이템','계정귀속','캐릭터귀속'))

def _is_card(item_db,iid):
    from core.item_category import category
    return category(item_db.get(iid) or {},(getattr(item_db,'categories',{}) or {}).get(iid))=='카드'

def storage_items(entries,item_db):
    """창고 아이템 줄 + 장비에 꽂힌 카드 줄(socketed=True, 참고용). 카드·인챈트 이름은 같은 구성 비교에 쓴다."""
    grouped={};excluded=0;cards={}
    name=getattr(item_db,'name',None) or (lambda i:str(i))
    for e in entries:
        if e.location_group!='창고':continue
        if untradeable(item_db,e.item_id):excluded+=1;continue
        key=e.variant_key();ids=[c for c in (e.cards or []) if c]
        r=grouped.setdefault(key,dict(item_id=e.item_id,name=e.name,quantity=0,
            variant=bool(e.is_equip or e.refine or e.cards or e.options),refine=e.refine or 0,
            card_names=[name(c) for c in ids],options=list(e.options or [])))
        r['quantity']+=e.quantity
        host=('+%d '%e.refine if e.refine else '')+e.name
        for c in ids:
            if not _is_card(item_db,c):continue            # 인챈트 등은 따로 팔 수 없어 줄을 만들지 않음
            row=cards.setdefault((c,host),dict(item_id=c,name=name(c),quantity=0,variant=False,refine=0,
                                               socketed=True,host=host,card_names=[],options=[]))
            row['quantity']+=e.quantity
    return list(grouped.values())+list(cards.values()),excluded

def _age(sec):
    return '%d분 전'%(sec//60) if sec<3600 else ('%d시간 전'%(sec//3600) if sec<86400 else '%d일 전'%(sec//86400))

def split_own(listings,own):
    """(내 노점을 뺀 목록, 가격만 같고 내 것인지 모르는 매물이 있는지). 상점명까지 맞아야 내 노점으로 뺀다."""
    kept=[l for l in listings if not any(o.get('shop') and is_own_listing(l,o.get('price'),o['shop']) for o in own)]
    maybe=any(not o.get('shop') and is_own_listing(l,o.get('price'),'') for l in kept for o in own)
    return kept,maybe

def advise(item,history,listings,today=None,fetched_at=None,now=None,history_at=None,listing_unknown=False,own=()):
    """자료 상태를 먼저 가른다: 아직 안 받음 / 현재 매물 확인 불가 / 거래 기록 없음·드묾 / 오래된 자료."""
    listings,maybe_own=split_own(listings,own)
    cmp=compare(history,listings,today);rec=recommend_price(cmp)
    recent=recent_history(history,30,today)
    low,avg=cmp['now_min'],cmp['avg7']
    now=time.time() if now is None else now
    diff=None if not low or not avg else (low-avg)/avg*100
    if fetched_at is None and history_at is None:
        verdict,reason='보류','시세 없음: 아직 받지 않음 — 표에서 선택 후 [선택한 아이템 시세 받기]'
    else:
        verdict,reason='보류','최근 7일 평균 또는 현재 판매 시세가 없습니다'
        if not recent:
            reason='거래 기록 없음: 최근 30일 날짜별 거래 기록 0건'
        elif len(recent)<MIN_HISTORY_RECORDS:
            reason='거래 드묾: 최근 30일 날짜별 거래 기록 %d건'%len(recent)
        elif listing_unknown:
            reason='현재 매물 확인 불가: 공식 검색이 이름이 비슷한 더 싼 아이템으로 가득 차 이 아이템 줄이 보이지 않음'
        elif diff is not None:
            if diff>=SELL_RISE_PCT-1e-9:verdict,reason='지금 팔기','7일 평균보다 %.1f%% 높은 최저가'%diff
            elif diff<=-NO_SELL_FALL_PCT+1e-9:verdict,reason='팔지 말기','하락 중: 7일 평균보다 %.1f%% 낮은 최저가'%abs(diff)
            else:reason='7일 평균 대비 %+.1f%%: 판단 기준 범위 안'%diff
        elif not low:
            reason='지금 판매 노점 없음(7일 평균만 있음)'
        if item.get('variant'):
            verdict='보류';reason='제련·카드·옵션별 시세 없음: 일반 아이템 기준가만 표시'
        if fetched_at is None or now-fetched_at>=FRESH_SEC:
            verdict='보류'
            reason=('오래된 자료(현재 매물 %s 확인)'%_age(now-fetched_at) if fetched_at else '현재 매물 미확인')+' · '+reason
        if maybe_own:
            reason+=' · 내 노점과 가격이 같은 매물 포함(상점명 미확인)'
    qty=item['quantity'];gross=None if rec is None else rec*qty;net=None if rec is None else real_amount(rec)*qty
    if item.get('socketed'):
        # 장비에 꽂힌 카드: 시세는 보여 주되 분리해야 팔 수 있으므로 판단·합계에서 뺀다(장비 값과 이중 계산 방지)
        verdict='참고';reason='장착 카드(%s) — 분리해야 따로 판매 가능 · %s'%(item.get('host',''),reason);gross=net=None
    return dict(item,now_min=low,avg7=avg,recommended=rec,recommendation=verdict,reason=reason,
                expected=gross,net=net,difference=diff,fetched_at=fetched_at)

def _norm(s):
    return re.sub(r'\s+','',s or '')

def advise_variant(item,listings,details,fetched_at=None,now=None,listing_unknown=False,listing_more=False,own=()):
    """장비(제련·카드·옵션): 공식 목록 이름의 제련이 같은 판매 매물만 보고, 슬롯이 있으면 상세 보기의 카드 구성이
    같은 매물을 '같은 구성'으로 본다. 랜덤옵션 번호는 공식 글과 맞출 수 없어 옵션 있는 장비는 같은 구성이라 단정하지 않는다.
    장비별 과거 시세(7일 평균)는 공식 자료가 제련을 가리지 않으므로 쓰지 않는다."""
    listings,maybe_own=split_own(listings,own)
    now=time.time() if now is None else now
    refine=item.get('refine',0);label='+%d'%refine if refine else '제련 0'
    want=sorted(_norm(c) for c in item.get('card_names') or [])
    same=[l for l in listings if l.get('kind')=='sale' and l.get('price') and l.get('listing_name')
          and split_name(l['listing_name'])[0]==refine]
    same.sort(key=lambda l:l['price'])
    identical=[];checked=differ=0
    for l in same:
        slots=split_name(l['listing_name'])[3]
        prefixed=_norm(split_name(l['listing_name'])[2])!=_norm(item['name'])   # '+9마술의일루시온…' = 카드 접두사
        if prefixed and not want:
            differ+=1;continue                              # 카드 없는 장비와는 다른 구성(상세 없이도 확실)
        if not want and not item.get('options') and not slots:
            identical.append(l);continue                    # 슬롯 없는 장비: 제련이 같으면 같은 구성
        det=details.get(l.get('ssi'))
        if det is None:continue
        checked+=1
        if not item.get('options') and not det.get('options') and sorted(_norm(c) for c in det.get('cards') or [])==want:
            identical.append(l)
        else:differ+=1
    low=identical[0]['price'] if identical else (same[0]['price'] if same else None)
    rec=recommend_price(dict(now_min=identical[0]['price'],avg7=None)) if identical else None
    verdict='보류'
    if fetched_at is None:
        reason='시세 없음: 아직 받지 않음 — 표에서 선택 후 [선택한 아이템 시세 받기]'
    elif identical:
        verdict='참고가'
        reason='같은 구성(%s%s) 판매 %d건 · 최저 %s'%(label,' · 카드 같음' if want else '',len(identical),format(low,','))
    elif same:
        if item.get('options'):
            reason='랜덤옵션이 있어 같은 구성인지 판단 불가 · 같은 제련(%s) 최저 %s(참고)'%(label,format(low,','))
        elif checked:
            reason='같은 제련(%s) %d건 중 상세 확인 %d건 모두 카드·옵션 다름 · 같은 제련 최저 %s(참고)'%(label,len(same),checked,format(low,','))
        else:
            reason='같은 제련(%s) %d건 · 카드·옵션 확인 전(시세 받기 필요) · 최저 %s(참고)'%(label,len(same),format(low,','))
    elif listing_unknown or listing_more:
        reason='같은 제련(%s) 매물 확인 불가: 더 싼 다른 제련·비슷한 이름 매물에 밀려 5쪽 안에 없음'%label
    else:
        reason='같은 제련(%s) 판매 매물 없음'%label
    if fetched_at is not None and now-fetched_at>=FRESH_SEC:
        verdict='보류';reason='오래된 자료(현재 매물 %s 확인) · '%_age(now-fetched_at)+reason
    if maybe_own:reason+=' · 내 노점과 가격이 같은 매물 포함(상점명 미확인)'
    qty=item['quantity']
    return dict(item,now_min=low,avg7=None,recommended=rec,recommendation=verdict,reason=reason,
                expected=None if rec is None else rec*qty,net=None if rec is None else real_amount(rec)*qty,
                difference=None,fetched_at=fetched_at,same_count=len(same),identical_count=len(identical))

def sorted_advice(rows):
    return sorted(rows,key=lambda r:(r['net'] is None,-(r['net'] or 0),r['name']))
