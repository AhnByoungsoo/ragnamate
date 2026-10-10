"""오늘의 사냥 결산 · 득템 알림: 합산, 시세, 효과음. 공식 사이트 요청은 가짜 클라이언트."""
import datetime
import os
import struct
import tempfile
import time
import unittest
import wave
from types import SimpleNamespace

from helpers import PLAYER, ev

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication  # noqa: E402

from analyzer.events import FROM_STORAGE, LOOT, NPC_SALE, OTHER_GAIN, OTHER_INCOME, PURCHASE, SPEND, VEND_SALE  # noqa: E402
from analyzer.events import EventType as E  # noqa: E402
from core import ledger as L  # noqa: E402
from database.database import Database  # noqa: E402
from services import gnjoy_market as G  # noqa: E402
from services.ledger_service import LedgerService  # noqa: E402
from services.loot_value import LootValue  # noqa: E402
from ui.loot_alert import DEFAULT_SOUND  # noqa: E402
from utilities import voice  # noqa: E402

APP = QApplication.instance() or QApplication([])
CARD, JUNK, ORE = 4001, 7219, 984
NAMES = {CARD: '포링 카드', JUNK: '줄무늬 양말', ORE: '오리데오콘'}
TODAY = datetime.date.today().isoformat()


def write(path, data, mode='wb'):
    with open(path, mode) as f:
        f.write(data)


def gain(t, item_id, amount=1, source=LOOT):
    return ev(t, E.ITEM_GAIN, PLAYER, item_id=item_id, amount=amount, source=source)


def flow(t, amount, kind):
    return ev(t, E.ZENY_FLOW, PLAYER, amount=amount, kind=kind)


EVENTS = [gain(0, JUNK, 3), gain(10, CARD, 1, OTHER_GAIN), gain(20, ORE, 5, FROM_STORAGE), gain(30, ORE, 2, PURCHASE),
          flow(40, -40_000, SPEND), flow(50, 124_000, NPC_SALE), flow(60, 15_000_000, VEND_SALE),
          flow(70, 500, OTHER_INCOME)]


