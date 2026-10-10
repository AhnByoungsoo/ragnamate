"""Passive Live Mode 테스트 (게임 없이 mock frame / 합성 이벤트)."""
import os
import tempfile
import time
import unittest

import numpy as np

from helpers import ROOT

from core.models import PriceInfo
from database.database import Database
from live.chat import AliasDB, ChatMonitor, ChatParser, ItemMatcher, PatternDB
from live.events import (GE, HIGH, LOW, MEDIUM, REPLAY, SCREEN_OCR, SCREEN_VISION, EventBus, EventDeduplicator,
                         GameEvent)
from live.ocr import OcrLine, normalize_number, normalize_text
from live.profile import ProfileStore, Region, ScreenProfile
from live.session import HYBRID, SCREEN, SessionManager, accuracy
from live.vision import bar_fill_ratio, changed, suggest_bars

FIX = os.path.join(ROOT, 'tests', 'fixtures', 'screens')
SETTINGS = dict(live_start_window=60, live_start_min=2, live_pause_sec=120, high_value_threshold=5_000_000)


class FakeItems:
    items = {909: dict(name='젤로피'), 4001: dict(name='포링 카드'), 22687: dict(name='사념의 파편'),
             501: dict(name='빨간포션'), 502: dict(name='빨간포션')}


def prices(ids):
    table = {909: 10, 4001: 6_000_000, 22687: 83_000}
    return {i: PriceInfo(i, table[i], table[i], table[i], 1, 0) for i in ids if i in table}


def ev(t, et, src=SCREEN_OCR, **kw):
    return GameEvent(et, src, kw.pop('confidence', MEDIUM), t, **kw)


class DedupTest(unittest.TestCase):
    def test_same_message_hash_once(self):
        d = EventDeduplicator()
        a = ev(0, GE.ITEM_GAIN, item_id=909, quantity=1, metadata=dict(message_hash='x'))
        b = ev(1, GE.ITEM_GAIN, item_id=909, quantity=1, metadata=dict(message_hash='x'))
        self.assertTrue(d.offer(a)[0])
        self.assertFalse(d.offer(b)[0])

    def test_replay_supersedes_screen(self):
        d = EventDeduplicator(window_sec=5)
        scr = ev(10, GE.ITEM_GAIN, item_id=909, quantity=2)
        rep = ev(12, GE.ITEM_GAIN, REPLAY, item_id=909, quantity=2)
        self.assertEqual(d.offer(scr), (True, []))
        ok, sup = d.offer(rep)
        self.assertTrue(ok)
        self.assertEqual(sup, [scr])
        self.assertFalse(d.offer(ev(13, GE.ITEM_GAIN, item_id=909, quantity=2))[0])   # 이후 화면 중복은 버림
        self.assertTrue(d.offer(ev(30, GE.ITEM_GAIN, item_id=909, quantity=2))[0])    # 시간창 밖은 새 사건

    def test_per_type_priority(self):
        d = EventDeduplicator(per_type_priority={GE.ZENY_CHANGE: [SCREEN_OCR, REPLAY]})
        self.assertTrue(d.offer(ev(0, GE.ZENY_CHANGE, value=100))[0])
        self.assertFalse(d.offer(ev(1, GE.ZENY_CHANGE, REPLAY, value=100))[0])


class TextTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_number('1O,2l5'), 10215)
        self.assertIsNone(normalize_number('abc'))
        self.assertEqual(normalize_text('  사념의   파편 '), '사념의 파편')


class ChatTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.patterns = PatternDB(os.path.join(d, 'p.json'))
        self.alias = AliasDB(os.path.join(d, 'a.json'))
        self.matcher = ItemMatcher(FakeItems, self.alias)
        self.parser = ChatParser(self.patterns, self.matcher)

    def test_no_pattern_no_event(self):
        self.assertIsNone(self.parser.parse(1, OcrLine('아무 말', 1.0, (0, 0, 1, 1))))

    def test_learn_and_parse(self):
        self.patterns.learn('테스트 문구: 젤로피 3개 획득', GE.ITEM_GAIN, '젤로피', '3')
        e = self.parser.parse(1, OcrLine('테스트 문구: 사념의 파편 12개 획득', 1.0, (0, 0, 1, 1)))
        self.assertEqual((e.event_type, e.item_id, e.quantity, e.confidence), (GE.ITEM_GAIN, 22687, 12, MEDIUM))
        e = self.parser.parse(2, OcrLine('테스트 문구:  젤로피  1개 획득', 1.0, (0, 0, 1, 1)))     # 띄어쓰기 흔들림
        self.assertEqual(e.item_id, 909)

    def test_alias_fuzzy_ambiguous(self):
        self.patterns.learn('획득 젤로피 x1', GE.ITEM_GAIN, '젤로피', '1')
        e = self.parser.parse(1, OcrLine('획득 사념외 파편 x1', 1.0, (0, 0, 1, 1)))
        self.assertEqual((e.item_id, e.confidence, e.metadata['match']), (22687, LOW, 'fuzzy'))
        self.alias.add('사념외 파편', '사념의 파편')
        e = self.parser.parse(2, OcrLine('획득 사념외 파편 x1', 1.0, (0, 0, 1, 1)))
        self.assertEqual((e.item_id, e.metadata['match']), (22687, 'alias'))
        e = self.parser.parse(3, OcrLine('획득 빨간포션 x1', 1.0, (0, 0, 1, 1)))
        self.assertEqual((e.item_id, e.confidence, e.metadata['match']), (None, LOW, 'ambiguous'))

    def test_monitor_only_new_lines(self):
        class FakeOCR:
            def __init__(self):
                self.lines = []

            def recognize(self, img, scale=1.0):
                return [OcrLine(t, 1.0, (0, i * 10, 50, 8)) for i, t in enumerate(self.lines)]
        o = FakeOCR()
        m = ChatMonitor(o)
        img = np.zeros((40, 100, 3), np.uint8)
        o.lines = ['a', 'b', 'c']
        self.assertEqual([l.text for _, l in m.update(img)], ['a', 'b', 'c'])
        self.assertEqual(m.update(img), [])                          # 화면 변화 없음 → OCR 안 함
        o.lines = ['b', 'c', 'd']
        img2 = img.copy()
        img2[0, 0] = 255
        img2[5:10] = 200
        self.assertEqual([l.text for _, l in m.update(img2)], ['d'])
        o.lines = ['c', 'd', 'd']                                    # 같은 문구가 새 줄로 또 옴 → 새 사건
        img3 = img2.copy()
        img3[20:30] = 90
        self.assertEqual([l.text for _, l in m.update(img3)], ['d'])


