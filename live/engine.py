"""Passive Live 엔진 (백그라운드 스레드).

WAITING → (게임 창 발견) SCREEN → (Replay 녹화 감지) HYBRID → (녹화 중단) SCREEN → (게임 종료) WAITING
게임 클라이언트에는 아무것도 보내지 않는다: 창 탐지 · 창 캡처 · OCR/Vision · Replay 파일 읽기만 사용.
"""
import json
import logging
import threading
import time
import copy
import os
from collections import deque

from live import vision
from live.capture import CaptureEngine
from live.chat import AliasDB, ChatMonitor, ChatParser, ItemMatcher, PatternDB
from live.events import GE, HIGH, LOW, MEDIUM, SCREEN_OCR, SCREEN_VISION, EventBus, GameEvent
from live.ocr import create_ocr, normalize_number, normalize_text
from live.profile import ProfileStore
from live.replay_link import ReplayLink
from live.session import HYBRID, PAUSED, SCREEN, WAITING, SessionManager, accuracy
from live.wgc import WGCCapture
from live.window import find_game_window, is_alive

log = logging.getLogger(__name__)
BAR_EVENTS = {'HP_REGION': GE.HP_CHANGE, 'SP_REGION': GE.SP_CHANGE, 'AP_REGION': GE.AP_CHANGE}


