"""ReplayFeedSimulator: binary chunks supplied to an actual growing file.

python -m live.simulator --fixture-dir C:\\path\\to\\Replay --runs 100 --output report.json
Private Replay files remain outside this repository.
"""
import argparse
import json
import os
import random
import tempfile
import time
from pathlib import Path

from analyzer.normalizer import normalize
from live.replay_engine import ReplayLiveSession, compare_metrics, offline_metrics
from rrf import Replay


class ReplayFeedSimulator:
    def __init__(self, data, gamedata, settings=None):
        self.data, self.gamedata = data, gamedata
        self.settings = dict(settings or {}, live_finalize_sec=10**9, live_snapshot_ms=10**9)

    def run(self, seed=0, chunks=None, duplicate=False):
        rng = random.Random(seed)
        sizes = iter(chunks) if chunks is not None else None
        with tempfile.TemporaryDirectory(prefix='replay_feed_') as folder:
            path = Path(folder) / 'feed.rrf'
            path.touch()
            live = ReplayLiveSession(path, self.settings, self.gamedata)
            now, pos = 1.0, 0
            while pos < len(self.data):
                n = next(sizes, 2048) if sizes is not None else rng.randint(1, 4096)
                if n <= 0:
                    raise ValueError('Chunk sizes must be positive')
                with path.open('ab') as f:
                    f.write(self.data[pos:pos + n])
                pos += n
                now += .001
                live.step(now)
                if duplicate:
                    live.step(now)
            while live.reader.replay and not live.reader.caught_up:
                now += .001
                live.step(now)
            if live.decoder:
                live._consume(live.sequencer.drain(final=True))
            actual = live.reducer.metrics() if live.reducer else {}
            # Offline receives a separate immutable full copy through its existing entry point.
            pr = normalize(Replay(path), str(path), self.settings.get('experimental_packets', False))
            expected = offline_metrics(pr, self.gamedata, self.settings)
            comparison = compare_metrics(actual, expected)
            comparison['bytes_read'] = live.reader.bytes_read
            comparison['header_bytes_read'] = live.reader.header_bytes_read
            comparison['file_bytes'] = len(self.data)
            comparison['packets'] = live.packet_count
            comparison['pending'] = len(live.reader.framer.pending) if live.reader.framer else None
            comparison['late'] = live.sequencer.late
            comparison['unknown'] = dict(live.decoder.unknown) if live.decoder else {}
            return comparison


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fixture-dir', default=os.environ.get('RO_REPLAY_FIXTURES'))
    ap.add_argument('--runs', type=int, default=100)
    ap.add_argument('--output', default='live_accuracy.json')
    ap.add_argument('--experimental', action='store_true')
    args = ap.parse_args()
    if not args.fixture_dir:
        ap.error('Set --fixture-dir or RO_REPLAY_FIXTURES')
    from analyzer.gamedata import GameDataProvider
    from core.items import ItemDB
    from config.settings import Settings, BASE_DIR
    settings = Settings()
    settings['experimental_packets'] = args.experimental
    gd = GameDataProvider(ItemDB(settings['ro_dir'], os.path.join(BASE_DIR, 'data', 'item_db.json')),
                          settings['ro_dir'], os.path.join(BASE_DIR, 'data'))
    results = []
    started = time.perf_counter()
    for path in sorted(Path(args.fixture_dir).glob('*.rrf')):
        sim = ReplayFeedSimulator(path.read_bytes(), gd, settings)
        failed, result = [], None
        for seed in range(args.runs):
            result = sim.run(seed, duplicate=True)
            if not result['equal'] or result['pending']:
                failed.append(dict(seed=seed, result=result))
        results.append(dict(file=path.name, runs=args.runs, failed=failed, last=result))
        print('%s: %d/%d matched' % (path.name, args.runs - len(failed), args.runs), flush=True)
    out = dict(elapsed_seconds=time.perf_counter() - started, fixtures=results,
               equal=bool(results) and all(not r['failed'] for r in results))
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if out['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
