"""main.py 없이 실제 위젯과 가짜 컨트롤러/시세를 검증한다."""
import helpers  # noqa
import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QMenu, QToolButton, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from config.settings import Settings as DiskSettings
from services import gnjoy_market as G, resale_watch as R
from ui.economy_controller import EconomyController
from ui.resale_watch_dialog import ResaleWatchDialog, ResalePopup
from test_resale_watch import DB, TARGET, evaluate, NOW

APP=QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/malgun.ttf')
from ui.theme import apply_theme
apply_theme(APP)


class Settings(dict):
    def save(self): pass


class ResaleUITest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=G.VendingStore(str(Path(self.tmp.name)/'v.db'))
        self.app=QWidget(); self.app.s=Settings(resale_watch=R.config()); self.app.item_db=DB
        self.app.storage_page=QWidget(self.app); QVBoxLayout(self.app.storage_page)
        self.app.btn_price=QToolButton(self.app); self.app.btn_price.setMenu(QMenu(self.app))
        self.app.entries=[]; self.app.tray=MagicMock(); self.app.status=MagicMock()
        self.app.replay=None; self.app.analysis=SimpleNamespace(gamedata=None)
        self.client=G.GnjoyClient(opener=lambda *a: self.fail('네트워크 요청 금지'))
        with patch('ui.economy_controller.VendingStore',return_value=self.store),patch('ui.economy_controller.shared_client',return_value=self.client):
            self.economy=EconomyController(self.app)
        self.economy.timer.stop(); self.c=self.economy.resale
        self.addCleanup(self.app.close); self.addCleanup(self.economy.stop)

    def arm(self):
        self.app.s['resale_watch']=R.config(dict(enabled=True))
        self.c.sync(); self.c.task_generation=self.c.generation; self.c.allow_alerts=True
        result=evaluate(); result.update(server='129',checked_at=NOW)
        return result

    def test_storage_button_and_menu_added_and_default_off(self):
        titles=[b.text() for b in self.app.storage_page.findChildren(QPushButton)]
        self.assertIn('고가 매물 구매 추천',titles)
        self.assertIn('고가 매물 구매 추천…',[a.text() for a in self.app.btn_price.menu().actions()])
        self.c.tick(); self.assertIsNone(self.c.task.future)

    def test_config_persists_with_real_loader(self):
        path=str(Path(self.tmp.name)/'settings.json')
        settings=DiskSettings(path); settings['resale_watch']=R.config(dict(max_ratio=75,targets=[TARGET])); settings.save()
        self.assertEqual(DiskSettings(path)['resale_watch']['max_ratio'],75)
        self.assertEqual(DiskSettings(path)['resale_watch']['targets'],[TARGET])

    def test_dialog_cancel_does_not_save_draft(self):
        before=copy.deepcopy(dict(self.app.s))
        d=ResaleWatchDialog(self.app,self.c); self.addCleanup(d.close)
        d.enabled.setChecked(True); d.ratio.setValue(60); d.query.setText(TARGET['query']); d.add_target(); d.close()
        self.assertEqual(dict(self.app.s),before)

    def test_settings_save_and_cached_preview_without_request(self):
        d=ResaleWatchDialog(self.app,self.c); self.addCleanup(d.close)
        d.ratio.setValue(75);d.query.setText(TARGET['query']);d.add_target();d.save_settings()
        self.assertEqual(self.app.s['resale_watch']['max_ratio'],75)
        self.assertEqual(self.app.s['resale_watch']['targets'],[TARGET])
        self.c.refresh_cached();self.assertEqual(self.client.requests,0)

    def test_only_delivered_popups_recorded_and_duplicates_suppressed(self):
        result=self.arm(); row=result['rows'][0]
        with patch.object(self.c,'notify',return_value=False) as notify:
            self.c.done(result);notify.assert_called_once();self.assertFalse(self.c.scanner.seen(row))
        with patch.object(self.c,'notify',return_value=True) as notify:
            self.c.done(result);self.c.done(result);notify.assert_called_once();self.assertTrue(self.c.scanner.seen(row))

    def test_changed_server_disable_and_shutdown_drop_old_results(self):
        for change in ('server','disabled','shutdown'):
            result=self.arm()
            if change=='server':self.app.s['resale_watch']['server']='229'
            elif change=='disabled':self.app.s['resale_watch']['enabled']=False
            else:self.economy.stopped=True
            with patch.object(self.c,'notify') as notify:
                self.c.done(result); notify.assert_not_called()
            self.economy.stopped=False

    def test_manual_check_does_not_alert(self):
        result=self.arm();self.c.allow_alerts=False
        with patch.object(self.c,'notify') as notify:self.c.done(result);notify.assert_not_called()

    def test_popup_actual_widget_and_numeric_table(self):
        result=evaluate(); d=ResaleWatchDialog(self.app,self.c);self.addCleanup(d.close)
        d.set_result(result);d.show_detail(d.table.proxy.index(0,0))
        self.assertIn('세후 수령 예상',d.detail.text())
        self.assertEqual(d.table.model_.columnCount(),11)
        popup=ResalePopup(self.app,result['rows'],lambda:None);self.addCleanup(popup.close)
        popup.show();APP.processEvents();self.assertTrue(popup.isVisible())
        self.assertTrue(popup.testAttribute(Qt.WA_ShowWithoutActivating))
        out=os.environ.get('RESALE_PREVIEW_DIR')
        if out:
            Path(out).mkdir(exist_ok=True,parents=True)
            d.show();APP.processEvents();self.assertTrue(d.grab().save(str(Path(out)/'resale-dialog.png')))
            self.assertTrue(popup.grab().save(str(Path(out)/'resale-popup.png')))


if __name__=='__main__':unittest.main()
