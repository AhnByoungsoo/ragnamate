"""관측 시간/처치/획득량과 저장된 시세로만 사냥터 수익을 계산한다."""
from collections import defaultdict

def report_sample(report,drop_index):
    window=report.get('window') or {};map_id=window.get('map') or ''
    ms=window.get('duration_ms') or 0
    if not map_id or ',' in map_id or ms<=0:return None
    a,b=window.get('start_ms') or 0,window.get('end_ms') or ms     # 전체 구간 Report 는 None 일 수 있음
    # A report's loot/monster totals are for its window, not every segment in its file.
    segments=report.get('segments') or []
    covered=[s for s in segments if s.get('start_ms',0)<b and s.get('end_ms',0)>a]
    if not covered or any(s.get('map')!=map_id or not s.get('is_hunt') for s in covered):return None
    loot=report.get('loot') or {};quantities=defaultdict(float);missing_monsters=0
    if loot.get('available') and loot.get('loot'):
        for row in loot['loot']:
            if row.get('item_id') and row.get('quantity',0)>0:quantities[row['item_id']]+=row['quantity']
        method=loot.get('method') or ''
        basis='실제 획득 기록' if method.startswith(('ITEM_GAIN','획득 패킷')) else '인벤토리 변화 기준'
        note=('획득 이벤트 기준' if basis=='실제 획득 기록' else '인벤토리 앞뒤 비교라 이동·창고 출납·판매가 섞일 수 있는 획득 추정')
    else:
        basis='드랍표 기반 추정';note='rAthena 기본 드랍률 × 관측 처치 수; 국내 서버·드랍 보너스와 다를 수 있음'
        monsters=(report.get('monsters') or {}).get('monsters') or []
        for monster in monsters:
            kills=monster.get('kills') or 0
            if kills<=0:continue
            drops=(drop_index.get(str(monster.get('class_id'))) or {}).get('drops')
            if not drops:missing_monsters+=kills;continue
            for drop in drops:
                quantities[drop['item_id']]+=kills*drop['probability']
    if not quantities:return None
    return dict(map=map_id,ms=ms,quantities=dict(quantities),basis=basis,note=note,
                kills=(report.get('kpi') or {}).get('kills'),server=(report.get('meta') or {}).get('server'),
                missing_monsters=missing_monsters,monsters=(report.get('monsters') or {}).get('monsters') or [])

def calculate_maps(samples,prices,item_name,map_name=lambda x:x,element_for_map=lambda x:'자료 없음'):
    groups=defaultdict(list)
    for s in samples:groups[s['map']].append(s)
    out=[]
    for map_id,records in groups.items():
        priorities={'실제 획득 기록':0,'인벤토리 변화 기준':1,'드랍표 기반 추정':2}
        best=min(priorities[s['basis']] for s in records)
        records=[s for s in records if priorities[s['basis']]==best]
        hours=sum(s['ms'] for s in records)/3600000
        quantities=defaultdict(float)
        for s in records:
            for iid,qty in s['quantities'].items():quantities[iid]+=qty
        items=[];unpriced=[]
        for iid,qty in quantities.items():
            price=prices.get(iid)
            if price is None:unpriced.append(item_name(iid));continue
            items.append(dict(item_id=iid,name=item_name(iid),quantity=qty,price=price,value=qty*price,per_hour=qty*price/hours))
        items.sort(key=lambda x:-x['value'])
        # Missing prices are never interpreted as a zero-income map.
        value=sum(i['value'] for i in items) if items else None
        out.append(dict(map=map_id,name=map_name(map_id),basis=records[0]['basis'],hours=hours,
            kills=sum(s.get('kills') or 0 for s in records),per_hour=None if value is None else value/hours,
            value=value,items=items,unpriced=unpriced,element=element_for_map(map_id),
            note=records[0]['note']+(' · 시세 없음 %d종 제외'%len(unpriced) if unpriced else '')+
                 (' · 드랍표 없는 처치 %d마리 제외'%sum(s['missing_monsters'] for s in records) if any(s['missing_monsters'] for s in records) else ''),
            sessions=len(records)))
    return sorted(out,key=lambda r:(r['per_hour'] is None,-(r['per_hour'] or 0),r['name']))
