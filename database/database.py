"""SQLite: 시세 캐시 + 로컬 시세(노점 관찰/수동 입력) 저장소."""
import logging
import os
import sqlite3
import threading
import time

from core.models import Listing, PriceInfo
from database.migrations import migrate

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_price_cache (
    item_id       INTEGER NOT NULL,
    variant       TEXT    NOT NULL DEFAULT '',   -- 장비 제련/카드/옵션 조합(향후 확장용)
    provider      TEXT    NOT NULL,
    lowest_price  INTEGER,
    median_price  INTEGER,
    average_price INTEGER,
    listing_count INTEGER NOT NULL DEFAULT 0,
    highest_buy   INTEGER,
    price_time    REAL,                           -- 시세 자체의 수집 시각
    updated_at    REAL    NOT NULL,               -- 캐시에 기록한 시각
    PRIMARY KEY (item_id, variant, provider)
);
CREATE TABLE IF NOT EXISTS local_listings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      INTEGER NOT NULL,
    variant      TEXT    NOT NULL DEFAULT '',
    price        INTEGER NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 1,
    kind         TEXT    NOT NULL DEFAULT 'sale',
    shop         TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL DEFAULT 'manual',
    collected_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_local_item ON local_listings(item_id, variant);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Database:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.schema_version = migrate(self.conn)

    def _exec(self, sql, args=(), many=False):
        with self._lock:
            cur = self.conn.executemany(sql, args) if many else self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _query(self, sql, args=()):
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    # ---- 캐시 ----
    def get_cached(self, item_ids, provider, max_age_sec, variant=''):
        """{item_id: PriceInfo} (유효기간 내 캐시만)"""
        out, limit = {}, time.time() - max_age_sec
        ids = list(item_ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self._query(
                'SELECT item_id, lowest_price, median_price, average_price, listing_count, price_time, highest_buy '
                'FROM market_price_cache WHERE provider=? AND variant=? AND updated_at>=? AND item_id IN (%s)'
                % ','.join('?' * len(chunk)), [provider, variant, limit] + chunk)
            for iid, lo, med, avg, cnt, pt, hb in rows:
                out[iid] = PriceInfo(iid, lo, med, avg, cnt, pt, provider, hb)
        return out

    def put_cache(self, infos, provider, variant=''):
        now = time.time()
        self._exec('INSERT OR REPLACE INTO market_price_cache VALUES (?,?,?,?,?,?,?,?,?,?)',
                   [(p.item_id, variant, provider, p.lowest_price, p.median_price, p.average_price,
                     p.listing_count, p.highest_buy, p.updated_at, now) for p in infos], many=True)

    def invalidate_cache(self, item_ids=None):
        if item_ids is None:
            self._exec('DELETE FROM market_price_cache')
        else:
            self._exec('DELETE FROM market_price_cache WHERE item_id=?', [(i,) for i in item_ids], many=True)

    # ---- 로컬 시세 ----
    def local_listings(self, item_id, variant=''):
        return [Listing(p, q, k, s, t) for p, q, k, s, t in self._query(
            'SELECT price, quantity, kind, shop, collected_at FROM local_listings WHERE item_id=? AND variant=?',
            (item_id, variant))]

    def local_last_updated(self, item_id, variant=''):
        r = self._query('SELECT MAX(collected_at) FROM local_listings WHERE item_id=? AND variant=?',
                        (item_id, variant))
        return r[0][0] if r else None

    def add_local_listings(self, rows, source, replace_items=True):
        """rows: [(item_id, Listing)]. replace_items면 같은 item_id의 기존 데이터를 교체."""
        with self._lock:
            if replace_items:
                self.conn.executemany('DELETE FROM local_listings WHERE item_id=? AND variant=\'\'',
                                      [(i,) for i in {iid for iid, _ in rows}])
            self.conn.executemany(
                'INSERT INTO local_listings(item_id, price, quantity, kind, shop, source, collected_at) '
                'VALUES (?,?,?,?,?,?,?)',
                [(iid, l.price, l.quantity, l.kind, l.shop, source, l.seen_at or time.time()) for iid, l in rows])
            self.conn.commit()

    def get_meta(self, key):
        r = self._query('SELECT value FROM meta WHERE key=?', (key,))
        return r[0][0] if r else None

    def set_meta(self, key, value):
        self._exec('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))