class SessionTest(unittest.TestCase):
    def mgr(self):
        self.alerts = []
        return SessionManager(prices, SETTINGS, alert=lambda k, m: self.alerts.append(m))

    def test_auto_start_pause_resume_profit(self):
        m = self.mgr()
        m.on_event(ev(0, GE.MAP_CHANGED, metadata=dict(map_name='필드')))
        m.on_event(ev(1, GE.ITEM_GAIN, item_id=909, item_name='젤로피', quantity=5))
        self.assertIsNone(m.session)                                 # 1회로는 시작 안 함
        m.on_event(ev(10, GE.ITEM_GAIN, item_id=4001, item_name='포링 카드', quantity=1))
        s = m.session
        self.assertIsNotNone(s)
        self.assertEqual(s.start_time, 1)
        self.assertEqual(s.item_value(), 6_000_050)                  # 시작 판정에 쓰인 첫 드랍도 포함
        self.assertTrue(any('HIGH VALUE' in a for a in self.alerts))
        m.tick(10 + 121)
        self.assertEqual(s.state, 'PAUSED')
        m.on_event(ev(200, GE.ITEM_GAIN, item_id=909, item_name='젤로피', quantity=10))
        self.assertEqual(s.state, 'ACTIVE')
        self.assertAlmostEqual(s.paused_time, 200 - 130)
        self.assertEqual(s.item_value(), 6_000_150)

    def test_low_confidence_excluded(self):
        m = self.mgr()
        m.start(0)
        m.on_event(ev(1, GE.ITEM_GAIN, item_id=22687, item_name='사념의 파편', quantity=1, confidence=LOW,
                      metadata=dict(match='fuzzy')))
        self.assertEqual((m.session.item_value(), len(m.session.review)), (0, 1))

    def test_exp_percent_with_levelup(self):
        m = self.mgr()
        m.start(0)
        for t, v in ((0, 90.0), (1800, 98.0), (1900, 2.0), (3600, 10.0)):
            m.on_event(ev(t, GE.EXP_CHANGE, SCREEN_OCR, value=v))
        m.session.last_activity = 3600
        m.session.end_time = 3600
        self.assertAlmostEqual(m.session.exp_pct_per_hour(), 20.0)

    def test_hybrid_upgrade_and_downgrade(self):
        m = self.mgr()
        m.start(0)
        self.assertEqual(m.session.mode, SCREEN)
        m.set_replay(True, dict(kills=10, dps=1000), now=50)
        self.assertEqual((m.session.mode, m.session.replay_since, m.session.replay['kills']), (HYBRID, 50, 10))
        m.set_replay(False)
        self.assertEqual(m.session.mode, SCREEN)

    def test_bus_hybrid_merge_no_double_count(self):
        m = self.mgr()
        bus = EventBus(EventDeduplicator(window_sec=5))
        bus.subscribe(m.on_event)
        m.start(0)
        bus.publish(ev(10, GE.ITEM_GAIN, item_id=22687, item_name='사념의 파편', quantity=2))
        bus.publish(ev(12, GE.ITEM_GAIN, REPLAY, confidence=HIGH, item_id=22687, item_name='사념의 파편', quantity=2))
        self.assertEqual(m.session.items[22687]['qty'], 2)
        self.assertEqual(m.session.items[22687]['sources'], {REPLAY})          # 화면 집계는 Replay로 대체
        acc = accuracy(m.session)
        self.assertEqual(acc[GE.ITEM_GAIN]['replay'], 1)

    def test_map_change_splits_session(self):
        m = self.mgr()
        m.on_event(ev(0, GE.MAP_CHANGED, metadata=dict(map_name='A')))
        m.start(0)
        m.on_event(ev(100, GE.MAP_CHANGED, REPLAY, metadata=dict(map_name='B')))
        self.assertIsNone(m.session)
        self.assertEqual((m.history[0].map, m.map, m.map_source), ('A', 'B', '●'))


class VisionProfileTest(unittest.TestCase):
    def test_bar_fill(self):
        img = np.full((12, 200, 3), 40, np.uint8)
        img[:, :130] = (60, 200, 60)
        self.assertAlmostEqual(bar_fill_ratio(img), 0.65, places=2)
        self.assertAlmostEqual(bar_fill_ratio(img, [60, 200, 60]), 0.65, places=2)
        self.assertEqual(bar_fill_ratio(np.full((12, 200, 3), 40, np.uint8)), 0.0)
        self.assertFalse(changed(img, img.copy()))

    def test_suggest_bars(self):
        """보정 도우미: 막대처럼 보이는 곳만 후보로 올리고, 색으로 HP/SP/AP를 맞춘다."""
        img = np.full((300, 600, 3), 30, np.uint8)
        img[20:31, 20:180] = (60, 200, 60)        # 초록 = HP
        img[40:51, 20:100] = (220, 60, 60)        # 파랑(BGR) = SP
        img[200:260, 300:360] = (200, 200, 200)   # 큰 회색 사각형 = 막대 아님
        found = dict(suggest_bars(img))
        self.assertEqual(sorted(found), ['HP_REGION', 'SP_REGION'])
        self.assertEqual(found['HP_REGION'][:2], (20, 20))
        self.assertEqual(found['SP_REGION'][1], 40)
        self.assertEqual(suggest_bars(np.full((50, 50, 3), 10, np.uint8)), [])

    def test_profile_store_and_scale(self):
        db = Database(os.path.join(tempfile.mkdtemp(), 'p.db'))
        store = ProfileStore(db)
        p = store.get(1920, 1080)
        self.assertEqual((p.calibrated, p.regions), (False, {}))       # 좌표를 지어내지 않음
        p.regions['HP_REGION'] = Region('HP_REGION', 100, 50, 200, 10)
        p.calibrated = True
        store.save(p)
        self.assertEqual(store.get(1920, 1080).regions['HP_REGION'].x, 100)
        q = store.get(3840, 2160)
        self.assertEqual((q.regions['HP_REGION'].x, q.calibrated, q.derived_from), (200, False, '1920x1080'))


