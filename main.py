"""라그메이트 — 라그나로크 플레이 도우미.

  python main.py              # GUI 실행 (최신 Replay 자동 분석)
  python main.py --export     # GUI 없이 최신 Replay를 캐시/로컬 시세로 분석해 엑셀 저장
"""
import argparse
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from config.settings import Settings  # noqa: E402


def _file_handler(name, level=logging.INFO):
    h = RotatingFileHandler(os.path.join(HERE, 'logs', name), maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    h.setLevel(level)
    return h


def setup_logging():
    """logs/app.log(전체), parser.log, unknown_packets.log, market.log — 모두 로테이션."""
    os.makedirs(os.path.join(HERE, 'logs'), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(_file_handler('app.log'))
    for name, fname in (('parser', 'parser.log'), ('market', 'market.log'), ('services.market_service', 'market.log'),
                        ('services.price_provider', 'market.log')):
        logging.getLogger(name).addHandler(_file_handler(fname))
    unk = logging.getLogger('unknown_packets')
    unk.addHandler(_file_handler('unknown_packets.log'))
    unk.propagate = False
    if sys.stderr:
        root.addHandler(logging.StreamHandler())


def run_gui(settings, replay=None):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ui.main_window import MainWindow

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    # Windows 배율 125%/150% 등 비정수 배율에서도 흐릿하거나 잘리지 않게(Overlay 포함)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    try:                              # 작업표시줄에 python 아이콘이 아니라 라그메이트 아이콘이 뜨도록
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('RagnaMate.App')
    except Exception:
        pass
    app = QApplication(sys.argv)
    from ui.theme import app_icon, apply_theme
    app.setWindowIcon(app_icon())     # 창 제목줄·작업표시줄·모든 대화상자
    apply_theme(app)                  # 화면 개선 시안(색·글자·탭) 전체 적용

    def excepthook(t, v, tb):
        if issubclass(t, (KeyboardInterrupt, SystemExit)):      # 종료는 오류가 아니다
            return
        logging.getLogger('crash').error(''.join(traceback.format_exception(t, v, tb)))
        QMessageBox.critical(None, '오류', '%s\n\n자세한 내용은 logs/app.log를 확인하세요.' % v)
    sys.excepthook = excepthook

    w = MainWindow(settings, replay)
    w.show()
    return app.exec()


def run_export(settings):
    from core import analysis
    from core.items import ItemDB
    from database.database import Database
    from services import export_service, replay_service
    from services.market_service import MarketService
    from ui.table_model import Formatter

    latest = replay_service.find_latest(settings['replay_dir'])
    if not latest:
        print('Replay 파일이 없습니다:', settings['replay_dir'])
        return 1
    item_db = ItemDB(settings['ro_dir'], os.path.join(HERE, 'data', 'item_db.json')).load()
    res = replay_service.load_replay(latest[0], item_db)
    entries = analysis.merge_entries(res.entries) if settings['merge_stackables'] else res.entries
    market = MarketService(Database(settings.abs_path('db_path')), settings)
    prices, _ = market.fetch({e.item_id: e.name for e in entries})
    rows = sorted(analysis.build_rows(entries, prices, settings['price_basis']), key=lambda r: -(r.value or 0))
    path = export_service.export(export_service.default_filename(HERE), rows, Formatter(item_db),
                                 analysis.summarize(rows),
                                 {'Replay': os.path.basename(res.path), '서버': res.server or '',
                                  '시세 출처': market.provider.label})
    print('저장:', path)
    return 0


_INSTANCE = None


def already_running():
    """이미 켜진 라그메이트가 있으면 True(트레이에 남은 이전 실행 포함). 두 개가 뜨면 노점 패널·오버레이가 번갈아 겹친다."""
    global _INSTANCE
    if os.name != 'nt':
        return False
    import ctypes
    import ctypes.wintypes as wt
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = wt.HANDLE
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)
    _INSTANCE = k32.CreateMutexW(None, False, 'Local\\RagnaMate.SingleInstance')
    if ctypes.get_last_error() != 183:                                # ERROR_ALREADY_EXISTS
        return False
    fn = ctypes.windll.user32.MessageBoxW
    fn.argtypes = (wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
    fn(None, '라그메이트가 이미 실행 중입니다.\n\n작업 표시줄 오른쪽 트레이의 라그메이트 아이콘을 두 번 눌러 여세요.\n'
             '새 버전으로 다시 켜려면 트레이 아이콘 우클릭 → 종료 후 다시 실행하세요.', '라그메이트', 0x40)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('replay', nargs='?', help='분석할 .rrf 파일(생략 시 최신 파일)')
    ap.add_argument('--export', action='store_true', help='GUI 없이 엑셀로 내보내기')
    ap.add_argument('--elevated-launch', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args()
    from core.elevation import ensure_gui_admin
    elevated = ensure_gui_admin(__file__, replay=a.replay, export=a.export, attempted=a.elevated_launch)
    if elevated is not None:
        return elevated
    if not a.export and already_running():
        return 0
    setup_logging()
    settings = Settings()
    try:
        # 이전 버전(price_cache.json)의 시세 스냅샷을 로컬 DB로 1회 이관
        from database.database import Database
        from services.price_provider.local import LocalPriceProvider
        LocalPriceProvider(Database(settings.abs_path('db_path')), settings) \
            .import_legacy_cache(os.path.join(HERE, 'price_cache.json'))
    except Exception:
        logging.getLogger(__name__).exception('이전 시세 이관 실패')
    return run_export(settings) if a.export else run_gui(settings, a.replay)


if __name__ == '__main__':
    sys.exit(main())
