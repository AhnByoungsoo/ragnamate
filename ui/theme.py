"""화면 개선 시안(2026-09-24 확정, Claude Design 캔버스 '라그메이트 화면 개선 시안' v2)의 색·글자·간격을 프로그램 전체에 적용.

모든 글자 조합은 대비 4.5:1 이상(가장 낮은 것: 보조 설명 #8E97A6 / 카드 #1C2027 = 5.5:1).
"""
import os
import re

from PySide6.QtGui import QColor, QFont, QIcon, QPalette

APP_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'app_icon.ico')   # tools/build_icon.py


def app_icon():
    """라그메이트 앱 아이콘(날개 달린 포링). 창·작업표시줄·트레이 공통."""
    return QIcon(APP_ICON)

T = {
    'bg': '#111317',        # 창 바탕
    'surface': '#1C2027',   # 카드·입력칸·표
    'button': '#D7E3F2', 'button_text': '#132034',
    'button_hover': '#EEF5FF', 'button_pressed': '#AFC7E5',
    'surface2': '#242933',  # 보조 버튼
    'header': '#252C38',    # 표 머리·구역 제목 줄
    'tabbar': '#232A36',    # 탭 줄
    'tab': '#2C3340',       # 선택 안 된 탭
    'line': '#39414F',      # 카드 테두리
    'line2': '#323846',     # 버튼·입력 테두리
    'text': '#EEF0F4',
    'text2': '#B4BBC7',     # 이름표
    'text3': '#8E97A6',     # 보조 설명
    'tabtext': '#D5DAE2',
    'accent': '#5AA9FF',    # 주 버튼·현재 탭
    'on_accent': '#0B1726',
    'accent_ring': '#9CCBFF',
    'select': '#2E4461',
    'ok': '#43C08A', 'ok_bg': '#16301F',
    'warn': '#F0B541', 'warn_bg': '#3A2A12',
    'bad': '#FF7A7A', 'bad_bg': '#4A1F22',
}
FONT_FAMILY = 'Malgun Gothic'
FONT_PX = 15               # 본문 15px (시안 1차 14px에서 +1)