class LedgerTest(unittest.TestCase):
    def test_storage_and_purchases_are_not_earnings(self):
        e = L.from_events(EVENTS, PLAYER)
        self.assertEqual(e['items'], {JUNK: 3, CARD: 1})              # 창고에서 꺼낸 것·산 것은 빠진다
        self.assertEqual(e['income'], {NPC_SALE: 124_000, VEND_SALE: 15_000_000, OTHER_INCOME: 500})
        self.assertEqual(e['spent'], 40_000)

    def test_merge_adds_replays_and_survives_json_keys(self):
        a = L.from_events(EVENTS, PLAYER)
        b = dict(items={str(JUNK): 2}, income={NPC_SALE: 1000}, spent=10)       # DB에서 읽으면 열쇠가 글자다
        m = L.merge([a, b])
        self.assertEqual(m['items'][JUNK], 5)
        self.assertEqual(m['income'][NPC_SALE], 125_000)
        self.assertEqual(m['spent'], 40_010)

    def test_settle_never_invents_a_price(self):
        vend = lambda i: ({CARD: 12_000_000}.get(i), '지금 판매 최저가')              # noqa: E731
        res = L.settle(L.from_events(EVENTS, PLAYER), vend, NAMES.get, jackpot_min=10_000_000)
        self.assertEqual(res['loot_value'], 12_000_000)               # 시세를 모르는 양말은 합계에 넣지 않는다
        self.assertEqual(res['unpriced'], 1)
        self.assertIsNone(next(r for r in res['rows'] if r['item_id'] == JUNK)['value'])
        self.assertEqual([r['name'] for r in res['jackpots']], ['포링 카드'])
        self.assertEqual((res['sales'], res['zeny_in'], res['zeny_net']), (15_124_000, 15_124_500, 15_084_500))
        self.assertNotIn('total', res)                                # 획득 가치와 판매 제니는 더하지 않는다

    def test_settle_uses_the_better_of_vending_and_shop(self):
        vend = lambda i: ({CARD: 12_000_000}.get(i), '')                             # noqa: E731
        res = L.settle(L.from_events(EVENTS, PLAYER), vend, NAMES.get, npc_of={JUNK: 570, CARD: 12}.get)
        rows = {r['item_id']: r for r in res['rows']}
        self.assertEqual((rows[JUNK]['sell_at'], rows[JUNK]['value']), ('상점', 1710))      # 양말 3개는 상점에
        self.assertEqual((rows[CARD]['sell_at'], rows[CARD]['value']), ('노점', 12_000_000))
        self.assertEqual((res['loot_value'], res['vend_value'], res['npc_value'], res['unpriced']),
                         (12_001_710, 12_000_000, 1722, 0))

    def test_empty_day(self):
        res = L.settle(L.merge([]), lambda i: (None, ''), str)
        self.assertEqual((res['rows'], res['loot_value'], res['zeny_net']), ([], None, 0))   # 가치는 0이 아니라 '없음'


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, 'm.db'))
        self.addCleanup(self.db.conn.close)
        self.svc = LedgerService(self.db, dict(replay_dir=self.tmp.name))

    def replay(self, name, at, events=EVENTS, character='사냥꾼'):
        path = os.path.join(self.tmp.name, name)
        write(path, b'x')
        pr = SimpleNamespace(events=events, player_aid=PLAYER, recorded_at=at, character=character,
                             server='바포메트', job_id=4262, duration_ms=60_000)
        return path, pr

    def test_day_collects_replays_recorded_that_day(self):
        self.svc.record(*reversed(self.replay('a.rrf', datetime.datetime(2026, 10, 10, 9, 0))))
        self.svc.record(*reversed(self.replay('b.rrf', datetime.datetime(2026, 10, 10, 21, 0), character='상인')))
        self.svc.record(*reversed(self.replay('c.rrf', datetime.datetime(2026, 10, 9, 23, 50))))
        rows = self.svc.day('2026-10-10')
        self.assertEqual([(r['file'], r['character']) for r in rows], [('a.rrf', '사냥꾼'), ('b.rrf', '상인')])
        self.assertEqual(L.merge(r['entry'] for r in rows)['items'][JUNK], 6)
        self.assertEqual(self.svc.days(), ['2026-10-10', '2026-10-09'])

    def test_reanalysing_the_same_replay_does_not_double_count(self):
        path, pr = self.replay('a.rrf', datetime.datetime(2026, 10, 10, 9, 0))
        self.svc.record(pr, path)
        self.svc.record(pr, path)
        self.assertEqual(len(self.svc.day('2026-10-10')), 1)
        self.assertTrue(self.svc._current(path))
        write(path, b'more', 'ab')                               # 파일이 바뀌면 다시 읽어야 한다
        self.assertFalse(self.svc._current(path))

    def test_same_file_name_recorded_again_is_a_new_entry(self):
        path, pr = self.replay('a.rrf', datetime.datetime(2026, 10, 10, 9, 0))
        self.svc.record(pr, path)
        pr.recorded_at = datetime.datetime(2026, 10, 11, 9, 0)
        self.svc.record(pr, path)
        self.assertEqual((len(self.svc.day('2026-10-10')), len(self.svc.day('2026-10-11'))), (1, 1))

    def test_refresh_reports_unreadable_files_instead_of_failing(self):
        path, _ = self.replay('broken.rrf', datetime.datetime.now())
        done, failed = self.svc.refresh(TODAY)
        self.assertEqual((done, failed), (0, ['broken.rrf']))


class FakeClient:
    def __init__(self, listings=(), history=(), more=False):
        self.rows, self.hist, self.more, self.calls = list(listings), list(history), more, []

    def listings(self, server, name, max_pages=1, with_more=False):
        self.calls.append(('listings', name))
        return (self.rows, self.more) if with_more else self.rows

    def history(self, server, name, pages=1):
        self.calls.append(('history', name))
        return self.hist, 1


def listing(item_id, name, price, kind='sale'):
    return dict(item_id=item_id, name=name, price=price, qty=1, shop='가게', kind=kind, ssi='1', map_code='')


class LootValueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'))
        self.addCleanup(self.store.close)

    def value(self, client):
        return LootValue(self.store, client)

    def test_unknown_until_fetched_then_lowest_sale(self):
        client = FakeClient([listing(CARD, '포링 카드', 13_000_000), listing(CARD, '포링 카드', 12_000_000),
                             listing(CARD, '포링 카드', 20_000_000, 'buy'), listing(4002, '마스터링 카드', 100)])
        v = self.value(client)
        self.assertEqual(v.price(CARD), (None, ''))
        self.assertEqual(v.ensure(CARD, '포링 카드'), (12_000_000, '지금 판매 최저가'))   # 구매 노점·다른 카드 제외
        self.assertEqual(len(client.calls), 2)
        v.ensure(CARD, '포링 카드')
        self.assertEqual(len(client.calls), 2)                        # 30분 안에는 다시 묻지 않는다

    def test_falls_back_to_recent_average_when_nobody_sells(self):
        days = [dict(day=(datetime.date.today() - datetime.timedelta(days=i)).isoformat(), min=9, avg=11_000_000,
                     max=12) for i in range(3)]
        self.assertEqual(self.value(FakeClient([], days)).ensure(CARD, '포링 카드'), (11_000_000, '최근 7일 거래 평균'))

    def test_crowded_search_stays_unknown(self):
        v = self.value(FakeClient([listing(4002, '포링 카드첩', 100)], more=True))
        self.assertEqual(v.ensure(CARD, '포링 카드'), (None, ''))       # '노점 없음'으로 단정하지 않는다
        self.assertTrue(v.fresh(CARD))                                # 그래도 30분 동안은 다시 묻지 않는다


