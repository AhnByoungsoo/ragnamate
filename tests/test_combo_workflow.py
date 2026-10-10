import copy
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helpers import PLAYER, ROOT, ev, sample
from analyzer.events import EventType as E
from core import combo as C, macros as M


def parsed(events=(), packets=()):
    return SimpleNamespace(events=list(events), packets=list(packets), player_aid=PLAYER,
        server='server', character='character', job_id=4262, base_level=208, job_level=32,
        path='fixture.rrf', recorded_at='2026-09-25')


class ProfileRegressionTest(unittest.TestCase):
    def test_raw_time_alignment_no_future_leak(self):
        p = parsed([ev(20, E.SKILL_CAST_START, PLAYER, skill=7, cast_ms=0)],
                   [(10000, struct.pack('<HHi', C.PAR_CHANGE, C.VAR_ASPD, 90)),
                    (10100, struct.pack('<HHi', C.PAR_CHANGE, C.VAR_ASPD, 70))])
        self.assertEqual(C.character_profile(p)['amotion'], 90)

    def test_long_cast_postdelay_and_map_boundary(self):
        events = [ev(0, E.SKILL_CAST_START, PLAYER, skill=7, cast_ms=1500),
                  ev(1510, E.BUFF_START, PLAYER, efst=46, remain_ms=200),
                  ev(2000, E.SKILL_CAST_START, PLAYER, skill=8, cast_ms=0),
                  ev(2010, E.MAP_CHANGE, PLAYER),
                  ev(2020, E.BUFF_START, PLAYER, efst=46, remain_ms=999)]
        self.assertEqual(C.postdelays(events, PLAYER), {7: [200]})

    def test_unknown_cast_is_not_zero(self):
        self.assertEqual(C.cast_times([ev(0,E.SKILL_CAST_START,PLAYER,skill=7)],PLAYER), {})
        self.assertEqual(C.cast_times([ev(0,E.SKILL_CAST_START,PLAYER,skill=7,cast_ms=0)],PLAYER), {7:0})

    def test_no_invented_rotation(self):
        cs=[(0,9),(100,2),(3000,9),(3100,2)]
        seqs=[s['skills'] for s in C.suggest(cs)]
        self.assertIn([9,2],seqs)
        self.assertNotIn([2,9],seqs)

    def test_measured_floor_and_known_zero(self):
        p=dict(postdelay={1:0},motion_ms=None,cast={1:0},cooldown={})
        self.assertEqual(C.plan_auto([1],{},p)[0]['wait'],50)
        self.assertEqual(C.plan_auto([1],{(1,1):[200]*5},p)[0]['wait'],210)
        with self.assertRaises(ValueError): C.plan_auto([1],{},p,-10)

    def test_conservative_delay_and_cooldown(self):
        p=parsed([ev(0,E.SKILL_CAST_START,PLAYER,skill=7,cast_ms=0),
                  ev(1,E.BUFF_START,PLAYER,efst=46,remain_ms=100),
                  ev(1000,E.SKILL_CAST_START,PLAYER,skill=7,cast_ms=0),
                  ev(1001,E.BUFF_START,PLAYER,efst=46,remain_ms=200)],
                 [(0,struct.pack('<HHI',C.SKILL_COOLDOWN,7,500)),
                  (1000,struct.pack('<HHI',C.SKILL_COOLDOWN,7,1000))])
        profile=C.character_profile(p)
        self.assertEqual((profile['postdelay'][7],profile['cooldown'][7]),(200,1000))
        self.assertGreaterEqual(C.plan_auto([7],profile['stats'],profile)[0]['wait'],1010)


class RegistrationTest(unittest.TestCase):
    def macro(self):
        return C.make_macro(parsed(),[dict(skill=7,wait=200)],{7:'E'},'combo','E')

    def test_idempotent_and_identity_separation(self):
        m=self.macro()
        ms,i=C.register_macro([],m)
        ms,i=C.register_macro(ms,m)
        self.assertEqual((len(ms),i),(1,0))
        p=parsed();p.server='other'
        other=C.make_macro(p,[dict(skill=7,wait=200)],{7:'E'},'combo','E')
        ms,i=C.register_macro(ms,other)
        self.assertEqual(len(ms),2)
        self.assertFalse(ms[i]['enabled'])
        self.assertEqual(M.validate(ms),[])

    def test_legacy_duplicate_preserved_disabled(self):
        m=self.macro()
        old={k:v for k,v in m.items() if k!='replay_combo'}
        ms,i=C.register_macro([old,dict(old,name='c o m b o')],m)
        self.assertEqual(len(ms),2)
        self.assertTrue(ms[0]['enabled'])
        self.assertFalse(ms[1]['enabled'])
        self.assertEqual(M.validate(ms),[])

    def test_missing_or_too_long_not_silently_replaced(self):
        for wait in (None,60001):
            with self.assertRaises(ValueError):
                C.make_macro(parsed(),[dict(skill=7,wait=wait)],{7:'E'},'combo','E')

    def test_invalid_wait_returns_problem(self):
        for wait in (None,'bad',float('inf')):
            self.assertTrue(M.validate([dict(M.new_macro(),steps=[dict(type='wait',ms=wait)])]))


