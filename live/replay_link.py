"""Compatibility facade around the dedicated incremental Replay session."""
import glob
import logging
import os
import time
from live.replay_engine import ReplayLiveSession

log = logging.getLogger('parser')

class ReplayLink:
    def __init__(self, settings, gamedata, journal=None, price_lookup=None):
        self.s, self.gamedata, self.journal = settings, gamedata, journal
        self.path = None
        self.active = False
        self.error = ''
        self.session = None
        self._last_scan = 0
        self._seen = {}
        self.price_lookup = price_lookup
        self._recovery_checked = False

    def poll(self, now=None):
        now = time.time() if now is None else now
        previous = self.active
        mono = time.monotonic()
        if not self._recovery_checked and self.journal and self.journal.ready.is_set():
            self._recovery_checked = True
            previous_session = self.journal.recovery()
            if previous_session and os.path.isfile(previous_session['path']):
                self.path = previous_session['path']
                self.session = ReplayLiveSession(self.path, self.s, self.gamedata, self.journal, self.price_lookup)
        if mono - self._last_scan >= 1 or self.session is None:
            self._last_scan = mono
            folder = self.s.get('replay_dir')
            candidates = []
            if folder and os.path.isdir(folder):
                for path in glob.glob(os.path.join(folder, '*.rrf')):
                    try:
                        stat = os.stat(path)
                        sig = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                        changed = path in self._seen and self._seen[path] != sig
                        self._seen[path] = sig
                        if changed or now - stat.st_mtime < 30:
                            candidates.append((stat.st_mtime, path))
                    except OSError:
                        pass
            if candidates:
                path = max(candidates)[1]
                if path != self.path:
                    if self.session and self.session.status != 'FINISHED':
                        try:
                            self.session.finalize()
                        except Exception:
                            log.exception('Previous Replay finalization failed')
                    self.path = path
                    self.session = ReplayLiveSession(path, self.s, self.gamedata, self.journal, self.price_lookup)
        if self.session:
            try:
                self.session.step()
                self.error = self.session.error
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                self.session.error = self.error
                self.session.status = 'STALLED'
                self.session.publish(force=True)
            self.active = self.session.status != 'FINISHED'
        return 'started' if self.active and not previous else ('stopped' if previous and not self.active else None)

    def snapshot(self):
        snap = self.session.published if self.session else None
        return snap.to_dict() if snap else None

    def analyze(self, session_start=None):
        """Detached compatibility view, without reparsing or replaying old deltas."""
        view = self.snapshot()
        if not view:
            return None, []
        metrics = view.get('metrics', {})
        return dict(view, kills=metrics.get('kills'), damage=metrics.get('damage'), dps=view.get('combat_dps')), []