class Bag:
    """queue.Queue 대신 넣은 것만 모아 두는 통(가격 조회 스레드와 섞이지 않게)."""
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class LootAlertTest(unittest.TestCase):
    def setUp(self):
        from ui.loot_alert import LootAlert
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = G.VendingStore(os.path.join(self.tmp.name, 'v.db'))
        self.addCleanup(self.store.close)
        self.client = FakeClient([listing(CARD, '포링 카드', 12_000_000)])
        self.s = dict(jackpot_enabled=True, jackpot_min_price=10_000_000)
        self.fired = []
        self.alert = LootAlert(self.s, LootValue(self.store, self.client), NAMES.get,
                               lambda text, sound: self.fired.append((text, sound)))
        self.addCleanup(self.alert.stop)

    def snap(self, *gains, session='s1'):
        return dict(session_id=session, gains=[dict(seq=i + 1, item_id=item, amount=1) for i, item in enumerate(gains)])

    def test_backlog_is_skipped_and_only_new_gains_are_checked(self):
        bag = self.alert._q = Bag()
        self.alert.feed(self.snap(JUNK, JUNK))                        # 붙기 전에 이미 주운 것
        self.assertEqual(bag.items, [])
        self.alert.feed(self.snap(JUNK, JUNK, CARD))
        self.assertEqual([g['item_id'] for g in bag.items], [CARD])
        self.alert.feed(self.snap(JUNK, JUNK, CARD))                  # 같은 스냅샷이 또 와도 한 번만
        self.assertEqual(len(bag.items), 1)
        self.alert.feed(self.snap(CARD, session='s2'))                # 다른 리플레이: 다시 기준점부터
        self.assertEqual(len(bag.items), 1)

    def drops(self, *items, session='s1'):
        return dict(session_id=session, gains=[], drops=[dict(seq=i + 1, item_id=item, amount=1) for i, item in enumerate(items)])

    def test_valuable_drop_is_announced_before_pickup_without_the_fanfare(self):
        bag = self.alert._q = Bag()
        self.alert.feed(self.drops(JUNK))                             # 붙기 전에 떨어진 것은 알리지 않는다
        self.alert.feed(self.drops(JUNK, CARD))
        self.assertEqual([(g['item_id'], g['drop']) for g in bag.items], [(CARD, True)])
        found = self.alert.check(bag.items[0])
        self.assertTrue(found['drop'])
        self.alert._announce(found)
        text, sound = self.fired[0]
        self.assertIn('떨어졌습니다', text)
        self.assertIn('12,000,000', text)
        self.assertEqual(sound, '')                                   # 축하 소리는 주운 뒤에

    def test_drop_alert_can_be_turned_off_alone(self):
        bag = self.alert._q = Bag()
        self.s['drop_alert'] = False
        self.alert.feed(self.drops())
        self.alert.feed(self.drops(CARD))
        self.assertEqual(bag.items, [])

    def test_off_switch(self):
        bag = self.alert._q = Bag()
        self.s['jackpot_enabled'] = False
        self.alert.feed(self.snap())
        self.alert.feed(self.snap(CARD))
        self.assertEqual(bag.items, [])

    def test_expensive_item_is_announced_with_the_sound(self):
        found = self.alert.check(dict(item_id=CARD, amount=1))
        self.assertEqual((found['name'], found['unit'], found['basis']), ('포링 카드', 12_000_000, '지금 판매 최저가'))
        self.alert._announce(found)
        text, sound = self.fired[0]
        self.assertIn('포링 카드', text)
        self.assertIn('12,000,000', text)
        self.assertEqual(sound, DEFAULT_SOUND)

    def test_cheap_or_unknown_items_stay_silent(self):
        self.client.rows = [listing(JUNK, '줄무늬 양말', 300)]
        self.assertIsNone(self.alert.check(dict(item_id=JUNK, amount=5)))
        calls = len(self.client.calls)
        self.store.save_listings('129', JUNK, self.client.rows, now=time.time() - 86400)    # 어제 받은 값도 300
        self.assertIsNone(self.alert.check(dict(item_id=JUNK, amount=1)))
        self.assertEqual(len(self.client.calls), calls)               # 싼 것으로 아는 잡템은 다시 묻지 않는다
        self.client.rows = []
        self.assertIsNone(self.alert.check(dict(item_id=ORE, amount=1)))                    # 시세 모름 → 알리지 않음

    def test_default_sound_is_a_real_wav(self):
        from ui.loot_alert import FANFARE, VOICE, sound_path
        self.assertEqual(DEFAULT_SOUND, VOICE)                        # 기본은 효과음 + 음성
        for path in (FANFARE, VOICE):
            with wave.open(path) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 44100))
                self.assertGreater(w.getnframes() / w.getframerate(), 0.8)
        with wave.open(FANFARE) as a, wave.open(VOICE) as b:           # 음성이 붙은 쪽이 더 길다
            self.assertGreater(b.getnframes(), a.getnframes())
        self.assertEqual(sound_path(dict(jackpot_sound='없는파일.wav')), DEFAULT_SOUND)
        self.assertEqual(sound_path(dict(jackpot_sound=DEFAULT_SOUND)), DEFAULT_SOUND)


class VoiceTest(unittest.TestCase):
    def tone(self, path, seconds, level, lead=0.0):
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(voice.RATE)
            lead_n, n = int(lead * voice.RATE), int(seconds * voice.RATE)
            w.writeframes(struct.pack('<%dh' % (lead_n + n + lead_n), *([0] * lead_n + [level] * n + [0] * lead_n)))

    def test_mix_puts_the_voice_after_the_fanfare_starts_and_makes_it_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            spoken, fanfare, out = (os.path.join(tmp, n) for n in ('v.wav', 'f.wav', 'o.wav'))
            self.tone(spoken, 1.0, 4000, lead=1.0)            # 작게 녹음됐고 앞뒤가 비어 있는 음성
            self.tone(fanfare, 0.5, 20000)
            voice.mix(spoken, fanfare, out, delay=0.35, under_gain=0.4)
            data = voice._read(out)
            self.assertLess(len(data) / voice.RATE, 0.35 + 2.0 + 0.3)      # 뒤쪽 빈 소리는 잘렸다(3초 → 2.2초쯤)
            self.assertEqual(data[100], int(20000 * 0.4))                  # 처음에는 효과음만(작게)
            self.assertGreater(abs(data[int(1.8 * voice.RATE)]), 20000)    # 음성은 크게 키워졌다
            self.assertLessEqual(max(map(abs, data)), 32767)

    def test_quotes_and_empty_text(self):
        self.assertEqual(voice._quote("it's\n새 줄"), "it''s 새 줄")
        with self.assertRaises(ValueError):
            voice.speak_to_wav('   ', 'x.wav')

    def test_windows_voice_really_speaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                path = voice.speak_to_wav('득템 축하드려요!', os.path.join(tmp, 'v.wav'))
            except (RuntimeError, OSError):
                self.skipTest('Windows 한국어 음성 없음')
            data = voice._read(path)
            self.assertGreater(sum(1 for v in data if abs(v) > 800) / voice.RATE, 0.3)   # 소리가 0.3초 이상 들어 있다


