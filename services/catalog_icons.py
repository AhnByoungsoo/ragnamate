"""스킬·상태 아이콘 받기: 아이콘 파일은 프로그램과 함께 배포하지 않고, 기기마다 기록된 출처에서 받아 둔다.

data/icon_catalog.json 에 아이콘마다 출처 주소와 sha256 이 적혀 있다. 없는 파일만 뒤에서 차례로 받고,
내용이 기록과 다르면 버린다(다른 그림으로 대신 채우지 않는다). 받는 동안은 종류별 기본 아이콘이 보인다.
"""
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request

MAX_BYTES = 256 * 1024
INTERVAL = 0.5


def missing(data_dir):
    """아직 없는 아이콘의 (저장할 경로, 주소, sha256) 목록."""
    try:
        with open(os.path.join(data_dir, 'icon_catalog.json'), encoding='utf-8') as f:
            catalog = json.load(f)
    except (OSError, ValueError):
        return []
    root, out = os.path.realpath(data_dir), []
    for meta in catalog.values() if isinstance(catalog, dict) else ():
        rel, url, digest = (meta.get(k) for k in ('path', 'url', 'sha256')) if isinstance(meta, dict) else (None,) * 3
        if not (isinstance(rel, str) and isinstance(url, str) and digest) or os.path.isabs(rel) or not url.startswith('https://'):
            continue
        path = os.path.realpath(os.path.join(root, rel))
        if path.startswith(root + os.sep) and not os.path.isfile(path):
            out.append((path, url, digest))
    return out


class CatalogIconFetcher:
    def __init__(self, data_dir, opener=None, interval=INTERVAL):
        self.data_dir, self.opener, self.interval = data_dir, opener or urllib.request.urlopen, interval
        self.saved = self.failed = 0
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self.run, name='catalog-icons', daemon=True).start()

    def stop(self):
        self._stop.set()

    def run(self):
        for path, url, digest in missing(self.data_dir):
            if self._stop.is_set():
                return
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'RagnaMate personal icon cache'})
                with self.opener(req, timeout=8) as response:
                    payload = response.read(MAX_BYTES + 1)
            except urllib.error.HTTPError as e:
                if e.code == 429:                                  # 요청 제한 — 이번 실행에서는 그만 받는다
                    return
                self.failed += 1
            except Exception:
                self.failed += 1
            else:
                if len(payload) <= MAX_BYTES and hashlib.sha256(payload).hexdigest() == digest:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path + '.tmp', 'wb') as f:
                        f.write(payload)
                    os.replace(path + '.tmp', path)
                    self.saved += 1
                else:
                    self.failed += 1
            self._stop.wait(self.interval)
