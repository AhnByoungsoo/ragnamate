"""Incremental Replay engine with immutable published snapshots and FINAL audit."""
import datetime
import json
import logging
import os
import time
from dataclasses import dataclass

from analyzer.normalizer import PacketNormalizer, _i32, normalize
from analyzer.analyzers import buff, combat, exp_zeny, loot, skill
from analyzer.analyzers.context import Context
from live.reader import IncrementalReplayReader
from live.sequencer import EventSequencer
from live.state import GameStateReducer
from services import replay_service

log = logging.getLogger('parser')


@dataclass(frozen=True)
class LiveSnapshot:
    session_id: str
    revision: int
    stage: str
    payload_json: str

    def to_dict(self):
        return json.loads(self.payload_json)


def offline_metrics(pr, gamedata, settings):
    """Independent existing offline analyzers are the comparison oracle."""
    ctx = Context.build(pr, gamedata, settings, 0, pr.duration_ms)
    cb, sk, bf = combat.analyze(ctx), skill.analyze(ctx), buff.analyze(ctx)
    xp, zn = exp_zeny.analyze_exp(ctx), exp_zeny.analyze_zeny(ctx)
    lt = loot.analyze(ctx, lambda ids: {})
    return dict(damage=cb['total_damage'], kills=cb['kills'],
        skills={r['skill_id']: dict(casts=r['casts'], hits=r['hits'], uses=r['uses']) for r in sk['skills']},
        base_exp=xp.get('base_total') or 0, job_exp=xp.get('job_total') or 0, zeny=zn.get('net'),
        loot={r['item_id']: r['quantity'] for r in lt['loot']},
        buff_ms={r['efst']: r['active_ms'] for r in bf['buffs']})


def compare_metrics(live, final):
    def clean(v):
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items() if x != 0 or isinstance(x, dict)}
        return v
    rows = {}
    for key, expected in final.items():
        actual = live.get(key)
        same = clean(actual) == clean(expected)
        row = dict(live=actual, final=expected, equal=same)
        if isinstance(expected, (float, int)) and isinstance(actual, (float, int)):
            row['match_percent'] = 100.0 if same else max(0.0, 100 * (1 - abs(actual - expected) / max(abs(expected), 1)))
        rows[key] = row
    return dict(equal=all(r['equal'] for r in rows.values()), metrics=rows)


