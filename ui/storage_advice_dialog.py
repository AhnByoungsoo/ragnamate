"""창고 정리 추천 표. 선택한 항목 또는 전체를 공식 조회하며, 받기 전·중에는 캐시를 표시한다.
장비는 같은 제련(목록 이름)·같은 카드(상세 보기) 매물과 비교하고, 장비에 꽂힌 카드는 참고 줄로 따로 보여 준다."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton
from core.storage_advice import storage_items,advise,advise_variant,sorted_advice
from services.market_insights import MarketInsights
from services.gnjoy_market import SERVERS
from ui.widgets import Table,Col,comma
from ui.economy_tasks import BackgroundTask

SEC_PER_ITEM=(5,15)          # 한 종류 받는 데 걸리는 대략 시간(요청 2.5초 간격 × 2~6번)

def _label(r):
    if r.get('socketed'):return '  └ 카드: %s'%r['name']
    return ('+%d '%r['refine'] if r['refine'] else '')+r['name']+(' [%s]'%', '.join(r['card_names']) if r.get('card_names') else '')

class StorageAdviceDialog(QDialog):
    def __init__(self,parent,entries,item_db,store,client=None,own=()):
        super().__init__(parent);self.setWindowTitle('창고 정리 추천');self.resize(1320,720);self.own=list(own)
        self.items,self.excluded=storage_items(entries,item_db);self.market=MarketInsights(store,client)
        box=QVBoxLayout(self)
        note=QLabel('추천은 참고용입니다. 공식 날짜별 거래 기록으로 판단하며 실제 거래 건수·판매 성공을 보장하지 않습니다.\n'
                    '장비는 공식 목록 이름의 제련이 같은 매물과, 슬롯이 있으면 상세 보기의 카드가 같은 매물과 비교합니다(같은 구성 최저가 기준 참고가).\n'
                    '장비에 꽂힌 카드는 "└ 카드" 줄로 시세만 보여 주고 합계에는 넣지 않습니다(분리해야 판매 가능).')
        note.setWordWrap(True);box.addWidget(note)
        controls=QHBoxLayout();self.server=QComboBox()
        for sid,name in SERVERS.items():self.server.addItem(name,sid)
        self.server.setCurrentIndex(max(0,self.server.findData(store.meta('server','129'))))
        controls.addWidget(QLabel('서버'));controls.addWidget(self.server)
        self.fetch=QPushButton('선택한 아이템 시세 받기');self.fetch.clicked.connect(self._fetch)
        self.fetch_all=QPushButton('모든 아이템 시세 갱신');self.fetch_all.clicked.connect(self._fetch_everything)
        self.fetch_all.setToolTip('창고의 모든 아이템을 값 큰 순으로 받습니다. 30분 안에 받은 것은 다시 요청하지 않습니다.')
        controls.addWidget(self.fetch);controls.addWidget(self.fetch_all)
        cancel=QPushButton('받기 중지');cancel.clicked.connect(lambda:self.task.stop_event.set());controls.addWidget(cancel)
        controls.addStretch();box.addLayout(controls)
        self.status=QLabel();self.status.setWordWrap(True);box.addWidget(self.status)
        self.table=Table([Col('아이템',_label,width=260,tooltip=_label),
            Col('수량',lambda r:r['quantity'],comma,width=60),Col('지금 최저가',lambda r:r['now_min'],comma,width=105),
            Col('7일 평균',lambda r:r['avg7'],lambda x:'-' if x is None else comma(int(x)),numeric=True,width=105),
            Col('추천가',lambda r:r['recommended'],comma,width=105),Col('추천',lambda r:r['recommendation'],width=82),
            Col('이유',lambda r:r['reason'],width=360,tooltip=lambda r:r['reason']),
            Col('예상 판매액(세후)',lambda r:r['net'],comma,width=140,tooltip=lambda r:'세전 예상 판매액: '+comma(r['expected']))],sort_col=7)
        box.addWidget(self.table,1)
        self.task=BackgroundTask(self,lambda _:self._finished(),self._error)
        self.progress=[0,0,'']                  # 작업 스레드가 숫자만 바꾼다(위젯은 화면 스레드의 타이머가 그린다)
        self._shown=-1
        self.ticker=QTimer(self,interval=700,timeout=self._tick)
        self.server.currentIndexChanged.connect(lambda _:self.refresh())
        self.finished.connect(lambda _:(self.ticker.stop(),self.task.close()));self.refresh()

    def _row(self,server,item):
        data=self.market.cached(server,item['item_id'])
        own=[o for o in self.own if o.get('server',server)==server and
             (o.get('item_id')==item['item_id'] or o.get('name')==item['name'])]
        if item.get('variant') and not item.get('socketed'):
            return advise_variant(item,data['listings'],self.market.details(server,data['listings']),
                                  fetched_at=data['fetched_at'],listing_unknown=data.get('listing_unknown'),
                                  listing_more=data.get('listing_more'),own=own)
        return advise(item,data['history'],data['listings'],fetched_at=data['fetched_at'],
                      history_at=data['history_at'],listing_unknown=data.get('listing_unknown'),own=own)

    def refresh(self):
        server=self.server.currentData();rows=[self._row(server,item) for item in self.items]
        self._rows=rows;self.table.set_rows(sorted_advice(rows))
        if self.task.future is None:
            self.status.setText('%d줄 표시(장착 카드 %d줄 포함) · 거래 불가/귀속 %d개 기록 제외 · 30분 이내 자료 재사용'
                                %(len(rows),sum(1 for r in rows if r.get('socketed')),self.excluded))

    def _start(self,items):
        server=self.server.currentData();self.progress[:]=[0,len(items),''];self._shown=-1
        def work(stop):
            for item in items:
                if stop.is_set():break
                self.progress[2]=item['name']
                if item.get('variant') and not item.get('socketed'):self.market.fetch_variant(server,item,stop=stop)
                else:self.market.fetch(server,item['item_id'],item['name'],stop=stop)
                self.progress[0]+=1
        if self.task.start(work):
            self.fetch.setEnabled(False);self.fetch_all.setEnabled(False);self.server.setEnabled(False)
            self.ticker.start();self._tick()

    def _fetch(self):
        rows=self.table.selected_rows()
        if not rows:self.status.setText('시세를 받을 아이템을 표에서 선택하세요.');return
        self._start(self._unique(rows))

    def _fetch_everything(self):
        # 값이 큰 것부터: 지금 캐시 기준 예상 판매액(없으면 뒤로)
        rows=sorted(self._rows,key=lambda r:(r.get('socketed',False),-(r['net'] or r['now_min'] or 0)))
        self._start(self._unique(rows))

    @staticmethod
    def _unique(rows):
        out,seen=[],set()
        for r in rows:
            key=(r['item_id'],r.get('refine',0),tuple(r.get('card_names') or ())) if r.get('variant') and not r.get('socketed') else (r['item_id'],)
            if key not in seen:seen.add(key);out.append(r)
        return out

    def _tick(self):
        done,total,name=self.progress
        left=max(0,total-done)
        self.status.setText('공식 시세 받는 중 %d/%d · %s · 남은 예상 %d~%d분(30분 안에 받은 것은 건너뜀) · 요청 2.5초 간격'
                            %(done,total,name,left*SEC_PER_ITEM[0]//60,-(-left*SEC_PER_ITEM[1]//60)))
        if done!=self._shown:self._shown=done;self.refresh()      # 받는 대로 표를 갱신

    def _finished(self):
        self.ticker.stop()
        self.fetch.setEnabled(True);self.fetch_all.setEnabled(True);self.server.setEnabled(True);self.refresh()
        done,total,_=self.progress
        if total:self.status.setText(self.status.text()+' · 이번에 %d/%d종 확인'%(done,total))

    def _error(self,error):
        self._finished();self.status.setText(str(error))
