"""기존 분석 기록과 공식 판매 시세 캐시만 읽는다. 홈페이지 요청 없음."""
import json,sqlite3,zlib
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from core.hunting_profit import report_sample,calculate_maps
from core import elements
from services.gnjoy_market import SERVERS

def server_id(name):
    return next((sid for sid,label in SERVERS.items() if name in (sid,label)),None)

class HuntingProfitService:
    def __init__(self,db_path,store,item_db,gamedata,drop_path):
        self.db_path=db_path;self.store=store;self.item_db=item_db;self.gamedata=gamedata;self.drop_path=drop_path

    def load(self,server):
        try:drops=json.loads(Path(self.drop_path).read_text(encoding='utf-8'))['monsters']
        except (OSError,ValueError,KeyError):drops={}      # 없으면 드랍표 추정 불가 — 상태 글에 알린다
        samples=[];total=corrupt=0
        if Path(self.db_path).is_file():
            with closing(sqlite3.connect(Path(self.db_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'hunting_window_reports' in tables:
                    rows=db.execute('SELECT h.report_json FROM hunting_window_reports h JOIN replay_sessions r ON r.id=h.session_id '
                        'UNION ALL SELECT r.report_json FROM replay_sessions r WHERE r.report_json IS NOT NULL AND NOT EXISTS '
                        '(SELECT 1 FROM hunting_window_reports h WHERE h.session_id=r.id)')
                else:rows=db.execute('SELECT report_json FROM replay_sessions WHERE report_json IS NOT NULL')
                for (blob,) in rows:
                    total+=1
                    try:report=json.loads(zlib.decompress(blob))
                    except (ValueError,zlib.error,TypeError):corrupt+=1;continue
                    if server_id((report.get('meta') or {}).get('server'))!=server:continue
                    sample=report_sample(report,drops)
                    if sample:samples.append(sample)
        ids={i for s in samples for i in s['quantities']};prices={};times=[]
        for iid in ids:
            at,ls=self.store.latest_listings(server,iid)
            sales=[x['price'] for x in ls if x['kind']=='sale' and x['price'] is not None]
            if sales:prices[iid]=min(sales);times.append(at)
        advisor=elements.shared()
        observed=defaultdict(lambda:defaultdict(lambda:dict(name='',weight=0)))
        for sample in samples:
            for monster in sample.get('monsters',[]):
                cid=monster.get('class_id')
                if cid is None:continue
                row=observed[sample['map']][cid]
                row['name']=monster.get('name') or row['name'];row['weight']+=monster.get('kills') or monster.get('encounters') or 0
        def recommendation(map_id):
            name=self.gamedata.maps.name(map_id)
            # MapDB labels may contain the id in parentheses; don't guess a map from substring matches.
            display=name.rsplit(' (',1)[0]
            monsters=[dict(class_id=cid,**row) for cid,row in observed[map_id].items() if row['weight']]
            rec=advisor.recommend(monsters or advisor.map_monsters(display))
            if not rec.get('available'):return '속성 자료 없음'
            best=rec['elements'][0]
            estimated=elements.SOURCE_RATHENA in rec.get('sources',{}) or rec.get('attr_source')==elements.SOURCE_RATHENA
            return '%s 속성 %d%%%s'%(elements.ELEMENT_KO[best['element']],round(best['avg']),' · 추정' if estimated else '')
        rows=calculate_maps(samples,prices,self.item_db.name,self.gamedata.maps.name,recommendation)
        status='저장된 공식 판매 최저가만 사용 · %d개 기록 중 사용 가능 사냥 구간 %d개'%(total,len(samples))
        # 자료가 부족한 이유를 숨기지 않는다(0개·전부 시세 없음일 때 사용자가 할 일을 알 수 있게)
        if not drops:status+=' · 드랍표 파일을 읽지 못해 드랍표 추정을 할 수 없음'
        if corrupt:status+=' · 읽지 못한 기록 %d개'%corrupt
        if rows and all(r['per_hour'] is None for r in rows):
            status+=' · 모든 사냥터가 시세 없음: 드랍 아이템의 공식 판매 시세가 저장돼 있지 않습니다(노점 시세 비교·정리 추천에서 받은 시세만 씀)'
        return dict(rows=rows,reports=total,samples=len(samples),corrupt=corrupt,price_at=min(times) if times else None,
                    status=status)
