"""Preview only. No clicks or keystrokes are sent to the game, and no capture is saved."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPixmap
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QPushButton
from ui.storage_auto import StorageAutoController
from live.window import find_game_window


class StoragePreview(QDialog):
    def __init__(self,rows_provider,config,context_provider,server_provider,parent=None):
        super().__init__(parent)
        self.setWindowTitle('창고 자동 인식 미리보기');self.resize(850,750)
        box=QVBoxLayout(self)
        self.note=QLabel('게임 창고를 열면 인식 결과가 표시됩니다. 화면은 저장하지 않습니다.')
        self.note.setWordWrap(True);box.addWidget(self.note)
        self.image=QLabel();self.image.setAlignment(Qt.AlignCenter);box.addWidget(self.image,1)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['행/칸','인식한 아이템','점수','1·2등 차이','가격'])
        self.table.setColumnWidth(1,320);box.addWidget(self.table)
        close=QPushButton('닫기');close.clicked.connect(self.accept);box.addWidget(close)
        # Controller hides its overlay on focus loss; use a hidden child so the preview stays visible.
        from PySide6.QtWidgets import QWidget
        self.sink=QWidget(self);self.sink.hide()
        self.controller=StorageAutoController(self.sink,rows_provider,config,context_provider,find_game_window,
                                              server_provider,preview=True)
        self.controller.status_changed.connect(self.note.setText)
        self.controller.result_changed.connect(self._result)
        self.finished.connect(lambda _:self.controller.stop())
        self.controller.start()

    def _result(self,payload):
        r=payload['result'];frame=payload['frame'];x,y,w,h=r.rect
        rgb=frame[y:y+h,x:x+w,::-1].copy()
        image=QImage(rgb.data,w,h,rgb.strides[0],QImage.Format_RGB888).copy()
        self.image.setPixmap(QPixmap.fromImage(image).scaled(400,350,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.table.setRowCount(len(payload['labels']))
        for i,s in enumerate(payload['labels']):
            text=[str(i+1),s['name'],f"{s['score']:.3f}",f"{s['margin']:.3f}",s['text']]
            for j,v in enumerate(text):self.table.setItem(i,j,QTableWidgetItem(v))
