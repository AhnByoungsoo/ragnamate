import helpers
import unittest,tempfile
from pathlib import Path
import cv2,numpy as np
from live.storage_detect import StorageDetector,StorageRecognizer,IconMatcher

class StorageDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);rng=np.random.default_rng(412)
        self.title=rng.integers(0,255,(14,128),dtype=np.uint8)
        self.footer=rng.integers(0,255,(20,54),dtype=np.uint8)
        self.profile=self.root/'profile.npz'
        np.savez(self.profile,title=self.title,footer=self.footer,geometry=[284,5,4,223,651,676])
        self.window=np.full((676,284,3),255,np.uint8)
        self.window[4:18,5:133]=self.title[:,:,None]
        self.window[651:671,223:277]=self.footer[:,:,None]
        for y in (53,85,117):cv2.putText(self.window,'sample',(82,y+8),cv2.FONT_HERSHEY_SIMPLEX,.35,(0,0,0),1)
        self.icon=np.zeros((24,24,4),np.uint8)
        self.icon[3:20,4:20,:3]=rng.integers(20,230,(17,16,3),dtype=np.uint8)
        self.icon[3:20,4:20,3]=255
        cv2.imencode('.png',self.icon)[1].tofile(self.root/'1.png')
        self.c=[dict(item_id=1,name='test',price=None,updated_at=None)]

    def canvas(self,x=100,y=30):
        out=np.zeros((800,700,3),np.uint8);out[y:y+676,x:x+284]=self.window;return out

    def test_open_move_close_and_cache(self):
        d=StorageDetector(self.profile)
        first=d.detect(self.canvas());self.assertEqual(first.rect,(100,30,284,676))
        self.assertEqual(len(first.slots),3)
        self.assertTrue(d.detect(self.canvas()).cached)
        self.assertEqual(d.detect(self.canvas(210,60)).rect,(210,60,284,676))
        self.assertIsNone(d.detect(np.zeros((800,700,3),np.uint8)).rect)

    def test_title_alone_does_not_show_prices(self):
        self.window[651:671,223:277]=255
        self.assertIsNone(StorageDetector(self.profile).detect(self.canvas()).rect)

    def test_scroll_invalidates_slots(self):
        d=StorageDetector(self.profile);d.detect(self.canvas())
        self.window[42:160,80:260]=255
        cv2.putText(self.window,'changed',(82,190),cv2.FONT_HERSHEY_SIMPLEX,.35,(0,0,0),1)
        r=d.detect(self.canvas());self.assertFalse(r.cached);self.assertEqual(len(r.slots),1)

    def test_quantity_digits_ignored_and_unknown_rejected(self):
        crop=np.full((38,50,3),255,np.uint8)
        icon=np.where(self.icon[:,:,3:4]>0,self.icon[:,:,:3],255)
        crop[4:28,10:34]=icon;crop[20:28,10:34]=0
        m=IconMatcher(self.c,self.root)
        self.assertEqual(m.match(crop)['item']['item_id'],1)
        self.assertIsNone(m.match(np.zeros_like(crop))['item'])

    def test_identical_icons_abstain(self):
        (self.root/'2.png').write_bytes((self.root/'1.png').read_bytes())
        crop=np.where(self.icon[:,:,3:4]>0,self.icon[:,:,:3],255).astype(np.uint8)
        m=IconMatcher(self.c+[dict(self.c[0],item_id=2)],self.root)
        r=m.match(crop);self.assertIsNone(r['item']);self.assertEqual(r['margin'],0)

    def test_candidate_allowlist_and_cache_version(self):
        r=StorageRecognizer(self.profile)
        first=r.process(self.canvas(),self.c,self.root)
        self.assertTrue(all(x['item'] is None for x in first.slots))
        next_=r.process(self.canvas(),[],self.root,1)
        self.assertTrue(all(x['candidates']==[] for x in next_.slots))

    def test_grid_uses_real_borders(self):
        crop=np.full((676,284,3),255,np.uint8)
        for y in (60,104):
            for x in (30,74,118):cv2.rectangle(crop,(x,y),(x+39,y+39),(50,50,50),1)
        self.assertEqual(len(StorageDetector._grid_slots(crop,1)),6)

if __name__=='__main__':unittest.main()
