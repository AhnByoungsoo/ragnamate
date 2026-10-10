import helpers
import os,time,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
from concurrent.futures import Future
from unittest.mock import patch,MagicMock
import numpy as np
from PySide6.QtWidgets import QApplication,QWidget
from core.models import ItemEntry
from core.analysis import build_rows
from live.storage_detect import StorageResult,digest
from ui.storage_auto import StorageAutoController
from ui.storage_grid import StorageGridDialog

APP=QApplication.instance() or QApplication([])
class Overlay(QWidget):
    def __init__(self):super().__init__();self.shown=[]
    def show_automatic(self,*args):self.shown.append(args);self.show()
class StorageControllerTest(unittest.TestCase):
    def setUp(self):
        self.patcher=patch('ui.storage_auto.ItemIconCache');self.cache=self.patcher.start().return_value
        self.addCleanup(self.patcher.stop);self.cache.ready.return_value=1;self.cache.blocked=False;self.cache.version=0
        self.rows=build_rows([ItemEntry('창고1',1,'a',1)],{},'lowest')
        self.win=SimpleNamespace(hwnd=1,minimized=False,foreground=True,client=(20,30,100,100))
        self.frame=np.ones((100,100,3),np.uint8)
        self.capture=MagicMock();self.capture.closed=False
        self.capture.client_frame.side_effect=lambda win:(self.frame,time.time())
        self.overlay=Overlay();self.addCleanup(self.overlay.close)
        self.c=StorageAutoController(self.overlay,lambda:self.rows,{'source':'창고1'},lambda:'r1',lambda:self.win)
        self.c.executor.shutdown();self.c.executor=MagicMock()
        self.c.executor.submit.side_effect=lambda *args:Future()
        self.c.capture=self.capture;self.c.hwnd=1;self.c.enabled=True
        self.addCleanup(self.c.stop)
    def finish(self):
        self.c.tick()
        result=StorageResult((0,0,40,40),slots=[],fingerprint=digest(self.frame[:40,:40]))
        self.c.future.set_result(result);self.c.tick()
    def test_focus_minimized_and_closed_hide(self):
        self.finish();self.assertTrue(self.overlay.isVisible())
        self.win.foreground=False;self.c.tick();self.assertFalse(self.overlay.isVisible())
        self.win.foreground=True;self.c.tick();self.assertTrue(self.overlay.isVisible())
        self.win.minimized=True;self.c.tick();self.assertFalse(self.overlay.isVisible())
        self.win.minimized=False;self.capture.closed=True;self.c.tick();self.assertFalse(self.overlay.isVisible())
    def test_changed_frame_hides_old_labels_immediately(self):
        self.finish();self.frame[:40,:40]=2;self.c.tick()
        self.assertFalse(self.overlay.isVisible());self.assertIsNone(self.c.last_result)
    def test_replay_change_rejects_old_worker_result(self):
        self.c.tick();self.rows=build_rows([ItemEntry('창고1',2,'b',1)],{},'lowest')
        self.c.future.set_result(StorageResult((0,0,40,40),fingerprint=digest(self.frame[:40,:40])))
        self.c.tick();self.assertIsNone(self.c.last_result);self.assertFalse(self.overlay.isVisible())
    def test_worker_exception_recovers_on_next_tick(self):
        self.c.tick();self.c.future.set_exception(ValueError('test failure'));self.c.tick()
        self.assertIsNone(self.c.future);self.assertFalse(self.overlay.isVisible())
        self.c.tick();self.assertIsNotNone(self.c.future)
    def test_stale_capture_hides(self):
        self.finish();self.capture.client_frame.side_effect=lambda win:(self.frame,time.time()-5)
        self.c.tick();self.assertFalse(self.overlay.isVisible())
    def test_dialog_can_save_auto_without_manual_confirmation(self):
        class Settings(dict):
            def save(self):self.saved=True
        s=Settings();dialog=StorageGridDialog(s,self.rows)
        self.addCleanup(dialog.close)
        dialog._save()
        self.assertEqual(s['storage_grid']['mode'],'auto');self.assertTrue(s['storage_grid']['enabled'])
        self.assertEqual(s['storage_grid']['source'],'창고1')

if __name__=='__main__':unittest.main()
