"""버프 루틴 창: 리플레이 → 순서 → (파티·자신 버프만) 매크로. 게임에 입력을 보내지 않는다."""
import os
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from helpers import PLAYER, ev

from analyzer.events import EventType as E
from core import buff_routine as BR
from core import combo as C
from core import macros as M

BLESS, AGI, HEAL = 34, 29, 28
ALLY = 3001


def hotkey_packet(slots):
    """{칸: 스킬 id} → 리플레이 단축키 창 패킷(0x0B20)."""
    b = bytearray(C.HOTKEY_LEN)
    struct.pack_into('<HBH', b, 0, C.HOTKEY_PACKET, 0, 0)
    for slot in range(C.HOTKEY_SLOTS):
        sid = slots.get(slot, 0)
        struct.pack_into('<BIH', b, 5 + 7 * slot, 1 if sid else 0, sid, 1 if sid else 0)
    return bytes(b)


def parsed():
    """나에게 블레싱, 파티원에게 민첩성 증가 — 둘 다 직후에 상태가 걸린다. 힐은 상태가 안 걸린다."""
    events = []
    for t in range(0, 5000, 1000):
        events += [ev(t, E.SKILL_USE, PLAYER, skill=BLESS), ev(t + 100, E.BUFF_START, PLAYER, efst=10, max_ms=240000, remain_ms=240000)]
        events += [ev(t + 300, E.SKILL_USE, PLAYER, ALLY, skill=AGI), ev(t + 380, E.BUFF_START, ALLY, efst=11, max_ms=60000, remain_ms=60000)]
        events.append(ev(t + 600, E.SKILL_USE, PLAYER, ALLY, skill=HEAL))
    return SimpleNamespace(events=events, packets=[(0, hotkey_packet({0: BLESS, 10: AGI}))],
                           player_aid=PLAYER, actors={ALLY: dict(obj_type=0, name='홍길동')},
                           server='바포메트', character='버퍼', job_id=4262, base_level=200, job_level=50)


class BuffRoutineDialogTest(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication
        from config.settings import Settings
        from ui.pages.macro_page import MacroPage
        self.qt = QApplication.instance() or QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        s = Settings(os.path.join(self.tmp.name, 'settings.json'))
        s['macro_app_enabled'] = False
        s['macros'] = []
        names = {BLESS: '블레싱', AGI: '민첩성 증가', HEAL: '힐'}
        self.app = SimpleNamespace(
            s=s, status=lambda *a, **k: None,
            replay_page=SimpleNamespace(result=SimpleNamespace(parsed=parsed(), path='x.rrf')),
            analysis=SimpleNamespace(gamedata=SimpleNamespace(skills=SimpleNamespace(
                name=lambda sid: names.get(sid, '스킬 #%s' % sid)))))
        self.page = MacroPage(self.app)
        self.addCleanup(self.page.shutdown)

    def dialog(self):
        from ui.buff_routine_dialog import BuffRoutineDialog
        d = BuffRoutineDialog(self.app, self.page)
        self.addCleanup(d.close)
        return d

    def test_replay_fills_steps_members_and_hotkeys_from_skill_bar(self):
        d = self.dialog()
        self.assertEqual(d.member_names(), ['홍길동'])
        self.assertEqual([(s.name, s.scope, s.target, s.hotkey) for s in d.routine.steps],
                         [('블레싱', BR.PARTY, '', 'F1'), ('민첩성 증가', BR.TARGET, '홍길동', 'W')])
        self.assertNotIn('힐', [s.name for s in d.routine.steps])      # 상태가 안 걸린 스킬은 넣지 않는다
        self.assertEqual([r.get('remain_ms') for r in d.found.model_.rows], [240000, 60000])

    def test_checklist_walks_steps_and_remembers_position(self):
        d = self.dialog()
        d.start()
        self.assertIn('블레싱', d.current.text())
        self.assertTrue(d.progress.text().startswith('1 / 2'))
        d.step(1)
        self.assertIn('민첩성 증가 → 홍길동', d.current.text())
        d.skip_member()                                                 # 홍길동 몫을 건너뛰면 끝
        self.assertIn('다 돌았습니다', d.current.text())

    def test_saved_routine_is_reloaded(self):
        from config.settings import Settings
        d = self.dialog()
        d.save()
        self.assertEqual([s.label for s in BR.Routine.from_dict(Settings(self.app.s.path)['buff_routine']).steps],
                         [s.label for s in d.routine.steps])

    def test_macro_export_only_sends_party_buffs(self):
        from PySide6.QtWidgets import QMessageBox
        d = self.dialog()
        d.trigger.setCurrentText('F12')
        with patch.object(QMessageBox, 'information'):
            d.export_macro()
        self.assertEqual(len(self.page.macros), 1)
        m = self.page.macros[0]
        self.assertEqual([s['key'] for s in m['steps'] if s['type'] == 'tap'], ['F1'])   # 대상 지정 버프 제외
        self.assertEqual((m['key'], m['mode']), ('F12', 'once'))
        self.assertEqual(M.validate(self.page.macros), [])

    def test_export_without_hotkeys_warns_instead_of_making_an_empty_macro(self):
        from PySide6.QtWidgets import QMessageBox
        d = self.dialog()
        for s in d.routine.steps:
            s.hotkey = ''
        with patch.object(QMessageBox, 'information') as info:
            d.export_macro()
        self.assertEqual(self.page.macros, [])
        self.assertIn('단축키 지정', info.call_args[0][2])

    def test_no_replay_tells_the_user_instead_of_crashing(self):
        from PySide6.QtWidgets import QMessageBox
        self.app.replay_page = SimpleNamespace(result=None)
        with patch.object(QMessageBox, 'information'):
            d = self.dialog()
        self.assertIn('리플레이', d.who.text())
        self.assertEqual(d.routine.steps, [])


if __name__ == '__main__':
    unittest.main()
