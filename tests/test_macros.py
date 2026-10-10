import struct
import threading
import time
import unittest

from helpers import PLAYER, ROOT, ev  # noqa: F401  (sys.path 설정)

from analyzer.events import EventType as E
from core import combo as C
from core import macros as M


class ValidateTest(unittest.TestCase):
    def test_problems(self):
        ms = [dict(M.new_macro('A'), key='W'), dict(M.new_macro('B'), key='W'),
              dict(M.new_macro('C'), key='E', steps=[]), dict(M.new_macro('D'), key='R', steps=[dict(type='tap', key='??')]),
              dict(M.new_macro('E'), key='W', enabled=False), dict(M.new_macro('F'), key='W', mod='alt')]
        p = M.validate(ms)
        self.assertTrue(any(x.startswith('B') and '같은 트리거' in x for x in p))
        self.assertTrue(any(x.startswith('C') and '비어' in x for x in p))
        self.assertTrue(any(x.startswith('D') and '알 수 없는 키' in x for x in p))
        self.assertFalse(any(x.startswith(('E', 'F')) for x in p))

    def test_migrate_old_format(self):
        old = dict(name='옛', engine='app', hotkey='Ctrl+Alt+W', mode='hold', gap=100, steps=[dict(type='tap', key='F1')])
        m = M.migrate(old)
        self.assertEqual((m['key'], m['mod'], m['mode']), ('W', 'ctrl', 'hold'))
        self.assertNotIn('engine', m)
        self.assertEqual(M.migrate(dict(name='g', engine='ghub', button=4, steps=[]))['key'], 'F12')
        self.assertIs(M.migrate(m), m)

    def test_match_modifiers(self):
        ms = [dict(M.new_macro('plain'), key='W'), dict(M.new_macro('alt'), key='W', mod='alt')]
        self.assertEqual(M.match(ms, 'W', set())['name'], 'plain')
        self.assertEqual(M.match(ms, 'W', {'alt'})['name'], 'alt')
        self.assertIsNone(M.match(ms, 'W', {'ctrl'}))
        self.assertIsNone(M.match(ms, 'Q', set()))


class RunStepsTest(unittest.TestCase):
    def test_order_and_held_keys_released(self):
        log = []
        M.run_steps([dict(type='down', key='Shift'), dict(type='tap', key='F1'), dict(type='wait', ms=40),
                     dict(type='click'), dict(type='rclick')],
                    key=lambda vk, up: log.append(('up' if up else 'down', vk)),
                    click=lambda right: log.append(('click', right)), sleep=lambda s: log.append(('sleep', s)))
        self.assertEqual(log, [('down', 0xA0), ('down', 0x70), ('up', 0x70), ('sleep', 0.04),
                               ('click', False), ('click', True), ('up', 0xA0)])

    def test_stop_event(self):
        log, stop = [], threading.Event()
        stop.set()
        M.run_steps([dict(type='tap', key='A')], stop, key=lambda vk, up: log.append(vk))
        self.assertEqual(log, [])


class EngineTest(unittest.TestCase):
    """훅 없이 on_key 만 호출해 트리거 판단을 확인 (키 입력은 보내지 않음)."""
    def make(self, macros, fg=True):
        runs = []

        def runner(steps, stop):
            runs.append(steps)
            time.sleep(0.01)
        return M.MacroEngine(macros, game_only=True, is_game_foreground=lambda: fg, runner=runner), runs

    def wait_idle(self, e):
        for _ in range(200):
            if not e.running:
                return
            time.sleep(0.005)

    def test_hold_repeats_until_release(self):
        e, runs = self.make([dict(M.new_macro('W 난타'), key='W', mode='hold', gap=30)])
        e.on_key('W', True)
        time.sleep(0.2)
        e.on_key('W', True)                  # 누르고 있어 생기는 자동 반복은 새로 시작하지 않는다
        e.on_key('W', False)
        self.wait_idle(e)
        n = len(runs)
        self.assertGreaterEqual(n, 3)
        time.sleep(0.1)
        self.assertEqual(len(runs), n)       # 떼면 멈춤

    def test_once_and_foreground_and_modifier(self):
        e, runs = self.make([dict(M.new_macro('한 번'), key='Q', mode='once')])
        e.on_key('Q', True)
        e.on_key('Q', False)
        self.wait_idle(e)
        self.assertEqual(len(runs), 1)
        e.on_key('Ctrl', True)
        e.on_key('Q', True)                  # Ctrl+Q 는 조합 없는 매크로가 아님
        e.on_key('Q', False)
        self.wait_idle(e)
        self.assertEqual(len(runs), 1)
        e2, runs2 = self.make([dict(M.new_macro('bg'), key='Q', mode='once')], fg=False)
        e2.on_key('Q', True)
        self.wait_idle(e2)
        self.assertEqual(runs2, [])          # 게임 창이 앞이 아니면 실행 안 함


