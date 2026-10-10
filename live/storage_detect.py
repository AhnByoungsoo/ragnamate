"""Passive image-only storage localisation and conservative icon matching (physical pixels)."""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import time

import cv2
import numpy as np


def read_image(path):
    try:return cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_UNCHANGED)
    except (OSError,ValueError):return None


def digest(image):
    return hashlib.blake2b(np.ascontiguousarray(image).tobytes(),digest_size=12).hexdigest()


@dataclass
class StorageResult:
    rect: tuple = None
    scale: float = 1.0
    slots: list = field(default_factory=list)
    fingerprint: str = ''
    elapsed_ms: float = 0.0
    cached: bool = False
    reason: str = ''


class StorageDetector:
    def __init__(self, profile, threshold=.89):
        with np.load(profile,allow_pickle=False) as z:
            self.title=z['title'].copy();self.footer=z['footer'].copy()
            self.geometry=z['geometry'].astype(float)
        self.threshold=threshold
        self._last=None
        self._templates={}
        self._layout=None

    def locate(self, frame):
        if self._last and self._last.rect:
            x,y,w,h=self._last.rect
            if y+h<=frame.shape[0] and x+w<=frame.shape[1] and digest(frame[y:y+h,x:x+w,:3])==self._last.fingerprint:
                return self._last.rect,self._last.scale
        gray=cv2.cvtColor(frame[:,:,:3],cv2.COLOR_BGR2GRAY)
        # Previous scale first; 100/125/150/175/200% are included exactly.
        scales=[self._last.scale] if self._last and self._last.rect else []
        scales += [s for s in (.75,1.,1.25,1.5,1.75,2.) if s not in scales]
        twidth,title_x,title_y,footer_x,footer_y,base_h=self.geometry
        matches=[]
        for scale in scales:
            if scale not in self._templates:
                self._templates[scale]=tuple(cv2.resize(t,None,fx=scale,fy=scale,interpolation=cv2.INTER_LINEAR) for t in (self.title,self.footer))
            title,footer=self._templates[scale]
            if title.shape[0]>gray.shape[0] or title.shape[1]>gray.shape[1]:continue
            scores=cv2.matchTemplate(gray,title,cv2.TM_CCOEFF_NORMED)
            _,score,_,pos=cv2.minMaxLoc(scores)
            if score<self.threshold:continue
            x=round(pos[0]-title_x*scale);y=round(pos[1]-title_y*scale);width=round(twidth*scale)
            if min(x,y)<0 or x+width>gray.shape[1]:continue
            fx=round(x+footer_x*scale)
            # Search the footer down the same window column: storage height can change.
            xa=max(0,fx-round(5*scale));xb=min(gray.shape[1],fx+footer.shape[1]+round(5*scale))
            ya=y+round(140*scale)
            roi=gray[ya:,xa:xb]
            if roi.shape[0]<footer.shape[0] or roi.shape[1]<footer.shape[1]:continue
            _,bottom_score,_,bp=cv2.minMaxLoc(cv2.matchTemplate(roi,footer,cv2.TM_CCOEFF_NORMED))
            if bottom_score<self.threshold-.035:continue
            bottom=ya+bp[1]+round((base_h-footer_y)*scale)
            if gray.shape[0]<bottom<=gray.shape[0]+round(2*scale):bottom=gray.shape[0]
            if bottom>gray.shape[0] or bottom-y<round(180*scale):continue
            matches.append((min(score,bottom_score),(x,y,width,bottom-y),scale))
            if matches[-1][0]>.97:break
        if not matches:return None,1.
        matches.sort(reverse=True)
        return matches[0][1],matches[0][2]

    def detect(self, frame, layout='list'):
        started=time.perf_counter()
        if frame is None or frame.ndim!=3 or not frame.size:return StorageResult(reason='캡처 없음')
        rect,scale=self.locate(frame)
        if rect is None:
            self._last=None
            return StorageResult(reason='창고 창을 찾지 못했습니다',elapsed_ms=(time.perf_counter()-started)*1000)
        x,y,w,h=rect;crop=frame[y:y+h,x:x+w,:3];stamp=digest(crop)
        if self._last and self._last.fingerprint==stamp and self._last.scale==scale and self._layout==layout:
            result=StorageResult(rect,scale,self._last.slots,stamp,cached=True)
        else:
            slots=self._list_slots(crop,scale) if layout=='list' else self._grid_slots(crop,scale)
            result=StorageResult(rect,scale,slots,stamp,reason='' if slots else '보이는 아이템 없음 / 배치 확인 필요')
        result.elapsed_ms=(time.perf_counter()-started)*1000
        self._last=result
        self._layout=layout
        return result

    @staticmethod
    def _list_slots(crop,scale):
        h,w=crop.shape[:2]
        left,top,right,bottom=round(80*scale),round(42*scale),w-round(20*scale),h-round(62*scale)
        if left>=right or top>=bottom:return []
        gray=cv2.cvtColor(crop[top:bottom,left:right],cv2.COLOR_BGR2GRAY)
        active=np.count_nonzero(gray<170,axis=1)>=max(3,round(4*scale))
        # Join glyph rows separated by <=1 scaled pixel, but not adjacent item rows.
        active=cv2.morphologyEx(active.astype(np.uint8).reshape(-1,1),cv2.MORPH_CLOSE,np.ones((max(2,round(2*scale)),1),np.uint8)).ravel()
        runs=[];start=None
        for i,v in enumerate(np.r_[active,False]):
            if v and start is None:start=i
            if not v and start is not None:
                if 3*scale<=i-start<=20*scale:runs.append((top+start,top+i))
                start=None
        out=[]
        for a,b in runs:
            if out and a-out[-1]['text_y']<20*scale:continue
            sy=max(top,round(a-13*scale));ey=min(bottom,round(b+12*scale))
            out.append(dict(search=(round(28*scale),sy,round(50*scale),ey-sy),
                            label=(left,round(b+1*scale),max(30,right-left),round(13*scale)),text_y=a))
        return out

    @staticmethod
    def _grid_slots(crop,scale):
        """Only accept actually visible cell borders; never guess a screen/server index order."""
        h,w=crop.shape[:2];top=round(42*scale);bottom=h-round(62*scale)
        gray=cv2.cvtColor(crop[top:bottom],cv2.COLOR_BGR2GRAY)
        edges=cv2.Canny(gray,70,150)
        contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
        boxes=[]
        for c in contours:
            x,y,bw,bh=cv2.boundingRect(c)
            if not (28*scale<=bw<=65*scale and 28*scale<=bh<=65*scale and .7<bw/bh<1.4):continue
            if any(abs(x-a)<4*scale and abs(y-b)<4*scale for a,b,_,_ in boxes):continue
            boxes.append((x,y,bw,bh))
        boxes.sort(key=lambda b:(b[1],b[0]))
        return [dict(search=(x+2,top+y+2,bw-4,bh-4),label=(x,top+y+bh-round(13*scale),bw,round(12*scale))) for x,y,bw,bh in boxes]


