"""Qt coordinator: capture/read-only recognition off the UI thread, hide on loss of confidence."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer, Signal
from config.settings import BASE_DIR
from core.storage_auto import candidates_from_rows, with_official_prices, price_labels
from live.storage_detect import StorageRecognizer, digest
from services.item_icons import ItemIconCache


class StorageAutoController(QObject):
    result_changed=Signal(object)
    status_changed=Signal(str)

    def __init__(self,overlay,rows_provider,config,context_provider,window_provider,
                 server_provider=None,preview=False,capture_factory=None):
        super().__init__(overlay)
        self.overlay,self.rows_provider,self.config=overlay,rows_provider,config
        self.context_provider,self.window_provider=context_provider,window_provider
        self.server_provider=server_provider or (lambda: None)
        self.preview=preview
        self.capture_factory=capture_factory
        self.profile=config.get('profile') or str(Path(BASE_DIR)/'data/storage_templates/kafra_list.npz')
        self.cache=ItemIconCache(Path(BASE_DIR)/'data/item_icons')
        self.recognizer=StorageRecognizer(self.profile)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='storage-recognition')
        self.future=None;self.capture=None;self.hwnd=None
        self.enabled=False;self.context=None;self.last_status='';self.last_frame=None
        self.last_result=None;self._submitted=None
        self.timer=QTimer(self,interval=350,timeout=self.tick)

    def _status(self,text):
        self.overlay.status=text
        if text!=self.last_status:
            self.last_status=text;self.status_changed.emit(text)

    def start(self):
        self.enabled=True;self.timer.start();self.tick()

    def stop(self):
        self.enabled=False;self.timer.stop();self.overlay.hide()
        if self.capture:self.capture.stop();self.capture=None
        self.cache.stop()
        self.executor.shutdown(wait=False,cancel_futures=True)

    def tick(self):
        if not self.enabled:return
        try:self._tick()
        except Exception as exc:
            self.overlay.hide();self._status('자동 인식 대기: '+str(exc))

    def _tick(self):
        win=self.window_provider()
        if not win or win.minimized or (not self.preview and not win.foreground):
            self.overlay.hide();self._status('게임 창이 앞에 있을 때 창고를 찾습니다.');return
        if self.capture is None or self.hwnd!=win.hwnd:
            if self.capture:self.capture.stop()
            factory=self.capture_factory
            if factory is None:
                from live.wgc import WGCCapture
                if not WGCCapture.available:
                    self._status('Windows 화면 캡처 패키지가 필요합니다. requirements-live.txt를 설치하세요.');return
                factory=WGCCapture
            self.capture=factory(win.hwnd,interval_ms=100).start();self.hwnd=win.hwnd
        frame,stamp=self.capture.client_frame(win)
        if self.capture.closed or frame is None or time.time()-stamp>1.2:
            self.overlay.hide();self._status('최신 게임 화면을 기다리는 중입니다.');return
        self.last_frame=frame
        candidates=candidates_from_rows(self.rows_provider(),self.config.get('source',''))
        context=(self.context_provider(),repr(candidates),self.server_provider())
        ids={c['item_id'] for c in candidates};self.cache.request(ids)
        if context!=self.context:
            self.context=context;self.overlay.hide();self.last_result=None
        if not candidates:
            self.overlay.hide();self._status('선택한 창고 목록이 없습니다. 창고를 녹화한 Replay를 불러오세요.');return
        if self.future and self.future.done():
            future,self.future=self.future,None
            result=future.result()
            if self._submitted==context and result.rect:
                x,y,w,h=result.rect
                if frame.shape[0]>=y+h and frame.shape[1]>=x+w and digest(frame[y:y+h,x:x+w,:3])==result.fingerprint:
                    self.last_result=result
                else:self.last_result=None
            else:self.last_result=None
        if self.last_result:
            x,y,w,h=self.last_result.rect
            if frame.shape[0]<y+h or frame.shape[1]<x+w or digest(frame[y:y+h,x:x+w,:3])!=self.last_result.fingerprint:
                self.last_result=None;self.overlay.hide()
            else:
                labels=price_labels(self.last_result.slots)
                self.result_changed.emit(dict(result=self.last_result,labels=labels,frame=frame))
                if not self.preview:self.overlay.show_automatic(self.last_result.rect,labels,win.client)
        else:self.overlay.hide()
        if self.future is None:
            self._submitted=context
            self.future=self.executor.submit(self._process,frame.copy(),candidates,self.cache.version,self.server_provider())
        ready=self.cache.ready(ids)
        if self.cache.blocked:self._status(self.cache.status)
        elif self.last_result:
            unknown=sum(s.get('item') is None for s in self.last_result.slots)
            self._status(f'자동 인식 {len(self.last_result.slots)}칸 · ? {unknown}개 · 아이콘 {ready}/{len(ids)} · {self.last_result.elapsed_ms:.0f}ms')
        else:self._status(f'창고를 찾는 중 · 아이콘 {ready}/{len(ids)} (최초 다운로드는 2초 간격)')

    def _process(self,frame,candidates,version,server):
        candidates=with_official_prices(candidates,Path(BASE_DIR)/'data/vending.db',server)
        return self.recognizer.process(frame,candidates,self.cache.directory,version,self.config.get('layout','list'))
