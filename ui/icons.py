"""상태 모니터용 아이콘. 사용자가 넣어 둔 이미지 → 없으면 종류별 기본 아이콘을 그려서 쓴다.

없는 아이콘을 인터넷에서 아무 이미지로 대신 채우지 않는다.
같은 아이콘을 여러 곳에서 다시 읽지 않도록 캐시한다.
"""
import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from core.icon_catalog import bundled_icon, canonical_key

from live.status import CONSUMABLE, COOLDOWN, ITEM_BUFF, OTHER, PASSIVE, SKILL_BUFF

# 종류별 기본 아이콘(글자 + 색). 실제 게임 아이콘이 아니라 '무엇인지' 구분용 표시다.
FALLBACK = {
    ITEM_BUFF: ('아', '#3d6fb4'), CONSUMABLE: ('소', '#3d6fb4'), SKILL_BUFF: ('스', '#7a4fb3'),
    PASSIVE: ('상', '#4f7a5e'), COOLDOWN: ('쿨', '#8a6d3b'), OTHER: ('효', '#4a5568'),
}


class IconResolver:
    """우선순위: 사용자가 지정한 이미지 > data/icons/<key>.png > 종류별 기본 아이콘."""

    def __init__(self, icon_map=None, icon_dir=''):
        self.icon_map = icon_map                 # ui.buff_overlay.IconMap (사용자가 [아이콘 지정]한 것)
        self.icon_dir = icon_dir
        self._cache = {}

    def path_for(self, key):
        if self.icon_map is not None:
            path = getattr(self.icon_map, 'get_custom', self.icon_map.get)(key)
            if path:
                return path
        if self.icon_dir and str(key) == os.path.basename(str(key)) and not any(c in str(key) for c in ('/', '\\', ':')):
            for ext in ('.png', '.bmp', '.jpg', '.webp'):
                p = os.path.join(self.icon_dir, '%s%s' % (key, ext))
                if os.path.isfile(p):
                    return p
        return bundled_icon(key, os.path.dirname(self.icon_dir)) if self.icon_dir else bundled_icon(key)

    def icon(self, key):
        """Real image for tables; a missing asset stays empty instead of borrowing an unrelated icon."""
        path = self.path_for(key)
        cache_key = ('icon', key, path)
        if cache_key not in self._cache:
            self._cache[cache_key] = QIcon(path) if path else QIcon()
        return self._cache[cache_key]

    def pixmap(self, key, kind=OTHER, size=24, label=''):
        cache_key = (key, kind, size, label[:2])
        hit = self._cache.get(cache_key)
        if hit is not None:
            return hit
        path = self.path_for(key)
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._cache[cache_key] = pm
                return pm
        self._cache[cache_key] = pm = self._draw(kind, size, label)
        return pm

    def _draw(self, kind, size, label):
        text, color = FALLBACK.get(kind, FALLBACK[OTHER])
        if label:
            text = label[:2]
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(color)))
        p.setPen(QPen(QColor('#00000000')))
        p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.22, size * 0.22)
        f = QFont('Malgun Gothic')
        f.setPixelSize(int(size * (0.5 if len(text) > 1 else 0.62)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor('#f2f5f9')))
        p.drawText(pm.rect(), Qt.AlignCenter, text)
        p.end()
        return pm

    def clear(self):
        self._cache.clear()
