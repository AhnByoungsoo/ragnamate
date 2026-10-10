"""ui/assets/app_icon.svg → app_icon.ico(16~256px 여러 크기) · app_icon.png(256px).

아이콘 그림을 바꾸면 다시 실행:  python tools/build_icon.py
작은 크기는 큰 그림을 줄이지 않고 크기마다 SVG에서 새로 그려 선이 뭉개지지 않게 한다.
"""
import io
import os
import sys

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), 'ui', 'assets')
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(renderer, size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(p)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, 'PNG')
    return Image.open(io.BytesIO(bytes(buf.data()))).convert('RGBA')


def main():
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(os.path.join(ASSETS, 'app_icon.svg'))
    if not renderer.isValid():
        raise SystemExit('app_icon.svg 를 읽지 못했습니다')
    images = [render(renderer, s) for s in SIZES]
    big = images[-1]
    big.save(os.path.join(ASSETS, 'app_icon.png'))
    big.save(os.path.join(ASSETS, 'app_icon.ico'), format='ICO', sizes=[(s, s) for s in SIZES],
             append_images=images[:-1])
    print('만듦:', ', '.join('%dpx' % s for s in SIZES))


if __name__ == '__main__':
    main()
