"""OCR Provider 추상화 + Windows 내장 OCR(Windows.Media.Ocr, 한국어) 구현 + 텍스트 정규화."""
import asyncio
import logging
import re
import threading
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    confidence: float            # Windows OCR은 단어 신뢰도를 주지 않음 → 정규화 결과로 추정(아래 참고)
    box: tuple                   # (x, y, w, h)
    words: list = field(default_factory=list)


INSTALL_HINT = ('Windows 내장 OCR 패키지가 없습니다. 이 프로그램 폴더에서 '
                r'.venv\Scripts\python.exe -m pip install -r requirements-live.txt 실행')


class OCRProvider:
    name = 'base'
    available = False
    error = ''

    def recognize(self, image_bgr):
        """BGR ndarray → [OcrLine]"""
        raise NotImplementedError


class WindowsOCR(OCRProvider):
    """Windows 10/11 내장 OCR. 한국어 언어팩이 설치되어 있어야 한다."""
    name = 'windows'

    def __init__(self, lang='ko'):
        self.lang = lang
        self.engine = None
        self._loop = None
        self._lock = threading.Lock()
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine
            self.engine = OcrEngine.try_create_from_language(Language(lang))
            self.available = self.engine is not None
            if not self.available:
                self.error = 'Windows에 %s OCR 언어팩이 없습니다(설정 > 시간 및 언어 > 언어에서 추가).' % lang
        except ImportError as e:
            self.error = '%s — %s' % (e, INSTALL_HINT)
            log.warning('Windows OCR 초기화 실패: %s', self.error)
            self.available = False
        except Exception as e:
            self.error = str(e)
            log.warning('Windows OCR 초기화 실패: %s', e)
            self.available = False

    def recognize(self, image_bgr, scale=1.0):
        if not self.available or image_bgr is None or image_bgr.size == 0:
            return []
        import cv2
        img = image_bgr
        if scale != 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        h, w = bgra.shape[:2]
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            result = self._loop.run_until_complete(self._run(bgra, w, h))
        out = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            xs = [wd.bounding_rect.x for wd in words]
            ys = [wd.bounding_rect.y for wd in words]
            x2 = [wd.bounding_rect.x + wd.bounding_rect.width for wd in words]
            y2 = [wd.bounding_rect.y + wd.bounding_rect.height for wd in words]
            box = tuple(int(v / scale) for v in (min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys)))
            text = ' '.join(wd.text for wd in words)
            out.append(OcrLine(text, estimate_confidence(text), box, [wd.text for wd in words]))
        return out

    async def _run(self, bgra, w, h):
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        buf = writer.detach_buffer()
        bmp = SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, w, h)
        return await self.engine.recognize_async(bmp)


class NullOCR(OCRProvider):
    name = 'none'

    def recognize(self, image_bgr, scale=1.0):
        return []


def create_ocr(lang='ko'):
    o = WindowsOCR(lang)
    if o.available:
        return o
    null = NullOCR()
    null.error = o.error or INSTALL_HINT          # 왜 글자를 못 읽는지 화면에 그대로 보여준다
    return null


# ---------------------------------------------------------------- 정규화
_DIGIT_FIX = str.maketrans({'O': '0', 'o': '0', 'D': '0', 'I': '1', 'l': '1', '|': '1', 'S': '5', 'B': '8', 'Z': '2'})


def normalize_text(s):
    """공백/특수문자 정리. 한글은 그대로 두고 연속 공백만 줄인다."""
    s = (s or '').replace('​', '').replace('，', ',').replace('．', '.')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def normalize_number(s):
    """'1O,2l5' → 10215. 숫자로 해석할 수 없으면 None."""
    if s is None:
        return None
    t = str(s).strip().translate(_DIGIT_FIX)
    t = re.sub(r'[,\s.]', '', t)
    return int(t) if t.isdigit() else None


def estimate_confidence(text):
    """Windows OCR은 신뢰도 값을 주지 않으므로 문자 구성으로 대략 추정한다(0~1).
    알 수 없는 기호/깨진 문자가 많을수록 낮게 준다. 이 값은 '표시/검토 여부' 판단에만 쓴다."""
    if not text:
        return 0.0
    ok = sum(1 for ch in text if ch.isalnum() or ch in ' ,.:;/%()[]\'"-+~!?·' or '가' <= ch <= '힣')
    return round(ok / len(text), 2)
