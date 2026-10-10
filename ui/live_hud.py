"""Live HUD Overlay: 게임 창을 따라다니는 작은 정보 창 (정보 표시만, 게임 입력 없음).

프리셋: compact(EXP/h·Profit/h) · hunt(+Top Drops·Buff) · status(HP/SP/AP/WT) · detailed(전부)
오른쪽 클릭 메뉴에서 항목 표시/숨김, 가로·세로, 투명도, 크기, 위치 고정, 클릭 통과를 바꾼다.
데이터마다 출처 표시(○ Screen / ● Replay). 알 수 없는 값은 표시하지 않는다.
"""
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMenu, QVBoxLayout

from live.hud_model import ITEMS, MODE_LABEL, PRESETS, build_rows, visible_items  # noqa: F401 (MODE_LABEL 재사용)
from ui import overlay_styles
from ui.overlay import OverlayWindow


class LiveHud(OverlayWindow):
    def __init__(self, settings):
        super().__init__(settings, 'live_hud', '라그메이트 LIVE')
        self.setStyleSheet('QWidget{background:#151a22;color:#e6e6e6;font-family:"Malgun Gothic";}'
                           'QLabel#title{color:#9ecbff;font-weight:bold;} QLabel#alert{color:#ffd166;font-weight:bold;}')
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 6, 10, 8)
        self.title = QLabel()
        self.title.setObjectName('title')
        self.body = QLabel()
        self.body.setTextFormat(Qt.RichText)
        self.alert = QLabel()
        self.alert.setObjectName('alert')
        self.alert.setWordWrap(True)
        for w in (self.title, self.body, self.alert):
            v.addWidget(w)
        self.preset = settings.get('hud_preset', 'hunt')
        self.apply_scale(settings.get('hud_scale', 1.0))

    def apply_scale(self, scale):
        f = self.font()
        f.setPointSizeF(9 * float(scale))
        self.setFont(f)
        self.setMinimumWidth(int(230 * float(scale)))
        self.s['hud_scale'] = float(scale)
        self.adjustSize()

    # ------------------------------------------------------------------ 표시
    def update_from(self, snap, buff_rows=()):
        settings = dict(self.s)
        settings['hud_preset'] = self.preset            # 단축키로 바꾼 임시 프리셋 반영
        title, rows = build_rows(snap, settings, buff_rows)
        self.title.setText(title)
        if not rows:
            self.body.setText('<span style="color:#888">%s</span>' % html.escape(snap.get('status', '')))
        elif self.s.get('hud_layout') == 'tiles':
            cells = ['<td bgcolor="#1e2530" style="padding:4px 8px"><span style="color:#aab2c0; font-size:11px">%s</span>'
                     '<br><b style="font-size:18px"><font color="%s">%s</font></b> <span style="color:#888; font-size:10px">%s'
                     '</span></td>' % (html.escape(r['label']), r['color'] or '#e6e6e6', html.escape(r['value']), r['src'])
                     for r in rows]
            self.body.setText('<table cellspacing="4">%s</table>' % ''.join(
                '<tr>%s</tr>' % ''.join(cells[i:i + 2]) for i in range(0, len(cells), 2)))
        elif self.s.get('hud_layout') == 'horizontal':
            self.body.setText(' &nbsp;·&nbsp; '.join(
                '%s <b><font color="%s">%s</font></b> <font color="#888">%s</font>'
                % (html.escape(r['label']), r['color'] or '#e6e6e6', html.escape(r['value']), r['src']) for r in rows))
        else:
            self.body.setText('<table cellspacing="2">%s</table>' % ''.join(
                '<tr><td>%s</td><td align="right"><b><font color="%s">%s</font></b></td>'
                '<td style="color:#888">&nbsp;%s</td></tr>'
                % (html.escape(r['label']), r['color'] or '#e6e6e6', html.escape(r['value']), r['src']) for r in rows))
        al = (snap.get('session') or {}).get('alerts') or []
        self.alert.setText(al[-1][1] if al else '')
        self.alert.setVisible(bool(al))
        self.adjustSize()

    def follow(self, client):
        """게임 클라이언트 영역 오른쪽 위에 붙이기(사용자가 옮긴 뒤에는 그 위치 유지)."""
        if (self.s.get('overlays') or {}).get(self.key, {}).get('pos') or not client:
            return
        x, y, w, h = client
        self.move(x + w - self.width() - 12, y + 12)

    # ------------------------------------------------------------------ 오른쪽 클릭 메뉴
    def contextMenuEvent(self, e):
        m = QMenu(self)
        preset = m.addMenu('프리셋')
        for name in PRESETS:
            a = preset.addAction(name, lambda n=name: self._set_preset(n))
            a.setCheckable(True)
            a.setChecked(self.preset == name)
        items = m.addMenu('표시 항목')
        shown = visible_items(dict(self.s, hud_preset=self.preset))
        for key, label in ITEMS:
            a = items.addAction(label, lambda k=key: self._toggle_item(k))
            a.setCheckable(True)
            a.setChecked(key in shown)
        overlay_styles.add_menu(m, self.s, 'hud', lambda: None)       # 모양: 세로 표 / 한 줄 스트립 / 숫자 타일
        op = m.addMenu('투명도')
        for v in (1.0, 0.9, 0.8, 0.7, 0.5):
            op.addAction('%d%%' % (v * 100), lambda v=v: self.set_opacity(v))
        size = m.addMenu('크기')
        for v in (0.9, 1.0, 1.25, 1.5):
            size.addAction('%d%%' % (v * 100), lambda v=v: self.apply_scale(v))
        m.addSeparator()
        a = m.addAction('위치 고정', lambda: self.set_locked(not self.locked))
        a.setCheckable(True)
        a.setChecked(self.locked)
        through = bool(self.windowFlags() & Qt.WindowTransparentForInput)
        a = m.addAction('클릭 통과', lambda: self.set_click_through(not through))
        a.setCheckable(True)
        a.setChecked(through)
        m.addAction('숨기기', self.hide)
        m.exec(e.globalPos())

    def _set(self, key, value):
        self.s[key] = value
        try:
            self.s.save()
        except Exception:
            pass

    def _set_preset(self, name):
        self.preset = name
        self._set('hud_preset', name)

    def _toggle_item(self, key):
        shown = visible_items(dict(self.s, hud_preset=self.preset))
        items = dict(self.s.get('hud_items') or {})
        items[key] = key not in shown
        self._set('hud_items', items)
