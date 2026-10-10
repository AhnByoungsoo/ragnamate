"""LocalPriceProvider: SQLite local_listings 테이블(직접 수집/입력한 시세)을 사용.

데이터 넣는 방법
  - CSV 가져오기: item_id,price[,quantity][,kind]  (kind: sale|buy, 기본 sale)
  - 프로그램에서 가격 칸 더블클릭 → 수동 입력
  - 이전 버전의 price_cache.json(노점 스냅샷)은 최초 실행 시 자동 이관
"""
import csv
import json
import logging
import os
import time

from core.models import Listing
from services.price_provider.base import PriceProvider

log = logging.getLogger(__name__)


class LocalPriceProvider(PriceProvider):
    key = 'local'
    label = '로컬 시세 DB'

    def get_market_listings(self, item_id, name=None):
        return self.db.local_listings(item_id)

    def get_last_updated(self, item_id):
        return self.db.local_last_updated(item_id)

    def set_manual_price(self, item_id, price, quantity=1):
        self.db.add_local_listings([(item_id, Listing(int(price), int(quantity), 'sale', '수동 입력', time.time()))],
                                   source='manual')

    def import_csv(self, path):
        """반환: (가져온 item_id 집합, 건너뛴 줄 수)"""
        rows, skipped = [], 0
        with open(path, encoding='utf-8-sig', newline='') as f:
            for rec in csv.reader(f):
                try:
                    iid, price = int(rec[0]), int(str(rec[1]).replace(',', ''))
                    qty = int(rec[2]) if len(rec) > 2 and rec[2].strip() else 1
                    kind = rec[3].strip() if len(rec) > 3 and rec[3].strip() in ('sale', 'buy') else 'sale'
                    rows.append((iid, Listing(price, qty, kind, 'CSV', time.time())))
                except (ValueError, IndexError):
                    skipped += 1   # 헤더/빈 줄 등
        if rows:
            self.db.add_local_listings(rows, source='csv:' + os.path.basename(path))
        return {iid for iid, _ in rows}, skipped

    def import_legacy_cache(self, path):
        """이전 버전 price_cache.json {'서버:id': {t, sales, buys}} 이관 (1회)."""
        if not os.path.exists(path) or self.db.get_meta('legacy_imported'):
            return 0
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            rows = []
            for key, v in data.items():
                iid = int(key.split(':')[1])
                for kind, lst in (('sale', v.get('sales', [])), ('buy', v.get('buys', []))):
                    rows += [(iid, Listing(r['price'], r.get('qty', 1), kind, r.get('shop', ''), v['t']))
                             for r in lst]
            if rows:
                self.db.add_local_listings(rows, source='legacy_snapshot')
            self.db.set_meta('legacy_imported', time.time())
            log.info('이전 시세 캐시 이관: %d건', len(rows))
            return len(rows)
        except Exception:
            log.exception('이전 시세 캐시 이관 실패')
            return 0
