import helpers
import io,tempfile,unittest,urllib.error,json
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np,cv2
import services.item_icons as mod

class ItemIconCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.old=(mod._LAST_REQUEST,mod._RATE_BLOCKED)
        mod._LAST_REQUEST=0;mod._RATE_BLOCKED=False
        self.addCleanup(self.restore)
    def restore(self):mod._LAST_REQUEST,mod._RATE_BLOCKED=self.old
    def png(self,h=24,w=24):return cv2.imencode('.png',np.full((h,w,4),255,np.uint8))[1].tobytes()
    def cache(self,opener):
        c=mod.ItemIconCache(self.root,opener=opener)
        c._wanted={1,2};c._stop.wait=Mock(return_value=False)
        return c
    def test_allowlist_and_cached_icon(self):
        opener=Mock(side_effect=lambda *a,**k:io.BytesIO(self.png()))
        c=self.cache(opener);c._fetch(99);self.assertEqual(opener.call_count,0)
        c._fetch(1);c._fetch(1)
        self.assertEqual(opener.call_count,1);self.assertTrue(c.path(1));self.assertIsNone(c.path('../1'))
    def test_official_illustration_is_not_inventory_template(self):
        opener=Mock(side_effect=[io.BytesIO(self.png(100,75)),io.BytesIO(self.png())])
        c=self.cache(opener);c._fetch(1)
        self.assertTrue(c.path(1));self.assertTrue((self.root/'illustrations/1.png').exists())
        self.assertEqual(c.sources['1']['official'],'illustration')
    def test_404_is_persisted_per_provider(self):
        opener=Mock(side_effect=urllib.error.HTTPError('url',404,'missing',None,None))
        c=self.cache(opener);c._fetch(1);self.assertEqual(opener.call_count,3)
        again=self.cache(opener);again._fetch(1)
        self.assertEqual(opener.call_count,3);self.assertIn(1,again.missing)
    def test_429_stops_all_instances(self):
        opener=Mock(side_effect=urllib.error.HTTPError('url',429,'rate',None,None))
        c=self.cache(opener);c._fetch(1)
        again=self.cache(opener);again._fetch(2)
        self.assertEqual(opener.call_count,1);self.assertTrue(c.blocked);self.assertTrue(again.blocked)
    def test_request_spacing_is_shared_between_instances(self):
        opener=Mock(side_effect=lambda *a,**k:io.BytesIO(self.png()))
        a=self.cache(opener);b=self.cache(opener)
        with patch.object(mod.time,'monotonic',return_value=100):
            a._fetch(1);b._fetch(2)
        self.assertGreaterEqual(b._stop.wait.call_args.args[0],2)
    def test_bad_response_not_saved(self):
        c=self.cache(Mock(side_effect=lambda *a,**k:io.BytesIO(b'<html>error</html>')))
        c._fetch(1);self.assertIsNone(c.path(1))

if __name__=='__main__':unittest.main()