QSS = """
QWidget {{ color: {text}; font-family: '{font}'; font-size: {px}px; }}
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {surface}; color: {text}; border: 1px solid {line}; padding: 6px; }}

QPushButton, QToolButton {{ background: {button}; color: {button_text}; border: 1px solid {button}; border-radius: 8px;
    padding: 7px 16px; font-weight: 600; }}
QPushButton:hover, QToolButton:hover {{ background: {button_hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {button_pressed}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {text2}; background: {line}; border-color: {line}; }}
QPushButton:checked, QToolButton:checked {{ background: {accent}; color: {on_accent}; border-color: {accent_ring}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {accent}; }}
QPushButton[primary="true"], QToolButton[primary="true"] {{ background: {accent}; color: {on_accent};
    border: 1px solid {accent}; font-weight: 700; }}
QPushButton[primary="true"]:hover, QToolButton[primary="true"]:hover {{ background: #7DBBFF; }}
QToolButton::menu-button {{ width: 18px; border-left: 1px solid {line2}; border-top-right-radius: 8px;
    border-bottom-right-radius: 8px; }}
QToolButton[primary="true"]::menu-button {{ border-left: 1px solid #3F8FE6; }}

QTabWidget::pane {{ border: 1px solid {line}; border-radius: 10px; top: -1px; background: {bg}; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: {tab}; color: {tabtext}; border: 1px solid #3A4250; border-radius: 8px;
    padding: 7px 16px; margin: 6px 3px 8px 0; }}
QTabBar::tab:hover {{ background: #353D4B; }}
QTabBar::tab:selected {{ background: {accent}; color: {on_accent}; font-weight: 700; border: 1px solid {accent_ring}; }}
QTabWidget#mainTabs > QTabBar::tab {{ font-size: {tabpx}px; padding: 8px 20px; margin: 8px 6px 10px 0; }}

QFrame#card {{ background: {surface}; border: 1px solid {line}; border-radius: 12px; }}
QGroupBox {{ background: {surface}; border: 1px solid {line}; border-radius: 12px; margin-top: 14px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {text}; font-weight: 700; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {surface}; color: {text}; border: 1px solid {line2}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {select}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {accent}; }}
QComboBox {{ padding-right: 38px; }}
QComboBox::drop-down {{ background: {button}; border: 0; width: 28px;
    border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}
QComboBox::drop-down:hover {{ background: {button_hover}; }}
QComboBox::drop-down:disabled {{ background: {line}; }}
QComboBox::down-arrow {{ image: url("{down_arrow}"); width: 10px; height: 6px; }}
QComboBox::down-arrow:disabled {{ image: url("{down_disabled}"); }}

QSpinBox, QDoubleSpinBox, QDateTimeEdit {{ padding-right: 38px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateTimeEdit::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 28px;
    background: {button}; border-left: 1px solid {line}; border-bottom: 1px solid {line}; border-top-right-radius: 7px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateTimeEdit::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 28px;
    background: {button}; border-left: 1px solid {line}; border-bottom-right-radius: 7px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
QDateTimeEdit::up-button:hover, QDateTimeEdit::down-button:hover {{ background: {button_hover}; }}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed,
QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed,
QDateTimeEdit::up-button:pressed, QDateTimeEdit::down-button:pressed {{ background: {button_pressed}; }}
QSpinBox::up-button:off, QSpinBox::down-button:off, QSpinBox::up-button:disabled, QSpinBox::down-button:disabled,
QDoubleSpinBox::up-button:off, QDoubleSpinBox::down-button:off, QDoubleSpinBox::up-button:disabled, QDoubleSpinBox::down-button:disabled,
QDateTimeEdit::up-button:off, QDateTimeEdit::down-button:off, QDateTimeEdit::up-button:disabled, QDateTimeEdit::down-button:disabled {{ background: {line}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateTimeEdit::up-arrow {{ image: url("{up_arrow}"); width: 10px; height: 6px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateTimeEdit::down-arrow {{ image: url("{down_arrow}"); width: 10px; height: 6px; }}
QSpinBox::up-arrow:off, QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:off, QDoubleSpinBox::up-arrow:disabled,
QDateTimeEdit::up-arrow:off, QDateTimeEdit::up-arrow:disabled {{ image: url("{up_disabled}"); }}
QSpinBox::down-arrow:off, QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:off, QDoubleSpinBox::down-arrow:disabled,
QDateTimeEdit::down-arrow:off, QDateTimeEdit::down-arrow:disabled {{ image: url("{down_disabled}"); }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {line}; selection-background-color: {select}; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator:unchecked {{ width: 14px; height: 14px; border: 1px solid {text3}; border-radius: 3px;
    background: {surface2}; }}

QTableView, QTableWidget, QTreeView, QListView, QListWidget {{ background: {surface}; alternate-background-color: #1F242C;
    border: 1px solid {line}; border-radius: 10px; gridline-color: #2A303B; selection-background-color: {select};
    selection-color: {text}; }}
QHeaderView::section {{ background: {header}; color: {text2}; padding: 8px 6px; border: 0;
    border-bottom: 1px solid {line}; font-weight: 600; }}
QTableCornerButton::section {{ background: {header}; border: 0; }}

QMenu {{ background: {surface}; border: 1px solid {line}; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {select}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 6px 8px; }}

QStatusBar {{ background: {surface}; color: {text2}; border-top: 1px solid {line2}; }}
QStatusBar QLabel {{ color: {text2}; }}
QProgressBar {{ background: {surface2}; border: 1px solid {line2}; border-radius: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
QSplitter::handle {{ background: {line}; }}
QScrollArea#pageScroll, QScrollArea#pageScroll > QWidget#qt_scrollarea_viewport {{ background: {bg}; border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle {{ background: #3A4250; border-radius: 5px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: #4A5364; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""

# 목록 항목을 카드처럼 (매크로 목록, 사냥터 속성표 등 위젯에 setStyleSheet 로 붙인다)
CARD_LIST_QSS = ('QListWidget {{ padding: 6px; }}'
                 ' QListWidget::item {{ background: {surface2}; border: 1px solid {line}; border-radius: 8px;'
                 ' padding: 8px 10px; margin: 2px 0; }}'
                 ' QListWidget::item:selected {{ background: {select}; border: 1px solid {accent}; color: {text}; }}'
                 ' QListWidget::item:hover:!selected {{ border: 1px solid {text3}; }}').format(**T)

MIN_SCALE, MAX_SCALE = 0.6, 1.3


def scale_qss(qss, scale):
    """스타일 안의 모든 px(글자·여백·모서리)를 배율만큼. 1px 테두리는 1px 유지."""
    if abs(scale - 1.0) < 1e-3:
        return qss
    return re.sub(r'(\d+)px', lambda m: '%dpx' % max(1, round(int(m.group(1)) * scale)), qss)


def stylesheet(scale=1.0):
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
    arrows = {key: os.path.join(assets, name).replace('\\', '/') for key, name in (
        ('up_arrow', 'step-up.svg'), ('down_arrow', 'step-down.svg'),
        ('up_disabled', 'step-up-disabled.svg'), ('down_disabled', 'step-down-disabled.svg'))}
    return scale_qss(QSS.format(font=FONT_FAMILY, px=FONT_PX, tabpx=FONT_PX + 1, **arrows, **T), scale)


def palette():
    p = QPalette()
    c = lambda k: QColor(T[k])  # noqa: E731
    for role, key in ((QPalette.Window, 'bg'), (QPalette.WindowText, 'text'), (QPalette.Base, 'surface'),
                      (QPalette.AlternateBase, 'surface2'), (QPalette.Text, 'text'), (QPalette.Button, 'button'),
                      (QPalette.ButtonText, 'button_text'), (QPalette.ToolTipBase, 'surface'), (QPalette.ToolTipText, 'text'),
                      (QPalette.Highlight, 'select'), (QPalette.HighlightedText, 'text'), (QPalette.Link, 'accent'),
                      (QPalette.PlaceholderText, 'text3'), (QPalette.Mid, 'line'), (QPalette.Midlight, 'line2'),
                      (QPalette.Dark, 'bg'), (QPalette.Light, 'tab'), (QPalette.BrightText, 'text')):
        p.setColor(role, c(key))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c('text3'))
    return p


def apply_theme(app, scale=1.0):
    """QApplication 시작 시 한 번. 기존 화면의 palette(...) 참조도 시안 색을 따른다."""
    app.setStyle('Fusion')
    app.setPalette(palette())
    set_scale(app, scale)


def set_scale(app, scale):
    """글자·여백 크기만 바꿔 다시 그린다(창 크기 변경, Ctrl +/- 에서 호출)."""
    f = QFont(FONT_FAMILY)
    f.setPixelSize(max(9, round(FONT_PX * scale)))
    app.setFont(f)
    app.setStyleSheet(stylesheet(scale))