class LiveEngine:
    def __init__(self, settings, db, price_lookup, item_db, gamedata, data_dir, alerts=None):
        self.s = settings
        self.db = db
        self.item_db = item_db
        self.gamedata = gamedata
        self.data_dir = data_dir
        self.alerts = alerts
        self.bus = EventBus()
        self.sessions = SessionManager(price_lookup, settings, alert=self._alert)
        self.bus.subscribe(self.sessions.on_event)
        self.bus.subscribe(self._record_event)
        self.profiles = ProfileStore(db)
        self.replay = ReplayLink(settings, gamedata, price_lookup=price_lookup)
        self._replay_thread = None
        self._screen_lock = threading.RLock()
        self._screen_snapshot = None
        self._journal = None
        self.capture_fallback = CaptureEngine(allow_screen_fallback=True)
        self.ocr = None
        self.win = None
        self.wgc = None
        self.profile = None
        self.state = WAITING
        self.status = 'Ragnarok 실행 대기'
        self.scene = 'UNKNOWN'
        self.last_frame = None
        self.region_values = {}
        self.region_text = {}
        self.chat_lines = deque(maxlen=200)
        self.events = deque(maxlen=500)
        self._prev = {}
        self._last_scene = 0
        self._last_replay = 0
        self._stop = threading.Event()
        self._thread = None
        self.chat = None
        self.parser = None
        self.ocr_error = ''
        self.patterns = PatternDB(f'{data_dir}/chat_patterns.json')
        self.item_alias = AliasDB(f'{data_dir}/item_ocr_alias.json')
        self.map_alias = AliasDB(f'{data_dir}/map_ocr_alias.json')
        self.fps = 0.0

    # ---------------------------------------------------------------- 수명 주기
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if self.s.get('live_replay_auto', True):
            from live.journal import LiveJournal
            self._journal = LiveJournal(os.path.join(self.data_dir, 'live_replay.db'))
            self.replay.journal = self._journal
            self._replay_thread = threading.Thread(target=self._replay_run, name='ReplayReader', daemon=True)
            self._replay_thread.start()
        self._thread = threading.Thread(target=self._run, name='LiveEngine', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(3)
        if self._replay_thread:
            self._replay_thread.join(10)
        with self._screen_lock:
            self._close_game(time.time(), save=True)
        if self._journal:
            self._journal.close()

    def _replay_run(self):
        while not self._stop.is_set():
            try:
                self.replay.poll()
            except Exception:
                log.exception('Replay worker error')
            self._stop.wait(max(0.02, self.s.get('live_poll_ms', 100) / 1000))

    def _run(self):
        try:
            self.ocr = create_ocr(self.s.get('live_ocr_lang', 'ko'))
        except Exception as ex:
            log.exception('OCR 생성 실패')
            from live.ocr import NullOCR
            self.ocr = NullOCR()
            self.ocr.error = str(ex)
        self.ocr_error = getattr(self.ocr, 'error', '')
        if self.ocr_error:
            log.warning('글자 인식 사용 불가: %s', self.ocr_error)
        if self.item_db and not self.item_db.items:
            try:
                self.item_db.load()
            except Exception:
                log.exception('Item DB 로드 실패')
        self.chat = ChatMonitor(self.ocr)
        self.parser = ChatParser(self.patterns, ItemMatcher(self.item_db, self.item_alias))
        while not self._stop.is_set():
            t0 = time.time()
            try:
                with self._screen_lock:
                    self._tick(t0)
                    self._screen_snapshot = self._build_snapshot()
            except Exception:
                log.exception('Live tick 오류')
            active = self.sessions.session is not None and self.sessions.session.state == 'ACTIVE'
            fps = self.s.get('live_fps', 5) if active else self.s.get('live_idle_fps', 1)
            if self.state == WAITING:
                fps = 1 / 3
            dt = time.time() - t0
            self.fps = round(1 / max(dt, 1e-3), 1)
            self._stop.wait(max(0.0, 1.0 / max(fps, 0.1) - dt))

    def _tick(self, now):
        if self.win is None or not is_alive(self.win.hwnd):
            if self.win is not None:
                self._close_game(now, save=True)
            win = find_game_window(self.s.get('live_process_names', ['Ragexe.exe', 'Ragnarok.exe']))
            if not win:
                self.state, self.status = WAITING, 'Ragnarok 실행 대기'
                return
            self._open_game(win, now)
        else:
            fresh = find_game_window(self.s.get('live_process_names', ['Ragexe.exe', 'Ragnarok.exe']))
            if fresh and fresh.hwnd == self.win.hwnd:
                if fresh.client[2:] != self.win.client[2:]:
                    self.profile = self.profiles.get(*fresh.client[2:])
                self.win = fresh                         # 창 이동/크기 변경 따라가기
        frame = self._grab()
        self.last_frame = frame
        if frame is None:
            self.status = '캡처 대기: %s' % (self.capture_fallback.last_error or 'WGC 프레임 없음')
        else:
            self._analyze(frame, now)
        self.sessions.tick(now)
        s = self.sessions.session
        if s is None:
            self.state = HYBRID if self.replay.active else SCREEN
        else:
            self.state = PAUSED if s.state == 'PAUSED' else s.mode

    def _open_game(self, win, now):
        self.win = win
        self.profile = self.profiles.get(*win.client[2:])
        if WGCCapture.available:
            self.wgc = WGCCapture(win.hwnd, int(1000 / max(1, self.s.get('live_fps', 5)))).start()
        self.bus.publish(GameEvent(GE.GAME_STARTED, SCREEN_VISION, HIGH, now, metadata=dict(title=win.title,
                                                                                             process=win.process)))
        self.status = 'Ragnarok 감지 (%s, %dx%d)' % (win.process, win.client[2], win.client[3])
        self.state = SCREEN

    def _close_game(self, now, save):
        if self.win is None:
            return
        self.bus.publish(GameEvent(GE.GAME_CLOSED, SCREEN_VISION, HIGH, now))
        if self.wgc:
            self.wgc.stop()
        self.wgc, self.win, self.last_frame = None, None, None
        if save:
            self.save_finished()
        self.state, self.status = WAITING, 'Ragnarok 실행 대기'

    def _grab(self):
        if self.wgc and not self.wgc.closed:
            img, t = self.wgc.client_frame(self.win)
            if img is not None:
                self.capture_method = 'WGC'
                return img
        img = self.capture_fallback.capture(self.win)
        self.capture_method = self.capture_fallback.method or '-'
        return img

    # ---------------------------------------------------------------- 화면 분석
    def _analyze(self, frame, now):
        p = self.profile
        if not p or not p.regions:
            if now - self._last_scene > 5:
                self._last_scene = now
                self.scene = vision.scene_from_text(self.ocr.recognize(frame))
            self.status = 'Region 보정 필요 (Live 탭 > 영역 설정)' + (' · 캐릭터 선택 화면' if self.scene == 'CHARACTER_SELECT' else '')
            return
        self.status = 'LIVE %s · %s' % (self.state, 'Region %d개' % len(p.regions))
        for kind, reg in p.regions.items():
            img = reg.crop(frame)
            if img is None:
                continue
            if kind in BAR_EVENTS:
                v = vision.bar_fill_ratio(img, reg.bar_color or None)
                if v is not None:
                    pct = round(v * 100, 1)
                    if abs(pct - self.region_values.get(kind, -100)) >= 1.0:
                        self.region_values[kind] = pct
                        self.bus.publish(GameEvent(BAR_EVENTS[kind], SCREEN_VISION, MEDIUM, now, value=pct,
                                                   metadata=dict(unit='%')))
            elif kind in ('CHAT_REGION', 'ITEM_MESSAGE_REGION'):
                for seq, line in self.chat.update(img):
                    self.chat_lines.append((now, line.text, line.confidence))
                    ev = self.parser.parse(seq, line, self.sessions.map)
                    if ev:
                        ev.timestamp = now
                        self.bus.publish(ev)
                    else:
                        self._diag('CHAT', line.text, line.confidence, 'no_pattern')
            elif kind in ('EXP_REGION', 'WEIGHT_REGION', 'MAP_REGION', 'JOB_EXP_REGION'):
                if not vision.changed(self._prev.get(kind), img, 4.0):
                    continue
                self._prev[kind] = img.copy()
                text = normalize_text(' '.join(l.text for l in self.ocr.recognize(img, scale=2.0)))
                self.region_text[kind] = text
                self._text_region(kind, text, now)

    def _text_region(self, kind, text, now):
        if kind == 'EXP_REGION':
            import re
            m = re.search(r'([\dOoIl]{1,3}[.,][\dOoIl]{1,3})\s*%', text) or re.search(r'([\dOoIl]{1,3})\s*%', text)
            if m:
                v = float(m.group(1).translate(str.maketrans('OoIl,', '0011.')))
                if 0 <= v <= 100:
                    self.region_values[kind] = v
                    self.bus.publish(GameEvent(GE.EXP_CHANGE, SCREEN_OCR, MEDIUM, now, value=v, metadata=dict(unit='%', text=text)))
            else:
                self._diag(kind, text, 0.5, 'exp_unparsed')
        elif kind == 'WEIGHT_REGION':
            import re
            m = re.search(r'([\d,OoIl]+)\s*/\s*([\d,OoIl]+)', text)
            pct = None
            if m:
                a, b = normalize_number(m.group(1)), normalize_number(m.group(2))
                pct = round(a * 100 / b, 1) if a is not None and b else None
            else:
                m = re.search(r'([\d.]+)\s*%', text)
                pct = float(m.group(1)) if m else None
            if pct is not None and 0 <= pct <= 200:
                self.region_values[kind] = pct
                self.bus.publish(GameEvent(GE.WEIGHT_CHANGE, SCREEN_OCR, MEDIUM, now, value=pct, metadata=dict(text=text)))
        elif kind == 'MAP_REGION' and text:
            name = self.map_alias.get(text) or text
            mid = next((k for k, v in self.gamedata.maps.names.items() if v and v == name), None)
            if name != self.sessions.map:
                self.bus.publish(GameEvent(GE.MAP_CHANGED, SCREEN_OCR, MEDIUM if mid else LOW, now, map=mid or name,
                                           metadata=dict(map_name=name, text=text)))

    # ---------------------------------------------------------------- 기록
    def _record_event(self, ev, superseded):
        self.events.append(ev)

    def _alert(self, kind, msg):
        if self.alerts:
            self.alerts.notify(msg)

    def _diag(self, region, text, conf, note):
        if conf < 0.8 or note:
            try:
                self.db._exec('INSERT INTO ocr_diagnostics VALUES (?,?,?,?,?)', (time.time(), region, text, conf, note))
            except Exception:
                pass

    def end_session(self):
        with self._screen_lock:
            s = self.sessions.end()
            if s:
                self._save(s)
        return s

    def save_finished(self):
        s = self.sessions.end()
        if s:
            self._save(s)

    def _save(self, s):
        active, elapsed = s.elapsed
        rp = s.replay or {}
        detail = dict(top=[{k: v for k, v in d.items() if k in ('item_id', 'name', 'qty', 'unit', 'value')}
                           for d in s.top_drops(20)], review=s.review[:200], alerts=s.alerts[-50:],
                      replay=rp, accuracy=accuracy(s), exp_first=s.exp_first, exp_last=s.exp_last)
        conf = '화면 기준' if s.mode == SCREEN else 'Replay 보강(%s)' % rp.get('file', '')
        try:
            with self.db._lock:
                cur = self.db.conn.execute(
                    'INSERT INTO live_sessions(start_time,end_time,data_mode,confidence,map,map_source,active_sec,'
                    'elapsed_sec,item_value,zeny,profit,profit_per_hour,exp_pct_per_hour,kills,combat_dps,detail_json) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (s.start_time, s.end_time, s.mode, conf, s.map, s.map_source, active, elapsed, int(s.item_value()),
                     s.zeny_gain if s.zeny_known else None, int(s.profit()), s.per_hour(s.profit()), s.exp_pct_per_hour(),
                     rp.get('kills'), rp.get('dps'), json.dumps(detail, ensure_ascii=False, default=str)))
                sid = cur.lastrowid
                rows = [(sid, e.timestamp, e.event_type, e.source, e.confidence, e.item_id, e.item_name, e.quantity,
                         e.value, str(e.raw_data)[:200] if e.raw_data else None)
                        for e in self.events if s.start_time <= e.timestamp <= (s.end_time or time.time())]
                self.db.conn.executemany('INSERT INTO game_events VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
                self.db.conn.commit()
        except Exception:
            log.exception('Live 세션 저장 실패')

    # ---------------------------------------------------------------- UI용 스냅샷
    def snapshot(self):
        # Only detached published data crosses the worker/UI boundary.
        out = copy.deepcopy(self._screen_snapshot) if self._screen_snapshot else dict(
            state='WAITING', status='시작 중', ocr_error=self.ocr_error, window=None, capture='-', calibrated=False,
            values={}, map=None, map_source='', replay_active=False, replay_file=None,
            replay_error='', session=None, scene='UNKNOWN', drops=[], review=[], events=[], chat=[])
        rp = self.replay.snapshot()
        out.update(replay_live=rp, replay_active=self.replay.active, replay_file=self.replay.path,
                   replay_error=self.replay.error)
        if rp and rp.get('stage') in ('CONFIRMED', 'FINAL'):
            out['map'] = rp.get('map')
            out['map_source'] = '● Replay'
            if self.replay.active:
                out['state'] = 'REPLAY'
        return out

    def _build_snapshot(self):
        s = self.sessions.session
        out = dict(state=self.state, status=self.status, ocr_error=self.ocr_error,
                   window=None, capture=getattr(self, 'capture_method', '-'),
                   calibrated=bool(self.profile and self.profile.regions), values=dict(self.region_values),
                   map=self.sessions.map, map_source=self.sessions.map_source, replay_active=self.replay.active,
                   replay_file=self.replay.path, replay_error=self.replay.error, session=None, scene=self.scene,
                   drops=copy.deepcopy(s.top_drops(50)) if s else [], review=copy.deepcopy(s.review) if s else [],
                   events=list(self.events), chat=list(self.chat_lines))
        if self.win:
            out['window'] = dict(title=self.win.title, process=self.win.process, client=self.win.client,
                                 foreground=self.win.foreground, dpi=self.win.dpi)
        if s:
            active, elapsed = s.elapsed
            out['session'] = dict(
                state=s.state, mode=s.mode, active=active, elapsed=elapsed, map=s.map, item_value=s.item_value(),
                profit=s.profit(), profit_per_hour=s.per_hour(s.profit()), exp_pct_per_hour=s.exp_pct_per_hour(),
                exp_last=s.exp_last, zeny=s.zeny_gain if s.zeny_known else None, top=s.top_drops(5),
                review=len(s.review), alerts=s.alerts[-3:], replay=s.replay, accuracy=accuracy(s))
        return out
