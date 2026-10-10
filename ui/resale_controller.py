"""기존 경제 컨트롤러의 저장소·공유 클라이언트·종료 처리를 사용하는 고가 매물 감시."""
import json
import time
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLabel
from services import resale_watch as R
from ui.economy_tasks import BackgroundTask


class ResaleController(QObject):
    def __init__(self, economy):
        super().__init__(economy)
        self.economy, self.app = economy, economy.app
        self.scanner = R.ResaleScanner(economy.store, economy.client)
        self.task = BackgroundTask(self, self.done, self.error)
        economy.tasks.append(self.task)
        self.generation, self.key, self.task_generation = 0, None, None
        self.result, self.dialog, self.popup = None, None, None
        self.status = '고가 매물 감시: 꺼짐'
        self.status_label = QLabel(self.status)
        self.status_label.setWordWrap(True)
        self.allow_alerts = False

    def current_config(self):
        return R.config(self.app.s.get('resale_watch'))

    def sync(self):
        cfg = self.current_config()
        key = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
        if key != self.key:
            self.key = key; self.generation += 1
            if self.task.future is not None: self.task.stop_event.set()
            if self.popup is not None: self.popup.close()
            self.result = None
        return cfg

    def show_status(self, text):
        self.status = text
        self.status_label.setText(text)
        if self.dialog is not None: self.dialog.state.setText(text)

    def tick(self):
        cfg = self.sync()
        if self.economy.stopped: return
        if not cfg['enabled']:
            self.show_status('고가 매물 감시: 꺼짐 · 시세 확인 버튼으로 수동 확인 가능'); return
        self.start_scan(True)

    def scan_now(self):
        self.start_scan(False)

    def start_scan(self, alerts):
        cfg = self.sync()
        if self.economy.stopped: return
        if self.economy.client.retry_after:
            self.show_status('공식 요청 중지 · %d초 뒤 재개'%self.economy.client.retry_after); return
        if self.task.future is not None:
            self.show_status('앞선 확인을 마치는 중입니다.'); return
        targets = R.targets_for(self.app.item_db, cfg)
        own = self.economy._own()
        if self.task.start(lambda stop:self.scanner.scan(cfg['server'], targets, cfg, own, stop)):
            self.task_generation = self.generation
            self.allow_alerts = alerts and cfg['enabled']
            self.show_status('고가 매물 %d개 대상 순차 확인 중 · 한 번에 한 대상 갱신'%len(targets))

    def refresh_cached(self):
        cfg = self.sync()
        targets = R.targets_for(self.app.item_db, cfg)
        snaps = {t['query']:self.scanner.snapshot(cfg['server'], t) for t in targets}
        result = R.evaluate(snaps, targets, cfg, self.economy._own())
        result.update(server=cfg['server'], checked_at=time.time())
        self.display(result)

    def display(self, result):
        self.result = result
        if self.dialog is not None: self.dialog.set_result(result)
        mode = '켜짐' if self.current_config()['enabled'] else '꺼짐'
        self.show_status('감시 %s · 대상 %d / 추천 %d / 확인 필요 %d · 미조회 %d / 오래된 시세 %d / 기록 부족 %d' % (
            mode, result['targets'], sum(r['eligible'] for r in result['rows']), sum(not r['eligible'] for r in result['rows']),
            result['unchecked'], result['stale'], result['insufficient']))

    def done(self, result):
        cfg = self.sync()
        if self.economy.stopped or self.task_generation != self.generation or result['server'] != cfg['server']: return
        self.display(result)
        if not cfg['enabled'] or not self.allow_alerts: return
        if self.popup is not None and self.popup.isVisible(): return
        rows = [r for r in result['rows'] if r['eligible'] and not self.scanner.seen(r)][:3]
        if rows and self.notify(rows):
            for row in rows: self.scanner.mark_seen(row)

    def notify(self, rows):
        from ui.resale_watch_dialog import ResalePopup
        if self.popup is not None: self.popup.deleteLater()
        self.popup = ResalePopup(self.app, rows, self.open)
        self.popup.show()
        return self.popup.isVisible()

    def error(self, exc):
        if not self.economy.stopped: self.show_status('고가 매물 확인 대기: '+str(exc))

    def save_config(self, cfg):
        old = self.app.s.get('resale_watch')
        self.app.s['resale_watch'] = cfg
        try:
            self.app.s.save()
        except Exception:
            self.app.s['resale_watch'] = old
            self.show_status('설정을 저장하지 못했습니다. 기존 설정을 유지합니다.'); return
        self.sync(); self.refresh_cached(); self.tick()

    def open(self):
        from ui.resale_watch_dialog import ResaleWatchDialog
        if self.dialog is None or not self.dialog.isVisible():
            if self.dialog is not None: self.dialog.deleteLater()
            self.dialog = ResaleWatchDialog(self.app, self)
        self.refresh_cached()
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()

    def stop(self):
        if self.popup is not None: self.popup.close()
        if self.dialog is not None: self.dialog.close()
