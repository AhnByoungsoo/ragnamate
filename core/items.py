"""Item DB 계층: item_id → 이름/슬롯/설명/분류.

클라이언트 itemInfo_true.lub에서 만들어 data/item_db.json에 캐시한다.
lub이 갱신(패치)되면 자동으로 다시 만든다. Replay 파서와는 독립적.
"""
import json
import logging
import os
import re
import hashlib

import iteminfo
from core.identifiers import numeric_id

log = logging.getLogger(__name__)
_COLOR = re.compile(r'\^[0-9a-fA-F]{6}')


class ItemDB:
    def __init__(self, ro_dir, cache_path):
        self.lub_path = os.path.join(ro_dir, 'System', 'itemInfo_true.lub')
        self.cache_path = cache_path
        self.items = {}
        self.categories = {}   # Replay에서 관찰된 분류(패킷 타입) 누적
        self.fingerprint = None

    def _fingerprint(self):
        if not os.path.isfile(self.lub_path):
            return None
        digest = hashlib.sha256()
        with open(self.lub_path, 'rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                digest.update(block)
        return dict(path=os.path.normcase(os.path.abspath(self.lub_path)), sha256=digest.hexdigest())

    def load(self):
        lub_mtime = os.path.getmtime(self.lub_path) if os.path.exists(self.lub_path) else 0
        self.fingerprint = self._fingerprint()
        try:
            if os.path.exists(self.cache_path):
                with open(self.cache_path, encoding='utf-8') as f:
                    data = json.load(f)
                if (self.fingerprint and data.get('fingerprint') == self.fingerprint) or not self.fingerprint:
                    self.items = {int(k): v for k, v in data['items'].items()}
                    self.categories = {int(k): v for k, v in data.get('categories', {}).items()}
                    return self
        except Exception:
            log.exception('item_db.json 읽기 실패, 다시 생성')
        if not lub_mtime:
            log.warning('itemInfo_true.lub 없음: %s', self.lub_path)
            return self
        log.info('Item DB 생성: %s', self.lub_path)
        self.items = iteminfo.load_items(self.lub_path)
        for v in self.items.values():
            v['description'] = _COLOR.sub('', v['description'])
        self._save(lub_mtime)
        return self

    def _save(self, lub_mtime=None):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        if lub_mtime is None:
            lub_mtime = os.path.getmtime(self.lub_path) if os.path.exists(self.lub_path) else 0
        with open(self.cache_path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(dict(lub_mtime=lub_mtime, fingerprint=self.fingerprint, items=self.items, categories=self.categories),
                      f, ensure_ascii=False)
        os.replace(self.cache_path + '.tmp', self.cache_path)

    def name(self, item_id):
        item_id = numeric_id(item_id)
        if item_id is None:
            return '아이템 ID 확인 안 됨'
        it = self.items.get(item_id)
        return it['name'] if it and it['name'] else '알 수 없는 아이템 #%d' % item_id

    def get(self, item_id):
        """{item_id, name, category, slot, description} 또는 None"""
        item_id = numeric_id(item_id)
        it = self.items.get(item_id)
        if not it:
            return None
        return dict(item_id=item_id, name=it['name'], category=self.categories.get(item_id),
                    slot=it['slot'], description=it['description'])

    def known(self, item_id):
        it = self.items.get(numeric_id(item_id))
        return bool(it and it.get('name'))

    def remember_categories(self, mapping):
        new = {k: v for k, v in mapping.items() if v and self.categories.get(k) != v}
        if new:
            self.categories.update(new)
            try:
                self._save()
            except Exception:
                log.exception('Item DB 분류 저장 실패')