class ComboTest(unittest.TestCase):
    def test_hotkey_bar_and_keys(self):
        ents = [(0, 0, 0)] * 38
        ents[0] = (1, 29, 10)                # 1줄 1칸 스킬
        ents[3] = (0, 12622, 0)              # 아이템
        ents[10] = (1, 2327, 5)              # 2줄 2칸 = W
        body = b''.join(struct.pack('<BIH', *x) for x in ents)
        pkt = struct.pack('<HBH', 0x0B20, 0, 0) + body
        other_tab = struct.pack('<HBH', 0x0B20, 0, 1) + b'\x00' * 266
        bar = C.hotkey_bar([(0, pkt), (5, other_tab)])
        self.assertEqual(set(bar), {0, 3, 10})
        self.assertEqual(C.skill_keys(bar), {29: ('F1', 0), 2327: ('W', 10)})

    def test_casts_intervals_suggest_plan(self):
        evs = []
        t = 0
        for _ in range(6):                   # 궁신탄영(264) → 천라지망(2327) ×3, 2초 쉬고 반복
            for sid, gap in ((264, 160), (2327, 150), (2327, 150), (2327, 2000)):
                evs.append(ev(t, E.SKILL_CAST_START, PLAYER, skill=sid))
                evs.append(ev(t + 5, E.SKILL_CAST_START, PLAYER, skill=sid))   # 중복 기록은 합친다
                t += gap
        evs.append(ev(t, E.SKILL_CAST_START, 999, skill=1))                  # 다른 사람 스킬 무시
        cs = C.casts(evs, PLAYER)
        self.assertEqual(len(cs), 24)
        st = C.intervals(cs)
        self.assertEqual(st[(264, 2327)], [160] * 6)
        self.assertNotIn((2327, 264), st)    # 2초 쉰 뒤는 콤보가 끊긴 것
        best = C.suggest(cs)[0]
        self.assertEqual(sorted(best['skills']), [264, 2327, 2327, 2327])
        rows = C.plan([264, 2327, 2327], st, q=0.2, extra_ms=10)
        self.assertEqual([r['wait'] for r in rows], [170, 160, None])       # 마지막(천라→궁신) 측정 없음
        steps = C.build_steps(rows, {264: 'Q', 2327: 'W'}, fallback_ms=300)
        self.assertEqual(steps[0], dict(type='tap', key='Q'))
        self.assertEqual(steps[-1], dict(type='wait', ms=300))


def _kb(hid, down=False):
    return {'keyboard': dict(displayName='?', hidUsage=str(hid), **({'isDown': True} if down else {}))}


def _ms(btn, down=False):
    return {'mouse': {'button': dict(hidUsage=str(btn), **({'isDown': True} if down else {}))}}


def _dl(ms):
    return {'delay': {'durationMs': ms}}