class OcrFallbackTest(unittest.TestCase):
    """글자 인식이 안 될 때 이유를 숨기지 않는다(설치 안 됨 / 언어팩 없음)."""

    def test_missing_language_reports_reason(self):
        from live.ocr import create_ocr
        o = create_ocr('zz-ZZ')
        self.assertFalse(o.available)
        self.assertIn('언어팩', o.error)

    def test_missing_package_gives_install_command(self):
        import sys
        from live import ocr
        for mod in ('winrt', 'winrt.windows.globalization', 'winrt.windows.media.ocr'):
            self.addCleanup(sys.modules.pop, mod, None)
            sys.modules[mod] = None                       # import 시 ImportError
        o = ocr.create_ocr('ko')
        self.assertFalse(o.available)
        self.assertIn('pip install', o.error)
        self.assertEqual(o.recognize(np.zeros((4, 4, 3), np.uint8)), [])


class MigrationTest(unittest.TestCase):
    def test_v4_tables(self):
        db = Database(os.path.join(tempfile.mkdtemp(), 'm.db'))
        for t in ('live_sessions', 'game_events', 'screen_profiles', 'ocr_diagnostics', 'replay_sessions', 'guide_progress'):
            self.assertTrue(db._query("SELECT name FROM sqlite_master WHERE name=?", (t,)), t)


class MockScreenTest(unittest.TestCase):
    """합성 게임 화면(채팅 줄 + HP/SP 바)을 실제 Windows OCR로 읽어 ITEM_GAIN → 수익까지 확인."""

    @classmethod
    def setUpClass(cls):
        from live.ocr import create_ocr
        cls.ocr = create_ocr('ko')

    def frame(self, lines, hp=0.8, sp=0.4):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (800, 450), (30, 30, 40))
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(r'C:\Windows\Fonts\malgun.ttf', 15)
        d.rectangle([20, 20, 20 + int(200 * hp), 30], fill=(60, 200, 60))
        d.rectangle([20, 40, 20 + int(200 * sp), 50], fill=(60, 60, 220))
        for i, t in enumerate(lines):
            d.text((20, 300 + i * 22), t, fill=(255, 255, 255), font=font)
        os.makedirs(FIX, exist_ok=True)
        path = os.path.join(FIX, 'mock_%d.png' % len(lines))
        img.save(path)
        return np.array(img)[:, :, ::-1].copy()

    def test_mock_frame_pipeline(self):
        if not self.ocr.available:
            self.skipTest('Windows 한국어 OCR 없음')
        from analyzer.gamedata import MapDB
        from live.engine import LiveEngine
        d = tempfile.mkdtemp()
        db = Database(os.path.join(d, 'e.db'))
        gd = type('GD', (), {'maps': MapDB(d)})()
        eng = LiveEngine(dict(SETTINGS, live_replay_auto=False), db, prices, FakeItems, gd, d)
        eng.ocr = self.ocr
        eng.chat = ChatMonitor(self.ocr)
        eng.parser = ChatParser(eng.patterns, ItemMatcher(FakeItems, eng.item_alias))
        eng.patterns.learn('시험 메시지 젤로피 3개 획득', GE.ITEM_GAIN, '젤로피', '3')     # 형식 학습(가상 문구)
        eng.profile = ScreenProfile('800x450', 800, 450, regions={
            'HP_REGION': Region('HP_REGION', 20, 15, 200, 20), 'SP_REGION': Region('SP_REGION', 20, 38, 200, 14),
            'CHAT_REGION': Region('CHAT_REGION', 10, 290, 500, 120)}, calibrated=True)
        eng._analyze(self.frame(['시험 메시지 포링 카드 1개 획득']), 100.0)
        eng._analyze(self.frame(['시험 메시지 포링 카드 1개 획득', '시험 메시지 젤로피 7개 획득']), 110.0)
        types = [e.event_type for e in eng.events]
        self.assertIn(GE.HP_CHANGE, types)
        self.assertAlmostEqual(eng.region_values['HP_REGION'], 80, delta=3)
        self.assertAlmostEqual(eng.region_values['SP_REGION'], 40, delta=3)
        gains = [(e.item_id, e.quantity) for e in eng.events if e.event_type == GE.ITEM_GAIN]
        self.assertEqual(gains, [(4001, 1), (909, 7)])
        s = eng.sessions.session
        self.assertIsNotNone(s)
        self.assertEqual(s.item_value(), 6_000_000 + 70)


if __name__ == '__main__':
    unittest.main()
