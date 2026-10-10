"""Storage-only icon cache with recorded sources, shared pacing and persistent provider 404s."""
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

URL = 'https://imgc1.gnjoy.com/games/ro1/object/201306/{item_id}.png'
INVENTORY_URL = 'https://assets.latam-tools.com.br/icons/item/{item_id}.png'
FALLBACK_URL = 'https://static.divine-pride.net/images/items/item/{item_id}.png'
_NETWORK_LOCK = threading.Lock()
_FILES_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_RATE_BLOCKED = False


class ItemIconCache:
    def __init__(self, directory, opener=None, interval=2.1):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.opener = opener or urllib.request.urlopen
        self.interval = max(2.0, interval)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._wanted = set()
        self._last = 0.0
        self.version = 0
        self.status = ''
        self.blocked = False
        try:
            self.missing = set(json.loads((self.directory/'missing.json').read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError):
            self.missing = set()
        self._attempted = set()
        try:self.sources=json.loads((self.directory/'sources.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):self.sources={}
        # A new provider can fill previous misses without retrying the providers' recorded 404s.
        self.missing={i for i in self.missing if self.sources.get(str(i),{}).get('fallback')=='404'}
        # Older caches can contain official 75x100 collection art, which is not an inventory icon.
        import cv2
        import numpy as np
        for p in self.directory.glob('*.png'):
            if not p.stem.isdigit():continue
            image=cv2.imdecode(np.fromfile(p,np.uint8),cv2.IMREAD_UNCHANGED)
            if image is not None and max(image.shape[:2])>48:
                folder=self.directory/'illustrations';folder.mkdir(exist_ok=True)
                os.replace(p,folder/p.name)
                self.sources.setdefault(p.stem,{})['official']='illustration'
        self._save_sources()

    def _save_sources(self):
        with _FILES_LOCK:
            path=self.directory/'sources.json'
            try:merged=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):merged={}
            for ident,record in self.sources.items():merged.setdefault(ident,{}).update(record)
            tmp=self.directory/'sources.json.tmp'
            tmp.write_text(json.dumps(merged,ensure_ascii=False,indent=1),encoding='utf-8')
            os.replace(tmp,path)

    def path(self, item_id):
        if type(item_id) is not int or item_id <= 0:
            return None
        p = self.directory/f'{item_id}.png'
        return str(p) if p.is_file() else None

    def request(self, storage_item_ids):
        """Replace the allowlist; IDs must come from the selected replay storage snapshot."""
        with self._lock:
            self._wanted = {i for i in storage_item_ids if type(i) is int and i > 0}
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._run, name='storage-icons', daemon=True)
                self._thread.start()
        self._wake.set()

    def _fetch(self, ident):
        global _LAST_REQUEST
        record=self.sources.setdefault(str(ident),{})
        for kind,pattern in [('official',URL),('inventory',INVENTORY_URL),('fallback',FALLBACK_URL)]:
            if record.get(kind) in ('404','illustration'):continue
            # Shared across preview and overlay instances, including a worker still finishing stop().
            with _NETWORK_LOCK:
                if self._stop.wait(max(0,_LAST_REQUEST+self.interval-time.monotonic())):return
                with self._lock:
                    if ident not in self._wanted or self.blocked:return
                if _RATE_BLOCKED:
                    self.blocked=True;self.status='아이콘 서버 요청 제한(429): 앱을 다시 실행한 뒤 시도하세요.';return
                if self.path(ident):return
                _LAST_REQUEST=time.monotonic()
                if self._fetch_source(ident,kind,pattern.format(item_id=ident),record):return

    def _fetch_source(self,ident,kind,url,record):
        global _RATE_BLOCKED
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'RagnaMate personal icon cache'})
            with self.opener(req, timeout=8) as response:
                payload = response.read(256*1024+1)
            if len(payload)>256*1024 or not payload.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('PNG 이미지 응답이 아닙니다')
            import cv2
            import numpy as np
            image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_UNCHANGED)
            if image is None or not (8<=image.shape[0]<=128 and 8<=image.shape[1]<=128):
                raise ValueError('아이콘 크기/형식을 확인할 수 없습니다')
            if max(image.shape[:2])>48:
                record[kind]='illustration';self._save_sources()
                folder=self.directory/'illustrations';folder.mkdir(exist_ok=True)
                (folder/f'{ident}.png').write_bytes(payload)
                return False
            tmp=self.directory/f'{ident}.png.tmp'
            tmp.write_bytes(payload)
            os.replace(tmp,self.directory/f'{ident}.png')
            self.version += 1
            self.missing.discard(ident)
            self._save_missing()
            record.update({kind:'ok','url':url,'checked_at':time.time()});self._save_sources()
            self.status = f'아이콘 저장: {ident}'
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                record[kind]='404';self._save_sources()
                if all(record.get(k)=='404' for k in ('inventory','fallback')):
                    self.missing.add(ident)
                    self._save_missing()
            elif exc.code == 429:
                _RATE_BLOCKED=True
                self.blocked=True
                self.status='아이콘 서버 요청 제한(429): 이번 실행의 다운로드를 중단했습니다.'
            else:self.status=f'아이콘 {ident}: HTTP {exc.code}'
        except Exception as exc:
            self.status=f'아이콘 {ident} 받기 실패: {exc}'

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                todo=sorted(i for i in self._wanted if i not in self.missing and i not in self._attempted and not self.path(i))
            if not todo or self.blocked:
                self._wake.wait(.5);self._wake.clear();continue
            ident=todo[0]
            self._attempted.add(ident)
            self._fetch(ident)

    def stop(self):
        self._stop.set();self._wake.set()

    def _save_missing(self):
        with _FILES_LOCK:
            tmp=self.directory/'missing.json.tmp'
            tmp.write_text(json.dumps(sorted(self.missing)),encoding='utf-8')
            os.replace(tmp,self.directory/'missing.json')

    def ready(self, ids):
        return sum(bool(self.path(i)) for i in ids)