class GhubImportTest(unittest.TestCase):
    SETTINGS = {
        'applications': {'applications': [{'applicationId': 'desk', 'name': 'APPLICATION_NAME_DESKTOP'}]},
        'cards': {'cards': [
            {'applicationId': 'desk', 'name': 'ZZ', 'macro': {'type': 'SEQUENCE', 'sequence': {'heldSequence': {
                'components': [_kb(20, True), _dl(25), _kb(20), _dl(25), _ms(1, True), _dl(25), _ms(1), _dl(25),
                               _kb(225, True), _kb(26, True), _dl(25), _kb(26), _kb(225), _ms(3, True)]}}}},
            {'name': '파리', 'macro': {'type': 'SEQUENCE', 'sequence': {'simpleSequence': {
                'components': [_kb(44, True), _dl(25), _kb(44)]}}}},
            {'name': '토글', 'macro': {'type': 'SEQUENCE', 'sequence': {'toggleSequence': {'components': [_kb(4, True)]}}}},
            {'name': '기본', 'readOnly': True, 'macro': {'type': 'SEQUENCE', 'sequence': {}}},
            {'name': '단축', 'macro': {'type': 'KEYSTROKE', 'keystroke': {'code': 8}}},
        ]}}

    def test_convert_and_filter(self):
        from core import ghub_import as G
        got = {g['name']: g for g in G.ghub_macros(self.SETTINGS)}
        self.assertEqual(set(got), {'ZZ', '파리', '토글'})           # 읽기 전용·단일 키 카드는 제외
        zz = got['ZZ']
        self.assertEqual((zz['mode'], zz['profile']), ('hold', '데스크톱'))
        self.assertEqual(zz['steps'], [dict(type='tap', key='Q'), dict(type='wait', ms=50), dict(type='click'),
                                       dict(type='wait', ms=50), dict(type='down', key='Shift'),
                                       dict(type='tap', key='W'), dict(type='wait', ms=25),
                                       dict(type='up', key='Shift')])
        self.assertEqual(zz['skipped'], ['마우스 버튼 3'])
        self.assertEqual(got['파리']['mode'], 'once')
        self.assertFalse(got['토글']['importable'])
        m = G.to_macro(zz)
        self.assertFalse(m['enabled'])
        self.assertEqual(M.validate([dict(m, enabled=True)]), [])

    def test_read_settings_from_wal_db(self):
        import json
        import os
        import sqlite3
        import tempfile
        from core import ghub_import as G
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'settings.db')
            conn = sqlite3.connect(path)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE data (_id INTEGER PRIMARY KEY, file BLOB)')
            conn.execute('INSERT INTO data (file) VALUES (?)', (json.dumps({'old': 1}),))
            conn.execute('INSERT INTO data (file) VALUES (?)', (json.dumps(self.SETTINGS),))
            conn.commit()                                        # 연결을 열어 둔 채(G HUB 실행 중처럼) 읽기
            self.assertEqual(len(G.ghub_macros(G.read_settings(path))), 3)
            conn.close()
        with self.assertRaises(FileNotFoundError):
            G.read_settings(os.path.join(d, 'none.db'))


class AutoPlanTest(unittest.TestCase):
    def test_profile_and_auto_plan(self):
        evs, t = [], 0
        for _ in range(10):
            for sid in (264, 2327, 2327):
                evs.append(ev(t, E.SKILL_CAST_START, PLAYER, skill=sid))
                if sid == 2327:                                   # 천라지망만 서버 후딜레이 90ms (음수는 0)
                    evs.append(ev(t + 40, E.BUFF_START, PLAYER, efst=C.EFST_POSTDELAY, remain_ms=90))
                t += 150
            t += 3000
        evs.append(ev(t, E.BUFF_START, PLAYER, efst=C.EFST_POSTDELAY, remain_ms=-10))   # 짝 없는 후딜레이는 무시
        par = lambda ms, v: (ms, struct.pack('<HHi', C.PAR_CHANGE, C.VAR_ASPD, v))  # noqa: E731

        class P:
            events, player_aid = evs, PLAYER
            packets = [par(0, 140), par(10, 70), (20, struct.pack('<HHi', C.PAR_CHANGE, 5, 999))]
        prof = C.character_profile(P)
        self.assertEqual((prof['amotion'], prof['aspd'], prof['motion_ms']), (70, 193.0, 140))
        self.assertEqual(prof['postdelay'], {2327: 90})
        rows = C.plan_auto([264, 2327, 2327], prof['stats'], prof, margin_ms=10)
        self.assertEqual([r['wait'] for r in rows], [163, 160, 150])  # 스킬별 모션과 관측 간격 모두 반영
        self.assertEqual([r['basis'] for r in rows], ['공격 모션 추정', '실측 하위 20%', '공격 모션 추정'])
        slow = dict(prof, postdelay={2327: 300})
        self.assertEqual(C.plan_auto([2327, 264], prof['stats'], slow, margin_ms=0)[0]['wait'], 300)
        none = dict(prof, postdelay={}, motion_ms=None, motion_by_skill={})
        r = C.plan_auto([264, 2327], prof['stats'], none, margin_ms=0)
        self.assertEqual((r[0]['basis'], r[0]['wait']), ('실측', 150))
        self.assertEqual((r[1]['basis'], r[1]['wait']), ('자료 없음', None))   # 2327→264 는 콤보가 끊겨 실측 없음


