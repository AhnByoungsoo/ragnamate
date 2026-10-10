"""위젯을 작업 스레드에서 만지지 않는 간단한 비동기 실행기."""
import threading
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject,QTimer

class BackgroundTask(QObject):
    def __init__(self,parent,done,error):
        super().__init__(parent);self.done=done;self.error=error
        self.stop_event=threading.Event();self.future=None
        self.closed=False
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='market-tools')
        self.timer=QTimer(self,interval=200,timeout=self.poll)
    def start(self,fn):
        if self.closed or self.future is not None:return False
        self.stop_event.clear();self.future=self.executor.submit(fn,self.stop_event);self.timer.start();return True
    def poll(self):
        if self.future is None or not self.future.done():return
        future,self.future=self.future,None;self.timer.stop()
        try:value=future.result()
        except Exception as exc:self.error(exc)
        else:self.done(value)
    def close(self):
        if self.closed:return
        self.closed=True
        self.stop_event.set();self.timer.stop();self.executor.shutdown(wait=False,cancel_futures=True)
