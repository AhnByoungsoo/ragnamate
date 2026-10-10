import helpers
import os,tempfile,unittest,time,datetime
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QToolButton,QMenu
from PySide6.QtCore import Qt
from core.models import ItemEntry
from services.gnjoy_market import VendingStore,GnjoyClient
from ui.storage_advice_dialog import StorageAdviceDialog
from ui.deal_watch_dialog import DealWatchDialog
from ui.hunting_profit_page import HuntingProfitPage
from ui.economy_controller import EconomyController

APP=QApplication.instance() or QApplication([])
class Settings(dict):
    def save(self):self.saved=True
    def abs_path(self,key):return self[key]
class EconomyWidgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=VendingStore(os.path.join(self.tmp.name,'v.db'));self.addCleanup(self.store.close)
        self.db=SimpleNamespace(items={1:dict(name='a'),2:dict(name='b')},get=lambda i:dict(description='일반'),name=lambda i:str(i))
    def test_watch_editor_saves_korean_name_threshold_server(self):
        s=Settings();d=DealWatchDialog(None,s,self.db);self.addCleanup(d.close)
        d.search.setText('a');d.percent.setValue(20);d._add();d.enabled.setChecked(True)          # 이름만 쳐도 하나뿐이면 등록
        d.server.setCurrentIndex(d.server.findData('229'));d._save()
        self.assertEqual(s['deal_watch']['server'],'229');self.assertEqual(s['deal_watch']['items'],[dict(item_id=1,name='a',percent=20)])
    def test_storage_table_has_required_columns_sorted_by_net(self):
        now=time.time();day=datetime.date.today().isoformat()
        for iid,price in ((1,100),(2,10000)):
            self.store.save_listings('129',iid,[dict(price=price,qty=1,shop='a',kind='sale')],now=now)
            self.store.save_history('129',iid,[dict(day=day,min=price,avg=price,max=price)],now=now)
        d=StorageAdviceDialog(None,[ItemEntry('창고1',1,'a',2),ItemEntry('창고1',2,'b',2)],self.db,self.store,GnjoyClient(opener=lambda *args:None))
        self.addCleanup(d.close)
        self.assertEqual(d.table.model_.columnCount(),8)
        self.assertEqual(d.table.row_at(d.table.proxy.index(0,0))['item_id'],2)
    def test_profit_sort_is_numeric_and_no_price_last(self):
        p=HuntingProfitPage(SimpleNamespace(load=lambda server:{}));self.addCleanup(p.task.close)
        rows=[dict(name=name,per_hour=value,basis='실제 획득 기록',hours=1,kills=1,element='화',note='',items=[],unpriced=[]) for name,value in [('low',9000),('high',10000),('none',None)]]
        p._done(dict(rows=rows,status='test',price_at=None))
        self.assertEqual(p.maps.row_at(p.maps.proxy.index(0,0))['name'],'high')
        self.assertEqual(p.maps.row_at(p.maps.proxy.index(2,0))['name'],'none')
        p.close()
    def test_controller_buttons_tray_and_shutdown(self):
        app=QWidget();self.addCleanup(app.close)
        app.s=Settings(db_path=os.path.join(self.tmp.name,'market.db'),deal_watch=dict(enabled=True,server='129',items=[dict(item_id=1,name='a',percent=15)]))
        app.storage_page=QWidget(app);QVBoxLayout(app.storage_page)
        app.btn_price=QToolButton(app);app.btn_price.setMenu(QMenu(app))
        app.entries=[];app.item_db=self.db;app.tray=MagicMock();app.status=MagicMock()
        app.analysis=SimpleNamespace(gamedata=None);app.replay=None
        client=GnjoyClient(opener=lambda *args:None)
        with patch('ui.economy_controller.BASE_DIR',self.tmp.name),patch('ui.economy_controller.VendingStore',return_value=self.store),patch('ui.economy_controller.shared_client',return_value=client):
            controller=EconomyController(app)
        self.addCleanup(controller.stop)
        titles=[a.text() for a in app.btn_price.menu().actions()]
        self.assertIn('창고 정리 추천…',titles)
        controller._sync_generation();controller._task_gen=controller.generation   # 지금 설정으로 시작한 작업의 결과
        controller._alerts([dict(item_id=1,name='a',price=85,discount=15,shop='매물',server='129')])
        app.tray.showMessage.assert_called_once();controller.stop();self.assertFalse(controller.timer.isActive())
        controller._alerts([dict(item_id=1,name='a',price=85,discount=15,shop='매물',server='129')])
        app.tray.showMessage.assert_called_once()

    def _controller(self,tray):
        app=QWidget();self.addCleanup(app.close)
        app.s=Settings(db_path=os.path.join(self.tmp.name,'market.db'),deal_watch=dict(enabled=True,server='129',items=[dict(item_id=1,name='a',percent=15)]))
        app.storage_page=QWidget(app);QVBoxLayout(app.storage_page)
        app.btn_price=QToolButton(app);app.btn_price.setMenu(QMenu(app))
        app.entries=[];app.item_db=self.db;app.tray=tray;app.status=None
        app.analysis=SimpleNamespace(gamedata=None);app.replay=None
        client=GnjoyClient(opener=lambda *args:None)
        with patch('ui.economy_controller.BASE_DIR',self.tmp.name),patch('ui.economy_controller.VendingStore',return_value=self.store),patch('ui.economy_controller.shared_client',return_value=client):
            c=EconomyController(app)
        c.timer.stop();self.addCleanup(c.stop)
        return app,c
    ALERT=dict(item_id=1,name='a',price=85,qty=1,discount=15,shop='매물',server='129')
    def test_settings_change_during_task_drops_old_results_without_recording(self):
        app,c=self._controller(MagicMock())
        c._sync_generation();c._task_gen=c.generation                    # 작업 시작
        app.s['deal_watch']=dict(app.s['deal_watch'],server='229')       # 작업 중 서버 변경
        c._alerts([dict(self.ALERT)])
        app.tray.showMessage.assert_not_called();self.assertFalse(c.watch.seen(self.ALERT))
        app.s['deal_watch']=dict(app.s['deal_watch'],server='129',enabled=False)
        c._sync_generation();c._task_gen=c.generation;c._alerts([dict(self.ALERT)])      # 꺼진 뒤 결과도 버림
        app.tray.showMessage.assert_not_called()
    def test_undelivered_alert_is_not_recorded(self):
        tray=MagicMock();tray.isVisible.return_value=False               # 트레이 숨김·상태줄 없음 → 전달 불가
        app,c=self._controller(tray)
        c._sync_generation();c._task_gen=c.generation
        c._alerts([dict(self.ALERT)]);self.assertFalse(c.watch.seen(self.ALERT))
        tray.isVisible.return_value=True;c._alerts([dict(self.ALERT),dict(self.ALERT)])
        tray.showMessage.assert_called_once();self.assertTrue(c.watch.seen(self.ALERT))   # 같은 매물 두 줄도 한 번만
    def test_storage_average_column_sorts_numerically(self):
        now=time.time();day=datetime.date.today().isoformat()
        for iid,price in ((1,999),(2,1234)):
            self.store.save_listings('129',iid,[dict(price=price,qty=1,shop='a',kind='sale')],now=now)
            self.store.save_history('129',iid,[dict(day=day,min=price,avg=price,max=price)],now=now)
        d=StorageAdviceDialog(None,[ItemEntry('창고1',1,'a',1),ItemEntry('창고1',2,'b',1)],self.db,self.store,GnjoyClient(opener=lambda *args:None))
        self.addCleanup(d.close);d.table.sortByColumn(3,Qt.DescendingOrder)
        self.assertEqual(d.table.row_at(d.table.proxy.index(0,0))['avg7'],1234)   # 글자 정렬이면 '999' 가 먼저

if __name__=='__main__':unittest.main()