class LiveLootTableTest(unittest.TestCase):
    """Live 탭 '현재 세션' 표: 리플레이가 붙어 있으면 얻은 아이템을 전부, 방금 얻은 것까지 보여 준다."""

    def setUp(self):
        from ui.pages import live_page as P
        self.P = P
        self.drawn, self.note = [], []
        self.page = SimpleNamespace(
            drops=SimpleNamespace(set_rows=self.drawn.append), drops_note=SimpleNamespace(setText=self.note.append),
            _loot_key=None, _loot_priced_at=0.0,
            app=SimpleNamespace(item_db=SimpleNamespace(name=NAMES.get), s={},
                                vend_price=lambda i: ({CARD: 12_000_000, JUNK: 300}.get(i), '지금 판매 최저가'),
                                npc_price={JUNK: 570, ORE: 682}.get))

    def snap(self, items, last=None, confidence='CONFIRMED'):
        return dict(session_id='s1', loot_confidence=confidence,
                    ledger=dict(items={str(k): v for k, v in items.items()},          # 스냅샷은 JSON이라 열쇠가 글자다
                                last={str(k): v for k, v in (last or {}).items()}))

    def refresh(self, rp):
        return self.P.LivePage._refresh_loot(self.page, rp)

    def test_every_gained_item_is_listed_with_price_and_time(self):
        self.assertTrue(self.refresh(self.snap({JUNK: 19, CARD: 1, ORE: 2}, {JUNK: 61_000, CARD: 125_000})))
        rows = {r['item_id']: r for r in self.drawn[-1]}
        self.assertEqual(set(rows), {JUNK, CARD, ORE})                         # 싼 잡템도 빠짐없이
        self.assertEqual((rows[JUNK]['name'], rows[JUNK]['quantity'], rows[JUNK]['last']), ('줄무늬 양말', 19, 61_000))
        self.assertEqual((rows[JUNK]['vend'], rows[JUNK]['npc'], rows[JUNK]['sell_at'], rows[JUNK]['value']),
                         (300, 570, '상점', 10_830))                                 # 노점 300 < 상점 570 → 상점에
        self.assertEqual((rows[CARD]['sell_at'], rows[CARD]['value'], rows[CARD]['npc']), ('노점', 12_000_000, None))
        self.assertEqual((rows[ORE]['vend'], rows[ORE]['npc'], rows[ORE]['value']), (None, 682, 1364))
        self.assertIn('3종 22개', self.note[-1])
        self.assertIn('가치 12,012,194 Z', self.note[-1])                         # 더 비싼 쪽끼리 더한 값
        self.assertIn('노점에 팔면 12,005,700 Z', self.note[-1])
        self.assertIn('상점에 팔면 12,194 Z', self.note[-1])

    def test_unknown_on_both_sides_is_left_out_not_zero(self):
        self.page.app.npc_price = {}.get
        self.refresh(self.snap({ORE: 2}))
        self.assertEqual(self.drawn[-1][0]['value'], None)
        self.assertIn('가치 모름', self.note[-1])
        self.assertIn('값 모름 1종', self.note[-1])

    def test_table_is_redrawn_only_when_something_changed(self):
        self.refresh(self.snap({JUNK: 1}))
        self.refresh(self.snap({JUNK: 1}))                                     # 0.2초마다 오는 같은 스냅샷
        self.assertEqual(len(self.drawn), 1)                                   # 선택·스크롤이 흔들리지 않게
        self.refresh(self.snap({JUNK: 2}))                                     # 하나 더 주움 → 바로 반영
        self.assertEqual(len(self.drawn), 2)
        self.page._loot_priced_at -= self.P.LOOT_PRICE_SEC + 1                 # 시세가 방금 들어왔을 수 있다
        self.refresh(self.snap({JUNK: 2}))
        self.assertEqual(len(self.drawn), 3)

    def test_without_replay_the_screen_drops_are_used(self):
        self.assertFalse(self.refresh(None))
        self.assertFalse(self.refresh(self.snap({JUNK: 1}, confidence='INVENTORY_NET_CHANGE')))
        self.assertEqual(self.drawn, [])
        self.assertEqual(self.note[-1], self.P.LOOT_IDLE)