class IconMatcher:
    def __init__(self, candidates, icon_dir, threshold=.91, margin=.055):
        self.threshold,self.margin=threshold,margin
        self.candidates=candidates
        self.templates=[]
        for candidate in candidates:
            image=read_image(Path(icon_dir)/f"{candidate['item_id']}.png")
            if image is None:continue
            rgb=image[:,:,:3] if image.ndim==3 else cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
            mask=(image[:,:,3]>80) if image.ndim==3 and image.shape[2]==4 else np.any(rgb<240,axis=2)
            mask &= ~((rgb[:,:,0]>235)&(rgb[:,:,1]<25)&(rgb[:,:,2]>235))
            if image.ndim==3 and image.shape[2]==4:
                alpha=image[:,:,3:4].astype(np.float32)/255
                rgb=(rgb*alpha+255*(1-alpha)).astype(np.uint8)
            # Stack quantities overlap the bottom of the game icon; don't identify from digits.
            mask[round(len(mask)*.60):]=False
            if np.count_nonzero(mask)<12:continue
            self.templates.append((candidate,rgb,mask.astype(np.uint8)))
        self._scaled={}

    def match(self, crop, scale=1.):
        if scale not in self._scaled:
            scaled=[]
            for item,img,mask in self.templates:
                size=(max(1,round(img.shape[1]*scale)),max(1,round(img.shape[0]*scale)))
                scaled.append((item,cv2.resize(img,size,interpolation=cv2.INTER_LINEAR),cv2.resize(mask,size,interpolation=cv2.INTER_NEAREST)))
            self._scaled[scale]=scaled
        ranks=[]
        for item,img,mask in self._scaled[scale]:
            if crop.shape[0]<img.shape[0] or crop.shape[1]<img.shape[1]:continue
            # Foreground-masked RGB error resists white cell backgrounds and stack counts.
            scores=cv2.matchTemplate(crop[:,:,:3],img,cv2.TM_SQDIFF,mask=mask)
            score=float(np.nanmin(scores))
            error=np.sqrt(max(0,score)/max(1,np.count_nonzero(mask)*3))
            ranks.append((max(0.,1-error/180.),item))
        ranks.sort(key=lambda p:p[0],reverse=True)
        best=ranks[0] if ranks else (0.,None)
        second=ranks[1][0] if len(ranks)>1 else 0.
        certain=best[0]>=self.threshold and best[0]-second>=self.margin
        return dict(item=best[1] if certain else None,score=round(best[0],4),margin=round(best[0]-second,4),
                    ambiguous=not certain,candidates=[p[1]['item_id'] for p in ranks[:2]])


class StorageRecognizer:
    def __init__(self, profile):
        self.detector=StorageDetector(profile)
        self.matcher=None;self._key=None;self._last=None

    def process(self,frame,candidates,icon_dir,version=0,layout='list'):
        key=(repr(candidates),version,layout)
        if key!=self._key:
            self.matcher=IconMatcher(candidates,icon_dir);self._key=key;self._last=None
        result=self.detector.detect(frame,layout)
        if result.rect is None:return result
        if result.cached and self._last and self._last.fingerprint==result.fingerprint:
            result.slots=self._last.slots;return result
        started=time.perf_counter()
        x,y,w,h=result.rect;window=frame[y:y+h,x:x+w]
        output=[]
        for slot in result.slots:
            sx,sy,sw,sh=slot['search']
            match=self.matcher.match(window[sy:sy+sh,sx:sx+sw],result.scale)
            output.append(dict(slot,**match))
        result.slots=output;result.elapsed_ms+=(time.perf_counter()-started)*1000
        self._last=result
        return result
