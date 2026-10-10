"""오버레이 모양(타입) 목록 — 설정 창과 각 오버레이의 오른쪽 클릭 메뉴가 같이 쓴다.

버프 타이머·Live HUD 는 예전부터 있던 설정 키(buff_display, hud_layout)를 그대로 쓰고,
나머지는 settings['overlay_style'] = {이름: 모양} 에 저장한다.
"""
from PySide6.QtWidgets import QMenu

STYLES = {
    'quest': ('퀘스트 진행', (('card', '카드형'), ('steps', '단계 목록형'), ('strip', '한 줄 스트립')), 'card'),
    'vend': ('노점 추천가', (('table', '표'), ('cards', '좁은 카드'), ('inline', '아이템 옆 한 줄')), 'table'),
    'buff': ('버프 타이머', (('list', '목록 (막대)'), ('compact', '간단 (이름+시간)'), ('icon', '아이콘'),
                         ('urgent', '곧 만료만 크게')), 'list'),
    'hud': ('Live HUD', (('vertical', '세로 표'), ('horizontal', '한 줄 스트립'), ('tiles', '숫자 타일')), 'vertical'),
    'storage': ('창고 시세', (('table', '표'), ('summary', '합계 먼저')), 'table'),
}
LEGACY_KEYS = {'buff': 'buff_display', 'hud': 'hud_layout'}


def get(settings, name):
    options = [k for k, _ in STYLES[name][1]]
    if name in LEGACY_KEYS:
        value = settings.get(LEGACY_KEYS[name])
    else:
        value = (settings.get('overlay_style') or {}).get(name)
    return value if value in options else STYLES[name][2]


def set_style(settings, name, value, save=True):
    if name in LEGACY_KEYS:
        settings[LEGACY_KEYS[name]] = value
    else:
        styles = dict(settings.get('overlay_style') or {})
        styles[name] = value
        settings['overlay_style'] = styles
    if save:
        try:
            settings.save()
        except Exception:
            pass


def add_menu(menu, settings, name, on_change, title='모양'):
    """menu 에 '모양' 하위 메뉴(체크 표시)를 붙인다. 고르면 저장하고 on_change() 를 부른다."""
    sub = menu.addMenu(title)
    current = get(settings, name)
    for key, label in STYLES[name][1]:
        def pick(k=key):
            set_style(settings, name, k)
            on_change()
        a = sub.addAction(label, pick)
        a.setCheckable(True)
        a.setChecked(key == current)
    return sub


def style_menu(parent, settings, name, on_change):
    """오른쪽 클릭용 단독 메뉴."""
    m = QMenu(parent)
    add_menu(m, settings, name, on_change)
    return m