class EngineCancellationTest(unittest.TestCase):
    def test_release_focus_and_stop_interrupt_wait(self):
        for reason in ('release','focus','stop','modifier'):
            with self.subTest(reason=reason):
                first=threading.Event(); sent=[];fg=[True]
                def key(vk,up):
                    sent.append((vk,up))
                    if up: first.set()
                def runner(steps,stop): M.run_steps(steps,stop,key=key)
                macro=dict(M.new_macro(),mod='ctrl' if reason=='modifier' else '',steps=[
                    dict(type='tap',key='E'),dict(type='wait',ms=5000),dict(type='tap',key='R')])
                e=M.MacroEngine([macro],is_game_foreground=lambda:fg[0],runner=runner)
                if reason=='modifier': e.on_key('Ctrl',True)
                e.on_key('W',True)
                self.assertTrue(first.wait(1))
                if reason=='release': e.on_key('W',False)
                elif reason=='focus': fg[0]=False
                elif reason=='modifier': e.on_key('Ctrl',False)
                else: e.stop()
                for thread in list(e.running.values()): thread.join(1)
                self.assertFalse(e.running)
                self.assertEqual(sent,[(M.KEYS['E'],False),(M.KEYS['E'],True)])
                e.stop()

    def test_input_error_visible(self):
        status=[]
        def fail(*args): raise OSError('insertion failed')
        e=M.MacroEngine([M.new_macro()],game_only=False,on_status=status.append,runner=fail)
        e.on_key('W',True)
        for thread in list(e.running.values()):thread.join(1)
        self.assertIn('insertion failed',e.error)
        self.assertTrue(status[-1].startswith('매크로 실행 실패:'))

    def test_sendinput_zero_detected_without_real_input(self):
        import ctypes
        with patch.object(ctypes.windll.user32,'SendInput',return_value=0) as send:
            with self.assertRaises(OSError):M._key(M.KEYS['E'],False)
            self.assertEqual(send.call_count,1)


class RealReplayUiTest(unittest.TestCase):
    def test_replay_profile_registration_reload(self):
        path=sample('111.rrf')
        if not path:self.skipTest('local replay unavailable')
        from rrf import Replay
        from analyzer.normalizer import normalize
        from config.settings import Settings
        from PySide6.QtWidgets import QApplication, QMessageBox
        from ui.pages.macro_page import MacroPage
        from ui.combo_dialog import ComboDialog
        qt=QApplication.instance() or QApplication([])
        pr=normalize(Replay(path),path,experimental=True)
        names={5245:'제1격 : 낙인',5248:'제2격 : 멸마지화',5252:'제3격 : 멸화격'}
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(os.path.join(tmp,'settings.json'))
            settings['macro_app_enabled']=False
            settings['macros']=[]
            settings['macro_skill_keys']={pr.character:{'5245':'E','5248':'R','5252':'T'}}
            app=SimpleNamespace(s=settings,replay_page=SimpleNamespace(result=SimpleNamespace(parsed=pr,path=path)),
                analysis=SimpleNamespace(gamedata=SimpleNamespace(skills=SimpleNamespace(name=lambda x:names.get(x,str(x))))))
            page=MacroPage(app)
            before=copy.deepcopy(settings['macro_skill_keys'])
            with patch.object(QMessageBox,'information'),patch.object(QMessageBox,'warning') as warning:
                for _ in range(2):
                    dialog=ComboDialog(app,page)
                    index=next(i for i,s in enumerate(dialog.suggestions) if s['skills']==[5245,5248,5252])
                    dialog.cands.setCurrentRow(index)
                    self.assertTrue(dialog.keys_confirmed.isChecked())
                    if _==0:self.assertEqual(settings['macro_skill_keys'],before)
                    dialog._add()
                    dialog.close()
                warning.assert_not_called()
            loaded=Settings(settings.path)
            self.assertEqual(len(loaded['macros']),1)
            m=loaded['macros'][0]
            self.assertEqual([s['key'] for s in m['steps'] if s['type']=='tap'],['E','R','T'])
            self.assertEqual(m['replay_combo']['identity'][:3],[pr.server,pr.character,pr.job_id])
            self.assertEqual(M.validate(loaded['macros']),[])
            self.assertIsNone(page.engine)
            page.shutdown();page.close()
