"""Buff Timer Overlay: 남은 시간을 게임 옆에 띄운다 (표시 전용, 게임 입력 없음).

표시 방식 세 가지 — 목록(막대) · 간단(이름+시간) · 아이콘(작은 칸). 오른쪽 클릭 메뉴에서 바꾼다.
○ = 사용자가 시작한 수동 타이머(시작 시각 기준 계산) · ● = Replay 패킷에서 확인된 Buff.
버프 이름은 지어내지 않는다. 확인된 이름이 없으면 `버프 #993`으로 두고, 사용자가 직접 지정한다.
"""
import html
import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMenu, QVBoxLayout

from live.buffs import mmss, visible_rows
from core.icon_catalog import bundled_icon, canonical_key
from ui.overlay import OverlayWindow

COLOR = {'RUNNING': '#6ddb7c', 'WARN': '#ffd166', 'DANGER': '#ff6b6b', 'EXPIRED': '#7a7a7a'}
NEW_COLOR = '#9ecbff'
DISPLAY = (('list', '목록 (막대)'), ('compact', '간단 (이름+시간)'), ('icon', '아이콘'), ('urgent', '곧 만료만 크게'))


class IconMap:
    """사용자 지정 아이콘 우선, 없으면 출처가 기록된 로컬 게임 아이콘."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding='utf-8') as f:
                self.map = json.load(f)
        except Exception:
            self.map = {}

    def get_custom(self, key):
        keys = [str(key)]
        canonical = canonical_key(key)
        if canonical and canonical.startswith('efst_'):
            keys += [canonical, canonical.replace('efst_', 'replay_', 1)]
        for candidate in keys:
            p = self.map.get(candidate)
            if p and os.path.isfile(p):
                return p
        return None

    def get(self, key):
        return self.get_custom(key) or bundled_icon(key, os.path.dirname(os.path.abspath(self.path)))

    def set(self, key, path):
        if path:
            self.map[str(key)] = path
        else:
            self.map.pop(str(key), None)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.map, f, ensure_ascii=False, indent=1)


def display_menu(settings, parent, on_change, icons_hint=''):
    """표시 방식·감추기·개수 메뉴. 오버레이 오른쪽 클릭과 Buff 패널 버튼이 같이 쓴다."""
    m = QMenu(parent)

    def setter(key, value):
        def run():
            settings[key] = value
            try:
                settings.save()
            except Exception:
                pass
            on_change()
        return run

    mode = m.addMenu('표시 방식')
    for key, label in DISPLAY:
        a = mode.addAction(label, setter('buff_display', key))
        a.setCheckable(True)
        a.setChecked(settings.get('buff_display', 'list') == key)
    for key, label in (('buff_hide_expired', '만료된 것 감추기'), ('buff_hide_long', '24시간 넘는 상시 버프 감추기')):
        a = m.addAction(label, setter(key, not settings.get(key, True)))
        a.setCheckable(True)
        a.setChecked(bool(settings.get(key, True)))
    under = m.addMenu('남은 시간 기준 (그 이하 + 재사용!!만)')
    for n in (10, 30, 60, 0):
        a = under.addAction('전부' if n == 0 else '%d초 이하' % n, setter('buff_overlay_under_sec', n))
        a.setCheckable(True)
        a.setChecked(int(settings.get('buff_overlay_under_sec', 30) or 0) == n)
    count = m.addMenu('최대 개수')
    for n in (6, 8, 12, 20, 0):
        a = count.addAction('전부' if n == 0 else str(n), setter('buff_max_rows', n))
        a.setCheckable(True)
        a.setChecked(int(settings.get('buff_max_rows', 12) or 0) == n)
    size = m.addMenu('아이콘 크기')
    for n in (20, 26, 32, 40):
        a = size.addAction('%dpx' % n, setter('buff_icon_size', n))
        a.setCheckable(True)
        a.setChecked(int(settings.get('buff_icon_size', 26)) == n)
    if icons_hint:
        m.addSeparator()
        m.addAction(icons_hint).setEnabled(False)
    return m


def bar(ratio, color, width=90):
    """남은 비율 막대. 비율을 모르면(Replay remain time 없음) 막대를 그리지 않는다."""
    if ratio is None:
        return '<td width="%d"></td>' % width
    fill = max(0, min(width, int(width * ratio)))
    cells = '<td bgcolor="%s" width="%d"></td>' % (color, fill) if fill else ''
    if fill < width:
        cells += '<td bgcolor="#2c3440" width="%d"></td>' % (width - fill)
    return '<td><table cellspacing="0" cellpadding="0"><tr>%s</tr></table></td>' % cells


def _ratio(r):
    ratio = r.get('ratio')
    if ratio is None and r.get('remaining') is not None and r.get('total'):
        ratio = max(0.0, min(1.0, r['remaining'] / r['total']))
    return ratio


def _color(r):
    if r.get('color'):                       # 상태 모니터가 계산한 색(재사용!! 포함)
        return r['color']
    return NEW_COLOR if r.get('new') else COLOR.get(r.get('state'), '#6ddb7c')


def _time_text(r):
    """효과가 끝난 뒤에는 남은 시간 대신 '재사용!!'을 크게 보여준다."""
    if r.get('state') == 'EXPIRED_ALERT':
        return '재사용!!'
    return mmss(r.get('remaining'))


class BuffOverlay(OverlayWindow):
    def __init__(self, settings, icons=None):
        super().__init__(settings, 'buff_timer', 'Buff Timer')
        self.icons = icons
        self.on_settings_changed = lambda: None
        self.setStyleSheet('QWidget{background:#151a22;color:#e6e6e6;font-family:"Malgun Gothic";}'
                           'QLabel#title{color:#9ecbff;font-weight:bold;}')
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 6, 10, 8)
        self.title = QLabel('Buff Timer')
        self.title.setObjectName('title')
        self.body = QLabel()
        self.body.setTextFormat(Qt.RichText)
        v.addWidget(self.title)
        v.addWidget(self.body)
        self.setMinimumWidth(int(200 * float(settings.get('hud_scale', 1.0))))

    # ------------------------------------------------------------------ 표시
    def update_rows(self, rows, paused=False):
        rows = visible_rows(rows, self.s)
        self.title.setText('Buff Timer' + ('  · 일시정지' if paused else ''))
        mode = self.s.get('buff_display', 'list')
        if not rows:
            self.body.setText('<span style="color:#888">진행 중인 타이머 없음</span>')
        elif mode == 'icon':
            self.body.setText(self._icons_html(rows))
        elif mode == 'urgent':
            self.body.setText(self._urgent_html(rows))
        else:
            self.body.setText('<table cellspacing="3">%s</table>'
                              % ''.join(self._row_html(r, mode) for r in rows))
        self.adjustSize()

    def _row_html(self, r, mode):
        color = _color(r)
        name = html.escape(r['name'][:18])
        time = '<td align="right">&nbsp;<b><font color="%s">%s</font></b></td>' % (color, _time_text(r))
        if mode == 'compact':
            return '<tr><td>%s&nbsp;%s</td>%s</tr>' % (r.get('icon', '○'), name, time)
        return '<tr><td>%s&nbsp;%s</td>%s%s</tr>' % (r.get('icon', '○'), name, bar(_ratio(r), color), time)

    def _urgent_html(self, rows):
        """곧 끝나는 것(종료 임박 기준 이하·재사용!!)만 크게, 나머지는 개수만."""
        limit = int(self.s.get('status_warn_sec', 30) or 30)
        urgent = [r for r in rows if r.get('state') == 'EXPIRED_ALERT'
                  or (r.get('remaining') is not None and r['remaining'] <= limit)]
        rest = len(rows) - len(urgent)
        if not urgent:
            return '<span style="color:#9aa4b2">곧 끝나는 버프 없음 · %d개 진행 중</span>' % rest
        lines = ['<tr><td>%s&nbsp;<b>%s</b></td><td align="right">&nbsp;&nbsp;<b style="font-size:20px"><font color="%s">%s'
                 '</font></b></td></tr>' % (r.get('icon', '○'), html.escape(r['name'][:16]), _color(r), _time_text(r))
                 for r in urgent]
        return '<table cellspacing="2">%s</table>%s' % (''.join(lines), (
            '<span style="color:#9aa4b2">외 %d개 정상</span>' % rest) if rest else '')

    def _icons_html(self, rows):
        size = int(self.s.get('buff_icon_size', 26))
        cols = max(1, int(self.s.get('buff_icon_columns', 6)))
        cells = []
        for r in rows:
            color = _color(r)
            img = self.icons.get(r['key']) if self.icons else None
            if img:
                face = '<img src="%s" width="%d" height="%d">' % (html.escape(img.replace('\\', '/')), size, size)
            else:                                   # 아이콘이 없으면 이름 앞 3글자, 이름이 없으면 EFST 번호
                label = r['name']
                short = html.escape(label.rsplit('#', 1)[-1] if label.startswith('버프 #') else label[:3])
                face = ('<table cellspacing="0" cellpadding="2" width="%d"><tr>'
                        '<td align="center" bgcolor="#222a35"><font color="%s">%s</font></td></tr></table>'
                        % (size, color, short))
            cells.append('<td align="center" title="%s">%s<br><font color="%s" size="-1">%s</font></td>'
                         % (html.escape('%s %s · %s · %s' % (r.get('icon', '○'), r['name'],
                                                             r.get('label', ''), r.get('note', ''))),
                            face, color, _time_text(r)))
        rows_html = ['<tr>%s</tr>' % ''.join(cells[i:i + cols]) for i in range(0, len(cells), cols)]
        return '<table cellspacing="4">%s</table>' % ''.join(rows_html)

    # ------------------------------------------------------------------ 메뉴
    def contextMenuEvent(self, e):
        m = display_menu(self.s, self, self.on_settings_changed,
                         '아이콘 지정: Live 탭 > 버프 타이머 > [아이콘 지정]')
        m.addSeparator()
        op = m.addMenu('투명도')
        for v in (1.0, 0.9, 0.8, 0.7, 0.5):
            op.addAction('%d%%' % (v * 100), lambda v=v: self.set_opacity(v))
        a = m.addAction('위치 고정', lambda: self.set_locked(not self.locked))
        a.setCheckable(True)
        a.setChecked(self.locked)
        m.addAction('숨기기', self.hide)
        m.exec(e.globalPos())

    def follow(self, client):
        """게임 클라이언트 왼쪽 위에 붙이기(사용자가 옮긴 뒤에는 그 위치 유지)."""
        if (self.s.get('overlays') or {}).get(self.key, {}).get('pos') or not client:
            return
        x, y, w, h = client
        self.move(x + 12, y + 12)