class CooldownPlanTest(unittest.TestCase):
    """연격처럼 스킬별 쿨타임이 있는 콤보(모든 직업 공통 계산)."""
    PROF = dict(postdelay={3: 126}, motion_ms=140, cooldown={1: 300, 2: 700, 3: 1000}, cast={})

    def test_pad_at_loop_end_keeps_chain_fast(self):
        rows = C.plan_auto([1, 2, 3], {}, self.PROF, margin_ms=10)
        self.assertEqual([r['wait'] for r in rows], [150, 150, 710])       # 한 바퀴 1010 = 멸화격 쿨타임 1000 + 10
        self.assertEqual(rows[2]['cooldown_for'], [2, 3])                 # 멸마지화 700·멸화격 1000 둘 다 여기서 맞춤
        self.assertIn('쿨타임', rows[2]['basis'])

    def test_cast_time_and_repeat_inside_loop(self):
        prof = dict(self.PROF, cast={1: 500})
        self.assertEqual(C.plan_auto([1, 2, 3], {}, prof, margin_ms=10)[0]['wait'], 650)   # 캐스팅 500 + 140 + 10
        rows = C.plan_auto([7, 7], {}, dict(self.PROF, cooldown={7: 400}), margin_ms=10)
        self.assertEqual([r['wait'] for r in rows], [410, 410])           # 같은 스킬 두 번: 둘 다 쿨타임 이상

    def test_cooldown_and_cast_from_replay(self):
        pkts = [(0, struct.pack('<HHI', C.SKILL_COOLDOWN, 5252, 1000)), (5, struct.pack('<HHI', C.SKILL_COOLDOWN, 5252, 1000)),
                (9, struct.pack('<HHI', C.SKILL_COOLDOWN, 5245, 300))]
        self.assertEqual(C.cooldowns(pkts), {5252: 1000, 5245: 300})
        evs = [ev(0, E.SKILL_CAST_START, PLAYER, skill=9, cast_ms=500), ev(10, E.SKILL_CAST_START, PLAYER, skill=9, cast_ms=700),
               ev(20, E.SKILL_CAST_START, PLAYER, skill=8, cast_ms=0), ev(30, E.SKILL_CAST_START, 99, skill=9, cast_ms=9000)]
        self.assertEqual(C.cast_times(evs, PLAYER), {9: 600, 8: 0})


class MergeKeysAndAdminTest(unittest.TestCase):
    def test_merge_keys_int_ids(self):
        ents = [(0, 0, 0)] * 38
        ents[10] = (1, 2327, 5)
        bar = C.hotkey_bar([(0, struct.pack('<HBH', 0x0B20, 0, 0) + b''.join(struct.pack('<BIH', *x) for x in ents))])
        self.assertEqual(C.merge_keys({264: 'Q', 2327: 'E'}, bar), {264: 'Q', 2327: 'E'})   # 저장한 실제 키 우선

    def test_is_elevated_current_process(self):
        self.assertIn(M.is_elevated(), (True, False))
        self.assertIsNone(M.is_elevated(4000000000))       # 없는 프로세스


class KeyCaptureTest(unittest.TestCase):
    def test_click_then_press(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from ui.key_capture import KeyCapture
        got = []
        w = KeyCapture('F1')
        w.captured.connect(lambda k, m: got.append((k, m)))
        w.show()
        for key, mods, want in ((Qt.Key_W, Qt.NoModifier, ('W', '')), (Qt.Key_Q, Qt.ControlModifier, ('Q', 'ctrl')),
                                (Qt.Key_Tab, Qt.NoModifier, ('Tab', ''))):
            w.click()
            QTest.keyClick(w, key, mods)
            self.assertEqual(got[-1], want)
        self.assertEqual(w.currentText(), 'Tab')
        w.click()
        QTest.keyClick(w, Qt.Key_Escape)                      # Esc = 취소, 키는 그대로
        self.assertEqual((w.currentText(), len(got)), ('Tab', 3))
        w.close()


class RecordTest(unittest.TestCase):
    def test_steps_from_events(self):
        from ui.pages.macro_page import steps_from_events
        evs = [(0.0, 'F1', True), (0.03, 'F1', False), (0.33, 'Shift', True), (0.4, 'Q', True), (0.45, 'Q', False),
               (0.5, 'Shift', False)]
        self.assertEqual(steps_from_events(evs), [
            dict(type='tap', key='F1'), dict(type='wait', ms=300), dict(type='down', key='Shift'),
            dict(type='wait', ms=70), dict(type='tap', key='Q'), dict(type='wait', ms=50), dict(type='up', key='Shift')])


if __name__ == '__main__':
    unittest.main()