class ReplayLiveSession:
    def __init__(self, path, settings, gamedata, journal=None, price_lookup=None):
        self.settings, self.gamedata, self.journal = settings, gamedata, journal
        self.reader = IncrementalReplayReader(path, settings.get('packet_profile', 'kro_2026'))
        self.decoder = self.reducer = None
        self.sequencer = EventSequencer(settings.get('live_reorder_ms', 250))
        self.status = 'STARTING'
        self.last_growth = self.last_frame_at = time.monotonic()
        self.revision = self.packet_count = 0
        self.first_timestamp = None
        self.published = None
        self.accuracy = None
        self.error = ''
        self.last_checkpoint = 0
        self.last_publish = 0
        self.final_signature = None
        self.final_attempt = 0
        self.final_end = None
        self.price_lookup = price_lookup
        self._price_key = None
        self._prices = {}

    def _init_decoder(self):
        rp = self.reader.replay
        self.recovered = False
        checkpoint = self.journal.recovery(self.reader.path) if self.journal else None
        if checkpoint and tuple(checkpoint.get('identity', ())) == self.reader.identity:
            self.reader.session_id = checkpoint['session_id']
            self.recovered = True
        self.decoder = PacketNormalizer(rp, self.settings.get('experimental_packets', False),
                                        self.settings.get('packet_profile', 'kro_2026'), retain_actors=False)
        meta, sess = rp.meta_chunks(2), rp.meta_chunks(3)
        name = lambda k: (meta.get(k) or b'').split(b'\0')[0].decode('cp949', 'replace')
        self.metadata = dict(character=name(964), job_id=_i32(sess.get(1014)), level=_i32(sess.get(1016)),
                             player=self.decoder.player, recorded_at=datetime.datetime(*rp.date).isoformat())
        self.reducer = GameStateReducer(self.decoder.player, _i32(sess.get(1051)), name(965),
                                        self.settings.get('combat_gap_ms', 3000))

    def _consume(self, frames):
        for frame in frames:
            pid = int.from_bytes(frame.packet[:2], 'little')
            events = self.decoder.feed(frame.timestamp, frame.packet, self.packet_count)
            self.packet_count += 1
            if pid not in self.decoder.defs:
                self.reducer.recent.append(dict(timestamp=frame.timestamp - self.first_timestamp,
                    offset=frame.offset, packet_id=pid, event_type='UNKNOWN_PACKET', actor=None,
                    target=None, confidence='UNKNOWN', payload_hex=frame.packet[:64].hex()))
                if self.decoder.unknown[pid] == 1:
                    logging.getLogger('unknown_packets').info('LIVE offset=%d pid=0x%04x payload=%s',
                        frame.offset, pid, frame.packet[:64].hex())
            for e in events:                 # 출처 판정을 기다렸다 나온 이벤트는 이 프레임의 패킷이 아니다
                own = e.raw_packet in (None, self.packet_count - 1)
                self.reducer.apply(e, frame.offset if own else None, pid if own else None)
            self.reducer.advance(frame.timestamp - self.first_timestamp)
        # Contiguous safe checkpoint stops before any provisional/late frame.
        candidates = [f.offset for _, _, f in self.sequencer.queue]
        if not self.sequencer.late:
            self.reader.last_confirmed_offset = min(candidates, default=self.reader.framer.offset)
        self.reader.last_confirmed_timestamp = self.sequencer.confirmed_timestamp

    def step(self, now=None):
        now = time.monotonic() if now is None else now
        batch = self.reader.poll()
        if batch.reset:
            self.decoder = self.reducer = None
            self.sequencer = EventSequencer(self.settings.get('live_reorder_ms', 250))
            self.packet_count = 0
            self.first_timestamp = None
            self.accuracy = None
            self.status = 'STARTING'
            self._price_key = None
        if batch.growth or batch.frames:
            self.last_growth = now
            self.error = ''
            if self.status == 'FINISHED':
                self.accuracy = None
                if self.journal:
                    self.journal.resumed(self.reader.session_id)
            self.status = 'ACTIVE'
        if self.reader.replay is not None and self.decoder is None:
            self._init_decoder()
        if self.decoder is None:
            self.status = 'STARTING' if now - self.last_growth < self.settings.get('live_stall_sec', 5) else 'STALLED'
            return self.publish(now)
        for frame in batch.frames:
            if self.first_timestamp is None:
                self.first_timestamp = frame.timestamp
                self.decoder.t0 = frame.timestamp
            self.last_frame_at = now
            self._consume(self.sequencer.offer(frame))
        # Wall clock releases a held complete frame only. It never advances ReplayClock.
        if self.sequencer.queue and now - self.last_frame_at >= self.sequencer.window_ms / 1000:
            self._consume(self.sequencer.drain(final=True))
        idle = now - self.last_growth
        if self.status != 'FINISHED' and idle >= self.settings.get('live_stall_sec', 5):
            self.status = 'STALLED'
        if self.status == 'STALLED' and idle >= self.settings.get('live_finalize_sec', 60) and now - self.final_attempt >= 5:
            self.finalize(now)
        if self.journal and now - self.last_checkpoint >= 5:
            self.last_checkpoint = now
            self.journal.checkpoint(self.reader.checkpoint())
        return self.publish(now)

    def finalize(self, now=None):
        now = time.monotonic() if now is None else now
        self.final_attempt = now
        if not self.decoder or not self.reader.caught_up or self.reader.framer.pending:
            self.error = 'INCOMPLETE: final analysis waits for complete records'
            return False
        self.status = 'FINALIZING'
        self.publish(now, force=True)
        self._consume(self.sequencer.drain(final=True))
        before = os.stat(self.reader.path)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if signature(before) != self.reader.signature:
            self.status, self.error = 'STALLED', 'Replay changed before FINAL; retry after incremental read'
            return False
        rp, copy_path = replay_service.open_replay(self.reader.path)
        try:
            pr = normalize(rp, self.reader.path, self.settings.get('experimental_packets', False),
                           self.settings.get('packet_profile', 'kro_2026'))
        finally:
            replay_service.close_copy(self.reader.path, copy_path)
        after = os.stat(self.reader.path)
        if signature(before) != signature(after) or any(getattr(rp, 'framing', {}).values()):
            self.status, self.error = 'STALLED', 'File changed or incomplete/corrupt during FINAL; retry pending'
            return False
        golden = offline_metrics(pr, self.gamedata, self.settings)
        self.accuracy = compare_metrics(self.reducer.metrics(), golden)
        # Build final UI state from the offline event stream, not the LIVE totals.
        final_state = GameStateReducer(pr.player_aid, pr.start_zeny, pr.last_map,
                                       self.settings.get('combat_gap_ms', 3000))
        for e in pr.events:
            final_state.apply(e)
        final_state.advance(pr.duration_ms)
        self.final_view = final_state.view()
        self.final_view['metrics'] = golden
        self._add_profit(self.final_view, final_state)
        self.final_signature, self.final_end = signature(after), self.reader.last_read_offset
        self.status, self.error = 'FINISHED', ''
        if self.journal:
            self.journal.final(self.reader.session_id, self.reader.path, self.accuracy, self.final_view)
        self.publish(now, force=True)
        return True

    def publish(self, now=None, force=False):
        now = time.monotonic() if now is None else now
        if not force and self.published and now - self.last_publish < self.settings.get('live_snapshot_ms', 200) / 1000:
            return self.published
        self.last_publish = now
        r, d, seq = self.reader, self.decoder, self.sequencer
        view = self.final_view if self.status == 'FINISHED' else (self.reducer.view() if self.reducer else {})
        if self.reducer and self.status != 'FINISHED':
            self._add_profit(view, self.reducer)
        diag = dict(bytes_read=r.bytes_read, header_bytes_read=r.header_bytes_read, file_size=r.file_size,
            current_offset=r.last_read_offset, confirmed_offset=r.last_confirmed_offset,
            pending_bytes=len(r.framer.pending) if r.framer else 0,
            packets_decoded=sum(d.decoded.values()) if d else 0,
            incomplete_packets=r.framer.incomplete if r.framer else 0,
            unknown_packets=dict(d.unknown) if d else {}, rejected_packets=dict(d.unsupported) if d else {},
            duplicate_packets=seq.duplicates + (r.framer.duplicates if r.framer else 0),
            resync_count=r.framer.resync if r.framer else 0,
            rejected_bytes=r.framer.rejected if r.framer else 0,
            timestamp_disorder=seq.disorder, late_events=seq.late, provisional_packets=len(seq.queue),
            queue_pressure=seq.pressure, state_corrections=self.reducer.corrections if self.reducer else 0,
            entity_evictions=(self.reducer.entities.evictions + d.actor_evictions) if self.reducer else 0,
            unverified_events=self.reducer.unverified_events if self.reducer else 0,
            live_latency_ms=max(0, (now - self.last_frame_at) * 1000),
            latency_note='Time since last record; recorder-to-reader delay is not observable')
        diag['journal_error'] = self.journal.error if self.journal else ''
        diag['unmapped_variables'] = {'0x%04x:%d' % key: value for key, value in d.unmapped_vars.items()} if d else {}
        warnings = bool(self.error or diag['journal_error'] or diag['unknown_packets'] or diag['rejected_packets'] or diag['resync_count'] or
                        diag['late_events'] or diag['unverified_events'] or diag['queue_pressure'] or diag['entity_evictions'])
        quality = '일부 데이터 불확실' if warnings else ('데이터 확인 중' if d is None or diag['pending_bytes'] or seq.queue else '정상')
        self.revision += 1
        stage = 'FINAL' if self.status == 'FINISHED' else ('CONFIRMED' if self.packet_count else 'PROVISIONAL')
        payload = dict(view, **getattr(self, 'metadata', {}), session_id=r.session_id, revision=self.revision,
            stage=stage, state=self.status, quality=quality, diagnostics=diag,
            events=list(self.reducer.recent) if self.reducer else [], accuracy=self.accuracy,
            file=os.path.basename(r.path), error=self.error,
            recovered=getattr(self, 'recovered', False),
            final_detection='inactivity heuristic; recording may resume',
            unsupported=['AP packet mapping', 'cast cancel / post delay',
                         'equipment change packet mapping'])
        payload['combat_available'] = view.get('combat_available', False)
        payload['skill_damage_disabled'] = bool(d and d.unknown.get(0x01de))
        payload['coverage_note'] = ('스킬 피해 후보 패킷이 제외됨: 설정 > 실험적 패킷 해석에서 활성화 가능 (미검증 △)'
            if payload['skill_damage_disabled'] else '미수신/미지원 값은 게임 내 0을 뜻하지 않습니다.')
        self.published = LiveSnapshot(r.session_id, self.revision, stage, json.dumps(payload, ensure_ascii=False))
        return self.published

    def _add_profit(self, view, state):
        from core.analysis import unit_price
        if not view.get('loot_available') or self.price_lookup is None:
            return
        gains = view['metrics']['loot']
        losses = {k: v - (state.inventory_last or {}).get(k, 0) for k, v in (state.inventory_first or {}).items()
                  if v > (state.inventory_last or {}).get(k, 0)}
        ids = set(gains) | set(losses)
        key = tuple(sorted(ids))
        if key != self._price_key:
            try:
                self._prices = self.price_lookup({i: self.gamedata.items.name(i) for i in ids}) if ids else {}
            except Exception:
                log.exception('LIVE inventory valuation failed')
                self._prices = {}
            self._price_key = key
        units = {i: unit_price(self._prices.get(i), self.settings.get('profit_basis', 'lowest')) for i in ids}
        missing = [i for i, value in units.items() if value is None]
        view['unpriced_items'] = missing
        if missing:
            view['profit_note'] = '시세 미확인 아이템이 있어 수익 미확정'
            return
        value = sum(qty * units[i] for i, qty in gains.items()) - sum(qty * units[i] for i, qty in losses.items())
        zeny = view['metrics'].get('zeny')
        view['profit'] = value + (zeny or 0)
        view['profit_per_hour'] = view['profit'] * 3600000 / state.clock if state.clock else None
        view['profit_note'] = '인벤토리 순증감 × 로컬 시세 (판매/창고 이동 포함 가능)' + (' · Zeny 미확인' if zeny is None else '')
