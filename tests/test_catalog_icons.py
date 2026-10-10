"""스킬·상태 아이콘 받기: 없는 것만, 기록된 sha256 과 같은 것만 저장한다."""
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error

import helpers  # noqa: F401  (sys.path 설정)

from core.icon_catalog import bundled_icon  # noqa: E402
from services.catalog_icons import CatalogIconFetcher, missing  # noqa: E402

GOOD, HAVE = b'good icon', b'already here'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class CatalogIconsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        os.makedirs(os.path.join(self.dir, 'icons', 'catalog'))
        with open(os.path.join(self.dir, 'icons', 'catalog', 'skill_2.png'), 'wb') as f:
            f.write(HAVE)
        self.catalog({
            'skill_1': dict(path='icons/catalog/skill_1.png', url='https://a/1.png', sha256=sha(GOOD)),
            'skill_2': dict(path='icons/catalog/skill_2.png', url='https://a/2.png', sha256=sha(HAVE)),
            'skill_3': dict(path='icons/catalog/skill_3.png', url='https://a/3.png', sha256=sha(b'expected')),
            'skill_4': dict(path='../outside.png', url='https://a/4.png', sha256=sha(GOOD)),
            'skill_5': dict(path='icons/catalog/skill_5.png', url='http://a/5.png', sha256=sha(GOOD)),
        })
        self.calls = []

    def catalog(self, obj):
        with open(os.path.join(self.dir, 'icon_catalog.json'), 'w', encoding='utf-8') as f:
            json.dump(obj, f)

    def opener(self, req, timeout=None):
        self.calls.append(req.full_url)
        return io.BytesIO(GOOD)                                    # 3번은 기록과 다른 내용이 온다

    def test_only_missing_safe_entries_are_listed(self):
        self.assertEqual(sorted(u for _, u, _ in missing(self.dir)), ['https://a/1.png', 'https://a/3.png'])

    def test_saves_verified_and_drops_mismatched(self):
        f = CatalogIconFetcher(self.dir, self.opener, interval=0)
        f.run()
        self.assertEqual((f.saved, f.failed), (1, 1))
        self.assertTrue(bundled_icon('skill_1', self.dir))         # 받은 뒤에는 화면이 찾는다
        self.assertIsNone(bundled_icon('skill_3', self.dir))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.dir), 'outside.png')))
        f.run()
        self.assertEqual(self.calls.count('https://a/1.png'), 1)   # 받은 것은 다시 받지 않는다

    def test_rate_limit_stops_this_run(self):
        def blocked(req, timeout=None):
            self.calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 429, 'Too Many', None, None)
        CatalogIconFetcher(self.dir, blocked, interval=0).run()
        self.assertEqual(len(self.calls), 1)

    def test_no_catalog_is_fine(self):
        os.remove(os.path.join(self.dir, 'icon_catalog.json'))
        self.assertEqual(missing(self.dir), [])


if __name__ == '__main__':
    unittest.main()