class DialogTest(unittest.TestCase):
    def setUp(self):
        from ui.settlement_dialog import SettlementDialog
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, 'm.db'))
        self.addCleanup(self.db.conn.close)
        self.svc = LedgerService(self.db, dict(replay_dir=self.tmp.name))
        now = datetime.datetime.now().replace(microsecond=0)
        path = os.path.join(self.tmp.name, 'a.rrf')
        write(path, b'x')
        self.svc.record(SimpleNamespace(events=EVENTS, player_aid=PLAYER, recorded_at=now, character='사냥꾼',
                                        server='바포메트', job_id=4262, duration_ms=60_000), path)
        self.live = None
        pricer = SimpleNamespace(price=lambda i: ({CARD: 12_000_000}.get(i), '지금 판매 최저가'))
        app = SimpleNamespace(s=dict(jackpot_min_price=10_000_000), item_db=SimpleNamespace(name=NAMES.get),
                              live_page=SimpleNamespace(last_replay_snap=None))
        self.app = app
        self.d = SettlementDialog(app, self.svc, pricer)
        self.addCleanup(self.d.close)
        self.addCleanup(self.d.task.close)

    def test_today_totals_and_jackpot_mark(self):
        d = self.d
        self.assertEqual(d.day.currentData(), TODAY)
        self.assertEqual(d.kpi['loot'].value.text(), '12,000,000')
        self.assertIn('1종', d.kpi['loot'].sub.text())                 # 양말은 시세 모름
        self.assertEqual(d.kpi['npc'].value.text(), '124,000')
        self.assertEqual(d.kpi['vend'].value.text(), '15,000,000')
        self.assertEqual(d.kpi['spent'].value.text(), '40,000')
        self.assertEqual(d.kpi['net'].value.text(), '+15,084,500')
        names = [d.items.model_.cols[0].value(r) for r in d.items.model_.rows]
        self.assertIn('★ 포링 카드', names)
        self.assertIn('포링 카드', d.status.text())

    def test_recording_in_progress_is_added_from_the_live_snapshot(self):
        self.app.live_page.last_replay_snap = dict(
            state='LIVE', file='now.rrf', character='사냥꾼', recorded_at=datetime.datetime.now().isoformat(),
            duration_ms=5000, ledger=dict(items={str(JUNK): 4}, income={}, spent=0))
        self.d.reload()
        self.assertEqual(len(self.d.entries), 2)
        junk = next(r for r in self.d.result['rows'] if r['item_id'] == JUNK)
        self.assertEqual(junk['quantity'], 7)                         # 저장된 3 + 녹화 중 4
        self.assertIn('녹화 중', self.d.replays.model_.cols[2].value(self.d.entries[-1]))

    def test_all_unpriced_shows_unknown_not_zero(self):
        self.d.pricer = SimpleNamespace(price=lambda i: (None, ''))
        self.d.reload()
        self.assertEqual(self.d.kpi['loot'].value.text(), '모름')
        self.assertIn('2종', self.d.kpi['loot'].sub.text())
        self.assertIn('2종', self.d.b_prices.text())                  # [시세 받기 (2종)]

    def test_no_data_day_says_so(self):
        self.db._exec('DELETE FROM daily_ledger')
        self.d.reload()
        self.assertEqual(self.d.kpi['loot'].value.text(), '-')
        self.assertIn('리플레이가 없습니다', self.d.status.text())


if __name__ == '__main__':
    unittest.main()
