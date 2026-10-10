import copy
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import helpers
from core import mouse_coords as C, macros as M, storage_grid as G
from core.analysis import build_rows
from core.models import ItemEntry, PriceInfo


def rows():
    return build_rows([ItemEntry('창고1',1,'오리데오콘',10,raw_data={'index':3}),
                       ItemEntry('창고1',2,'검',1,refine=9,is_equip=True,raw_data={'index':4}),
                       ItemEntry('창고1',3,'시세 없는 아이템',1,raw_data={'index':5}),
                       ItemEntry('인벤토리',1,'오리데오콘',2)],
                      {1:PriceInfo(1,12000,15000,17000,3,1700000000),
                       2:PriceInfo(2,1200000,1500000,1700000,2,1700000000)},'average')

def config(data):
    return dict(source='창고1',confirmed=True,slots=[0,1,2,None],fingerprint=G.fingerprint(data,'창고1','session'),
                x=100,y=100,columns=2,rows=2,cell_w=50,cell_h=50)


class CoordinatesTest(unittest.TestCase):
    def test_game_position_tracks_window(self):
        step=dict(type='click',position='game',x=20,y=30)
        self.assertEqual(C.resolve_position(step,(-1920,50,800,600)),(-1900,80))
        self.assertEqual(C.resolve_position(step,(100,200,800,600)),(120,230))

    def test_negative_monitor_absolute(self):
        self.assertEqual(C.absolute_position(-1920,0,(-1920,0,3840,1080)),(0,0))
        self.assertEqual(C.absolute_position(1919,1079,(-1920,0,3840,1080)),(65535,65535))
        with self.assertRaises(ValueError): C.absolute_position(1920,0,(-1920,0,3840,1080))

    def test_capture_and_bounds(self):
        self.assertEqual(C.capture_position('game',(120,230),(100,200,800,600)),(20,30))
        with self.assertRaises(ValueError): C.capture_position('game',(99,230),(100,200,800,600))
        with self.assertRaises(ValueError): C.resolve_position(dict(type='click',position='game',x=800,y=1),(0,0,800,600))

    def test_legacy_click_and_invalid_positions(self):
        self.assertIsNone(C.resolve_position(dict(type='click')))
        for step in (dict(type='move'),dict(type='click',position='bad'),
                     dict(type='click',position='game',x=-1,y=0),dict(type='click',position='screen',x='1',y=2)):
            self.assertTrue(M.validate([dict(M.new_macro(),steps=[step])]))

    def test_move_then_click_and_move_only(self):
        log=[]
        M.run_steps([dict(type='click',position='screen',x=-30,y=20),
                     dict(type='move',position='screen',x=1,y=2),dict(type='rclick')],
                    move=lambda x,y:log.append(('move',x,y)),click=lambda right:log.append(('click',right)))
        self.assertEqual(log,[('move',-30,20),('click',False),('move',1,2),('click',True)])

    def test_cancel_between_move_and_click(self):
        stop=threading.Event();click=[]
        M.run_steps([dict(type='click',position='screen',x=1,y=2)],stop,
                    move=lambda x,y:stop.set(),click=lambda right:click.append(right))
        self.assertFalse(click)

    def test_virtual_desktop_flags_without_input(self):
        import ctypes
        with patch.object(ctypes.windll.user32,'GetSystemMetrics',side_effect=[-1920,0,3840,1080]),patch.object(M,'_send') as send:
            M._move_to(-1920,0)
            inp=send.call_args.args[0]
            self.assertEqual((inp.u.mi.dx,inp.u.mi.dy,inp.u.mi.dwFlags),(0,0,0xC001))


class GridTest(unittest.TestCase):
    def test_slots_keep_unknown_and_variant(self):
        data=rows(); cfg=config(data)
        values=G.labels(data,cfg,'session')
        self.assertEqual([v['text'] if v else None for v in values],['1.2만','~120만','시세 없음',None])
        self.assertEqual(values[0]['price'],12000)  # average 선택 상태여도 판매 최저가를 정확히 사용

    def test_snapshot_change_invalidates_and_price_refresh_does_not(self):
        data=rows(); cfg=config(data)
        data[0].price=PriceInfo(1,20000,20000,20000,3,1800000000)
        self.assertEqual(G.labels(data,cfg,'session')[0]['price'],20000)
        self.assertIsNone(G.labels(data,cfg,'other-character'))
        data[0].entry.quantity+=1
        self.assertIsNone(G.labels(data,cfg,'session'))

    def test_explicit_mapping_and_unconfirmed(self):
        data=rows();cfg=config(data);cfg['slots']=[2,None,0]
        self.assertEqual(G.labels(data,cfg,'session')[2]['name'],'오리데오콘')
        cfg['confirmed']=False
        self.assertIsNone(G.labels(data,cfg,'session'))

    def test_physical_rect_boundary(self):
        cfg=config(rows())
        self.assertEqual(G.physical_rect(cfg,(-1920,20,800,600)),(-1820,96,100,124))
        with self.assertRaises(ValueError): G.physical_rect(dict(cfg,x=790),(-1920,20,800,600))


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt=QApplication.instance() or QApplication([])

    def test_step_dialog_roundtrip_and_capture(self):
        from ui.pages.macro_page import StepDialog
        step=dict(type='rclick',position='game',x=120,y=230)
        dialog=StepDialog(step)
        self.assertEqual(dialog.step(),step)
        with patch.object(C,'capture_position',return_value=(55,66)):
            dialog._capture_position()
        self.assertEqual((dialog.step()['x'],dialog.step()['y']),(55,66))
        dialog.close()

    def test_grid_save_reload_and_edit_invalidates_confirmation(self):
        from config.settings import Settings
        from ui.storage_grid import StorageGridDialog
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(os.path.join(tmp,'settings.json'))
            dialog=StorageGridDialog(settings,rows(),'session')
            dialog.mode.setCurrentIndex(dialog.mode.findData('manual'))
            self.assertFalse(dialog.confirm.isChecked())
            dialog.confirm.setChecked(True)
            dialog.table.cellWidget(0,1).setCurrentIndex(0)
            self.assertFalse(dialog.confirm.isChecked())
            dialog.confirm.setChecked(True)
            dialog._save()
            loaded=Settings(settings.path)
            self.assertIsNotNone(G.labels(rows(),loaded['storage_grid'],'session'))
            self.assertIsNone(G.labels(rows(),loaded['storage_grid'],'session')[0])
            self.assertEqual(loaded['hotkeys']['storage_grid'],'Ctrl+Alt+G')
            dialog.close()

    def test_overlay_clickthrough_focus_and_stale(self):
        from PySide6.QtCore import Qt
        from ui.storage_grid import StorageGridOverlay
        data=rows();cfg=config(data)
        overlay=StorageGridOverlay(lambda:data,cfg,lambda:'session',
                  window_provider=lambda:SimpleNamespace(minimized=False,foreground=False,client=(0,0,800,600)))
        self.assertTrue(overlay.windowFlags() & Qt.WindowTransparentForInput)
        overlay.start()
        self.assertFalse(overlay.isVisible())
        self.assertTrue(overlay.enabled)
        data[0].entry.quantity+=1
        overlay.refresh()
        self.assertFalse(overlay.enabled)
        self.assertIn('다시 확인',overlay.status)
        overlay.close()
