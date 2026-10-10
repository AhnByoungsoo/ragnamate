"""창고 정리, 관심 매물 알림, 사냥터 수익의 앱 연결 지점."""
import os
import threading
from PySide6.QtCore import QObject,QTimer,QEvent
from PySide6.QtWidgets import QPushButton,QHBoxLayout,QLabel,QDialog,QVBoxLayout,QMessageBox,QApplication
from config.settings import BASE_DIR
from services.gnjoy_market import VendingStore,shared_client
from services.deal_watch import DealWatch,alert_text,fingerprint
from services.hunting_profit import HuntingProfitService,server_id
from services import shadow_deals as SD
from ui.economy_tasks import BackgroundTask

class EconomyController(QObject):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.store=VendingStore(os.path.join(BASE_DIR,'data','vending.db'))
        self.client=shared_client();self.watch=DealWatch(self.store,self.client);self.stopped=False;self.own=[]
        try:                                   # 사용자가 정한 요청 간격(바닥 아래로는 내려가지 않음)
            from services.gnjoy_market import FLOOR_INTERVAL
            self.client.interval=max(FLOOR_INTERVAL,float(app.s.get('market_interval',self.client.interval)))
            self.client.floor=FLOOR_INTERVAL
        except Exception:pass
        # 설정 세대: 알림을 끄거나 서버·목록·기준을 바꾸면 올린다. 그 전에 시작한 작업의 결과는 버린다.
        self.generation=0;self._task_gen=None;self._cfg_key=None
        self.task=BackgroundTask(self,self._alerts,self._error)
        # +10 쉐도우 저가 TOP 20 자동 확인: 한 번에 대상 하나씩 순환(30분 캐시·10분 간격), 결과는 캐시로만 평가
        self.shadow=SD.ShadowScanner(self.store,self.client)
        self.shadow_task=BackgroundTask(self,self._shadow_done,self._shadow_error)
        self.shadow_gen=0;self._shadow_task_gen=None;self._shadow_key=None;self.shadow_result=None
        self.tasks=[self.task,self.shadow_task]
        from ui.resale_controller import ResaleController
        self.resale=ResaleController(self)
        self.timer=QTimer(self,interval=30000,timeout=self.tick);self.timer.start()
        app.installEventFilter(self)
        QApplication.instance().aboutToQuit.connect(self.stop)
        self.label=QLabel('관심 매물 알림: 꺼짐')
        row=QHBoxLayout()
        for text,slot in (('노점 검색',self.open_search),('정리 추천',self.open_storage),('관심 매물 알림',self.edit_watch),('+10 쉐도우 TOP 20',self.open_shadow),
                          ('사냥터 수익',self.open_profit),('오늘의 결산',lambda:app.open_settlement())):
            button=QPushButton(text);button.clicked.connect(slot);row.addWidget(button)
        row.addWidget(self.label,1);app.storage_page.layout().insertLayout(0,row)
        resale_row=QHBoxLayout()
        resale_button=QPushButton('고가 매물 구매 추천');resale_button.clicked.connect(self.resale.open)
        resale_row.addWidget(resale_button);resale_row.addWidget(self.resale.status_label,1)
        app.storage_page.layout().insertLayout(1,resale_row)
        menu=app.btn_price.menu();menu.addSeparator()
        menu.addAction('노점 검색…',self.open_search)
        menu.addAction('창고 정리 추천…',self.open_storage)
        menu.addAction('관심 아이템 저가 매물 알림…',self.edit_watch)
        menu.addAction('+10 쉐도우 저가 TOP 20…',self.open_shadow)
        menu.addAction('고가 매물 구매 추천…',self.resale.open)
        menu.addAction('사냥터 수익 추천…',self.open_profit)
        QTimer.singleShot(1500,self.tick)
    def eventFilter(self,obj,event):
        if obj is self.app and event.type()==QEvent.Close:self.stop()
        return False
    def stop(self):
        if self.stopped:return
        self.stopped=True;self.timer.stop()
        self.resale.stop()
        futures=[task.future for task in self.tasks if task.future and not task.future.done()]
        for task in self.tasks:task.close()
        if futures:
            def release():
                for future in futures:
                    try:future.result()
                    except Exception:pass
                self.store.close()
            threading.Thread(target=release,name='market-tools-close',daemon=True).start()
        else:self.store.close()
    def _own(self):
        panel=getattr(self.app,'vend_panel',None);state=getattr(panel,'state',None)
        if state:
            self.own=[dict(name=row.match,price=row.price,shop=state.get('shop') or '',server=panel.sid) for row in state.get('rows',[]) if row.match and row.price]
        return list(self.own)
    def _config_key(self,cfg):
        return (bool(cfg.get('enabled')),cfg.get('server','129'),
                tuple((i.get('item_id'),float(i.get('percent',15))) for i in cfg.get('items') or []))
    def _sync_generation(self):
        cfg=self.app.s.get('deal_watch') or {};key=self._config_key(cfg)
        if key!=self._cfg_key:
            self._cfg_key=key;self.generation+=1
            if self.task.future is not None:self.task.stop_event.set()
        return cfg
    def tick(self):
        if self.stopped:return
        self._shadow_tick()
        self.resale.tick()
        cfg=self._sync_generation()
        if not cfg.get('enabled') or not cfg.get('items'):
            self.label.setText('관심 매물 알림: 꺼짐');return
        if self.client.retry_after:
            self.label.setText('공식 요청 중지 · %d초 뒤 재개'%self.client.retry_after);return
        own=self._own();server=cfg.get('server','129');items=[dict(i) for i in cfg['items']]
        if self.task.start(lambda stop:self.watch.check(server,items,own,stop)):
            self._task_gen=self.generation
            self.label.setText('관심 매물 확인 중 · %d개'%len(items))
    def _deliver(self,text):
        """알림을 띄울 수 있으면 띄우고 True. 트레이가 없거나 숨겨져 있으면 상태 표시줄로."""
        tray=getattr(self.app,'tray',None)
        if tray is not None and tray.isVisible():
            tray.showMessage('관심 아이템 저가 매물',text);return True
        status=getattr(self.app,'status',None)
        if status:
            status(text,15000);return True
        return False
    def _alerts(self,alerts):
        cfg=self._sync_generation()
        # 작업을 시작한 뒤 알림을 끄거나 서버·목록·기준을 바꿨으면 그 작업의 결과는 알리지도 기록하지도 않는다
        if self.stopped or not cfg.get('enabled') or self._task_gen!=self.generation:return
        registered={i['item_id']:i for i in cfg.get('items',[])}
        alerts=[a for a in alerts if a.get('server')==cfg.get('server','129') and a.get('item_id') in registered
                and a['discount']+1e-9>=registered[a['item_id']]['percent']]
        sent=set()
        for alert in alerts:
            key=fingerprint(alert)
            if key in sent or self.watch.seen(alert):continue
            if not self._deliver(alert_text(alert)):break          # 전달 못 했으면 기록하지 않음 → 다음 확인 때 다시 시도
            self.watch.mark_seen(alert);sent.add(key)
        self.label.setText('관심 매물 알림 동작 중 · 이번 알림 %d개 · 아이템별 최소 10분 간격'%len(sent))
    def _error(self,exc):self.label.setText('관심 매물 확인 대기: '+str(exc))
    # ------------------------------------------------------------ +10 쉐도우 자동 확인
    def shadow_config(self):
        cfg=dict(self.app.s.get('shadow_watch') or {})
        cfg.setdefault('server','129');cfg['percent']=float(cfg.get('percent') or SD.DEFAULT_PERCENT)
        cfg['families']=[f for f in (cfg.get('families') or SD.FAMILIES)]
        return cfg
    def _sync_shadow(self):
        cfg=self.shadow_config();key=(bool(cfg.get('enabled')),cfg['server'],tuple(cfg['families']),cfg['percent'])
        if key!=self._shadow_key:
            self._shadow_key=key;self.shadow_gen+=1
            if self.shadow_task.future is not None:self.shadow_task.stop_event.set()
        return cfg
    def _shadow_tick(self):
        cfg=self._sync_shadow()
        if not cfg.get('enabled') or self.client.retry_after:return
        targets=SD.shadow_targets(self.app.item_db,cfg['families']);own=self._own();server=cfg['server'];percent=cfg['percent']
        scanner=self.shadow
        def work(stop):
            due=scanner.next_due(server,targets)
            if due is not None:scanner.fetch(server,due,stop)          # 한 번에 한 대상만(사이트 전체를 긁지 않음)
            snaps,details=SD.snapshot_all(scanner,server,targets)
            return dict(SD.evaluate(snaps,targets,percent,own,details),server=server,checked=due and due['name'])
        if self.shadow_task.start(work):self._shadow_task_gen=self.shadow_gen
    def _shadow_done(self,result):
        cfg=self._sync_shadow()
        # 설정 변경·끄기·종료 뒤 끝난 작업의 결과로는 알리지 않는다
        if self.stopped or not cfg.get('enabled') or self._shadow_task_gen!=self.shadow_gen:return
        self.shadow_result=result
        for row in result['top']:
            if self.shadow.seen(result['server'],row):continue
            if not self._deliver(SD.shadow_alert_text(row)):break      # 전달 못 했으면 기록 안 함
            self.shadow.mark_seen(result['server'],row)
    def _shadow_error(self,exc):self.label.setText('+10 쉐도우 확인 대기: '+str(exc))
    def open_search(self):
        # 창을 띄워 둔 채 게임할 수 있게 모달이 아닌 창 하나를 계속 쓴다(최근 결과·30분 캐시 유지)
        if getattr(self,'search_dialog',None) is None:
            from ui.vend_search_dialog import VendSearchDialog
            self.search_dialog=VendSearchDialog(self.app,self.app.s,self.app.item_db,self.store,self.client,
                map_name=self.app.analysis.gamedata.maps.name,on_watch_changed=self.tick)
            self.tasks.append(self.search_dialog.task)
            self.tasks.append(self.search_dialog.auto_task)
        d=self.search_dialog;d.item_db=self.app.item_db;d.show();d.raise_();d.activateWindow();d.query.setFocus()
    def open_shadow(self):
        # 모달이 아닌 별도 창 — 켜 둔 채로 다른 기능·게임을 쓸 수 있게 한다(창 하나를 계속 재사용)
        from ui.shadow_deals_dialog import ShadowDealsDialog
        if getattr(self,'shadow_dialog',None) is None:
            self.shadow_dialog=ShadowDealsDialog(self.app,self,self.app.item_db,self.store,self.client)
            self.tasks.append(self.shadow_dialog.task)
            self.shadow_dialog.finished.connect(lambda _:self.tick())   # 바뀐 설정 반영(세대 증가)
        self.shadow_dialog.show();self.shadow_dialog.raise_();self.shadow_dialog.activateWindow()
    def edit_watch(self):
        from ui.deal_watch_dialog import DealWatchDialog
        if DealWatchDialog(self.app,self.app.s,self.app.item_db,self.store).exec():
            self.tick()                                          # 바뀐 설정이면 세대가 올라가 이전 작업 결과는 버려진다
    def open_storage(self):
        from ui.storage_advice_dialog import StorageAdviceDialog
        entries=[e for e in self.app.entries if e.location_group=='창고']
        if not entries:
            QMessageBox.information(self.app,'창고 정리 추천','창고 기록이 있는 Replay를 먼저 불러오세요.');return
        dialog=StorageAdviceDialog(self.app,entries,self.app.item_db,self.store,self.client,own=self._own())
        self.tasks.append(dialog.task);dialog.exec()
    def profit_page(self,parent):
        from ui.hunting_profit_page import HuntingProfitPage
        service=HuntingProfitService(self.app.s.abs_path('db_path'),self.store,self.app.item_db,
                self.app.analysis.gamedata,os.path.join(BASE_DIR,'data','rathena','drop_index.json'))
        page=HuntingProfitPage(service,parent)
        self.tasks.append(page.task)
        sid=server_id(getattr(self.app.replay,'server',None)) or '129'
        page.server.setCurrentIndex(max(0,page.server.findData(sid)));return page
    def open_profit(self):
        dialog=QDialog(self.app);dialog.setWindowTitle('사냥터 수익 추천');dialog.resize(1250,800)
        box=QVBoxLayout(dialog);page=self.profit_page(dialog);box.addWidget(page)
        dialog.finished.connect(lambda _:page.task.close());page.reload();dialog.exec()

def install_economy_tools(app):
    app.economy=EconomyController(app)
