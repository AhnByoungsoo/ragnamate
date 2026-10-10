"""시세 조회 서비스: 중복 제거 → 캐시 우선 → 필요한 것만 Provider 조회 → 캐시 저장.

원격 Provider는 동시 요청 수와 요청 간격을 제한해서 외부 서버에 부담을 주지 않는다.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.price_provider import create_provider

log = logging.getLogger(__name__)


class _RateLimiter:
    def __init__(self, interval):
        self.interval, self._next, self._lock = interval, 0.0, threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class MarketService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.provider = create_provider(settings['provider'], db, settings)

    def set_provider(self, key):
        self.provider = create_provider(key, self.db, self.settings)

    @property
    def max_age(self):
        return max(0, float(self.settings['cache_minutes'])) * 60

    def cached(self, item_ids):
        try:
            return self.db.get_cached(set(item_ids), self.provider.key, self.max_age)
        except Exception:
            log.exception('캐시 조회 실패')
            return {}

    def fetch(self, items, force=False, progress=None, cancel=None):
        """items: {item_id: name}. 반환: ({item_id: PriceInfo}, 실패 item_id 목록)"""
        ids = set(items)
        result = {} if force else self.cached(ids)
        todo = [i for i in ids if i not in result]
        failed, done = [], 0
        if progress:
            progress(0, len(todo))
        if not todo:
            return result, failed

        remote = self.provider.is_remote
        limiter = _RateLimiter(float(self.settings['request_interval'])) if remote else None
        workers = max(1, int(self.settings['max_concurrency'])) if remote else 4

        def one(iid):
            if cancel and cancel.is_set():
                return iid, None
            if limiter:
                limiter.wait()
            return iid, self.provider.get_price(iid, items[iid])

        batch = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(one, i): i for i in todo}
            for fut in as_completed(futures):
                try:
                    iid, info = fut.result()
                    if info is not None:
                        result[iid] = info
                        batch.append(info)
                except Exception:
                    log.exception('시세 조회 실패: item_id=%s', futures[fut])
                    failed.append(futures[fut])
                done += 1
                if progress and (done % 20 == 0 or done == len(todo)):
                    progress(done, len(todo))
                if len(batch) >= 100:
                    self._store(batch)
                    batch = []
        self._store(batch)
        return result, failed

    def _store(self, infos):
        if not infos:
            return
        try:
            self.db.put_cache(infos, self.provider.key)
        except Exception:
            log.exception('캐시 저장 실패')

    def invalidate(self, item_ids=None):
        self.db.invalidate_cache(item_ids)
