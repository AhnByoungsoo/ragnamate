"""전체 시세 받기: 가진 아이템의 공식 노점 시세를 뒤에서 차례로 받아 온다.

받는 대로 저장되므로 중간에 멈추거나 앱을 꺼도 다음에 이어서 받는다(30분 안에 받은 것은 건너뛴다).
요청은 앱 전체가 같이 쓰는 간격·429 규칙을 따른다. 막히면 풀릴 때까지 기다렸다가 이어 간다.
"""
import logging
import threading

from PySide6.QtCore import QObject, Signal

from services import gnjoy_market as G

log = logging.getLogger(__name__)
RETRY_SEC = 20            # 요청이 막혔을 때 풀렸는지 다시 보는 간격


def order(entries, npc_price=None):
    """받을 순서: [(아이템 번호, 이름)] — 수량 × 상점가가 큰 것부터(상점가를 모르면 수량만)."""
    total = {}
    for e in entries:
        cur = total.setdefault(e.item_id, [e.name, 0])
        cur[1] += e.quantity

    def weight(item):
        item_id, (_name, qty) = item
        return qty * ((npc_price(item_id) if npc_price else None) or 1)
    return [(i, v[0]) for i, v in sorted(total.items(), key=weight, reverse=True)]


class BulkPriceFetcher(QObject):
    progress = Signal(int, int, str)      # 끝낸 수, 전체, 방금 받은 이름
    waiting = Signal(str)                 # 요청이 막혀 기다리는 중
    finished = Signal(int, int, bool)     # 새로 받은 수, 건너뛴 수(최근에 받음), 끝까지 갔는가

    def __init__(self, pricer, parent=None, retry_sec=RETRY_SEC):
        super().__init__(parent)
        self.pricer, self.retry_sec = pricer, retry_sec
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, items):
        if self.running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, args=(list(items),), daemon=True, name='BulkPrices')
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def run(self, items):
        fetched = skipped = 0
        for n, (item_id, name) in enumerate(items, 1):
            if self._stop.is_set():
                self.finished.emit(fetched, skipped, False)
                return
            try:
                if self.pricer.fresh(item_id):
                    skipped += 1
                else:
                    while not self._fetch(item_id, name):
                        if self._stop.wait(self.retry_sec):
                            self.finished.emit(fetched, skipped, False)
                            return
                    fetched += 1
            except Exception:
                log.exception('시세 받기 실패: %s', name)
            self.progress.emit(n, len(items), name)
        self.finished.emit(fetched, skipped, True)

    def _fetch(self, item_id, name):
        try:
            self.pricer.fetch(item_id, name)
            return True
        except G.RateLimited as exc:
            self.waiting.emit(str(exc))
            return False
