"""+10 쉐도우 저가 TOP 20 · 재료 차익 창. 추천만 하며 구매·게임 입력은 하지 않는다.
표는 저장된 +10 검색 결과(30분 캐시)로 그리고, [한 바퀴 확인]·자동 확인만 공식 요청을 한다(shared_client, 2.5초 간격)."""
import datetime
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QSpinBox,QPushButton,QCheckBox,QListWidget,
    QListWidgetItem,QTabWidget,QWidget,QSplitter)
from services import shadow_deals as SD
from services.gnjoy_market import SERVERS
from ui.widgets import Table,Col,comma
from ui.economy_tasks import BackgroundTask

def _when(t):return '-' if not t else datetime.datetime.fromtimestamp(t).strftime('%m-%d %H:%M')
def _shops(names):return '-' if not names else (', '.join(n or '이름 없음' for n in names[:2])+(' 외 %d'%(len(names)-2) if len(names)>2 else ''))

class ShadowDealsDialog(QDialog):
    def __init__(self,parent,controller,item_db,store,client=None):
        super().__init__(parent);self.ctrl=controller;self.item_db=item_db;self.store=store
        self.scanner=SD.ShadowScanner(store,client);self.setWindowTitle('+10 쉐도우 저가 TOP 20');self.resize(1560,760)
        self.setWindowFlag(Qt.Window);self.setModal(False)          # 띄워 둔 채로 다른 창을 쓸 수 있는 별도 창
        cfg=controller.shadow_config() if controller else dict(server='129',percent=SD.DEFAULT_PERCENT,families=list(SD.FAMILIES))
        outer=QVBoxLayout(self)
        note=QLabel('<b>옵션 가치를 반영하지 않은 가격 비교</b> · 같은 서버 + 같은 아이템 + 정확히 +10 판매 매물을 비교합니다. '
                    '기준가 = 같은 +10 장비의 최근 7일 공식 거래 평균(날짜별 평균의 평균, 거래일 3일 이상일 때만). '
                    '랜덤옵션·인챈트는 보지 않습니다. 추천·알림만 하며 구매하지 않습니다. 공식 사이트는 노점 개설 3분 뒤부터 보입니다.')
        note.setWordWrap(True);outer.addWidget(note)
        top=QHBoxLayout();self.server=QComboBox()
        for sid,name in SERVERS.items():self.server.addItem(name,sid)
        self.server.setCurrentIndex(max(0,self.server.findData(cfg['server'])))
        self.percent=QSpinBox(minimum=5,maximum=90,value=int(cfg['percent']),suffix='% 이상 저렴')
        self.auto=QCheckBox('자동 확인(대상을 하나씩 순환, 아이템별 30분마다)');self.auto.setChecked(bool(cfg.get('enabled')))
        self.scan=QPushButton('한 바퀴 확인');self.scan.setToolTip('선택한 계열 전체를 한 번 받습니다(30분 안에 받은 것은 재사용).')
        stop=QPushButton('중지');again=QPushButton('다시 보기')
        for w in (QLabel('서버'),self.server,QLabel('저가 기준'),self.percent,self.auto,self.scan,stop,again):top.addWidget(w)
        top.addStretch();outer.addLayout(top)
        split=QSplitter(Qt.Horizontal);outer.addWidget(split,1)
        self.families=QListWidget()
        for f in SD.FAMILIES:
            it=QListWidgetItem(f);it.setFlags(it.flags()|Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if f in cfg['families'] else Qt.Unchecked);self.families.addItem(it)
        famw=QWidget();fv=QVBoxLayout(famw);fv.setContentsMargins(0,0,0,0);fv.addWidget(QLabel('대상 계열(체크 해제 = 제외)'));fv.addWidget(self.families)
        split.addWidget(famw)
        tabs=QTabWidget();split.addWidget(tabs);split.setSizes([210,1270])
        page=QWidget();pv=QVBoxLayout(page);pv.setContentsMargins(0,0,0,0)
        self.status=QLabel();self.status.setWordWrap(True);pv.addWidget(self.status)
        self.table=Table([Col('순위',lambda r:r['rank'],numeric=True,width=45),
            Col('아이템',lambda r:'+10 '+r['name'],width=190,tooltip=lambda r:r['listing_name']),
            Col('판매가',lambda r:r['price'],comma,width=110),Col('비교 기준가(7일 평균)',lambda r:r['base'],lambda x:comma(int(x)),numeric=True,width=110),
            Col('할인율',lambda r:r['discount'],lambda x:'%.1f%%'%x,numeric=True,width=65),
            Col('가격 차이',lambda r:r['diff'],lambda x:comma(int(x)),numeric=True,width=110),
            Col('상점명',lambda r:r['shop'] or '이름 없음',width=130,tooltip=lambda r:r['shop']),
            Col('위치',SD.location_text,width=120,tooltip=lambda r:'두 번 눌러 상세(맵) 받기' if not r.get('map') else SD.location_text(r)),
            Col('7일 거래일',lambda r:r['days'],numeric=True,width=80),Col('확인 시각',lambda r:r['fetched_at'],_when,numeric=True,width=90),
            Col('추천 이유',lambda r:r['reason'],tooltip=lambda r:r['reason'])],sort_col=None)
        self.table.doubleClicked.connect(lambda idx:self._detail(self.table.row_at(idx)))
        pv.addWidget(self.table,1);tabs.addTab(page,'+10 쉐도우 저가 TOP 20')
        mat=QWidget();mv=QVBoxLayout(mat);mv.setContentsMargins(0,0,0,0)
        mnote=QLabel('저장된 노점 목록(다른 기능이 받은 시세)에서 같은 서버·같은 아이템의 판매노점을 사서 구매노점에 파는 차익입니다. '
                     '판매 수량과 구매노점 남은 매입 수량을 모두 반영하고 같은 수량을 두 번 세지 않습니다. 총차익 100만 제니 이상만. '
                     '구매노점에 팔 때의 수수료·세금은 근거를 확인하지 못해 반영하지 않았고, 이동비는 제외했습니다.')
        mnote.setWordWrap(True);mv.addWidget(mnote)
        self.mat_empty=QLabel('총차익 100만제니 이상 후보 없음');mv.addWidget(self.mat_empty)
        self.materials=Table([Col('아이템',lambda r:r['name'],width=200),Col('구매 원가',lambda r:r['unit_cost'],lambda x:comma(int(x)),numeric=True,width=100),
            Col('구매노점 매입가',lambda r:r['unit_buy'],lambda x:comma(int(x)),numeric=True,width=110),Col('가능한 수량',lambda r:r['qty'],comma,width=80),
            Col('총매입 비용',lambda r:r['cost'],comma,width=120),Col('예상 차익',lambda r:r['profit'],comma,width=120),
            Col('판매 상점',lambda r:_shops(r['sell_shops']),width=170),Col('구매 상점',lambda r:_shops(r['buy_shops']),width=170),
            Col('확인 시각',lambda r:r['fetched_at'],_when,numeric=True)],sort_col=5)
        mv.addWidget(self.materials,1);tabs.addTab(mat,'재료 차익')
        self.task=BackgroundTask(self,self._finished,self._error);self.progress=[0,0,''];self._shown=-1
        self.ticker=QTimer(self,interval=700,timeout=self._tick)
        self.scan.clicked.connect(self._scan_all);stop.clicked.connect(lambda:self.task.stop_event.set());again.clicked.connect(self.refresh)
        for sig in (self.server.currentIndexChanged,self.percent.valueChanged,self.auto.toggled):sig.connect(self._changed)
        self.families.itemChanged.connect(self._changed)
        self.finished.connect(lambda _:(self.ticker.stop(),self.task.stop_event.set()));self.refresh()

    def _families(self):
        return [self.families.item(i).text() for i in range(self.families.count()) if self.families.item(i).checkState()==Qt.Checked]

    def _changed(self,*_):
        """설정 저장 — 컨트롤러는 다음 확인 때 세대가 바뀐 것을 보고 이전 작업 결과를 버린다."""
        if self.ctrl:
            s=self.ctrl.app.s
            s['shadow_watch']=dict(enabled=self.auto.isChecked(),server=self.server.currentData(),families=self._families(),
                                   percent=self.percent.value())
            try:s.save()
            except Exception:pass
        self.refresh()

    def targets(self):
        return SD.shadow_targets(self.item_db,self._families())

    def _own(self):
        return self.ctrl._own() if self.ctrl else []

    def refresh(self):
        server=self.server.currentData();targets=self.targets()
        snaps,details=SD.snapshot_all(self.scanner,server,targets)
        res=SD.evaluate(snaps,targets,self.percent.value(),self._own(),details);self.result=res
        self.table.set_rows(res['top'])
        checked=len(targets)-len(res['unchecked'])
        scope=('조회한 매물 기준: %d종 중 %d종 조회'%(len(targets),checked)+
               (' · 뒤 쪽이 남은 %d종(아이템당 %d쪽까지만 봄)'%(len(res['partial']),SD.MAX_PAGES) if res['partial'] else '')+
               (' — 서버 전체 순위가 아닙니다' if res['partial'] or res['unchecked'] else ''))
        self.status.setText('%s · 적격 %d건 중 %d건 표시 · 비교 자료 부족(최근 7일 거래 3일 미만) %d건 · 0 누락 의심 %d건%s'
                            %(scope,len(res['candidates']),len(res['top']),res['insufficient'],sum(1 for r in res['top'] if r['zero']),
                              '' if res['top'] else ' · 조건에 맞는 매물 없음'))
        mats=SD.arbitrage(self.store,server);self.materials.set_rows(mats)
        self.mat_empty.setVisible(not mats);self.materials.setVisible(bool(mats))

    def _scan_all(self):
        server=self.server.currentData();targets=self.targets();scanner=self.scanner
        self.progress[:]=[0,len(targets),''];self._shown=-1
        def work(stop):
            for t in targets:
                if stop.is_set():break
                self.progress[2]=t['name'];scanner.fetch(server,t,stop);self.progress[0]+=1
        if self.task.start(work):self.scan.setEnabled(False);self.ticker.start();self._tick()

    def _detail(self,row):
        """상세(맵·캐릭터)는 사용자가 연 매물만 한 건 요청."""
        if not row or not row.get('ssi') or row.get('map'):return
        server=self.server.currentData();store=self.store;client=self.scanner.client
        def work(stop):
            det=client.deal_view(server,row['listing_name'] or '+10'+row['name'],row.get('map_code',''),row['ssi'])
            store.save_detail(server,row['ssi'],det)
        self.progress[:]=[0,0,'상세 보기'];self.task.start(work)

    def _tick(self):
        done,total,name=self.progress
        if total:
            left=max(0,total-done)*max(1.0,self.scanner.client.interval)*1.6     # 아이템당 대략 1~2건
            self.status.setText('+10 검색 중 %d/%d · %s · 요청 %.1f초 간격 · 남은 시간 약 %d초(30분 안에 받은 쪽은 재사용)'
                                %(done,total,name,self.scanner.client.interval,left))
        if done!=self._shown:self._shown=done;self.refresh()

    def _finished(self,_):
        self.ticker.stop();self.scan.setEnabled(True);self.refresh()

    def _error(self,exc):
        self.ticker.stop();self.scan.setEnabled(True);self.refresh();self.status.setText('확인 중단: %s'%exc)
