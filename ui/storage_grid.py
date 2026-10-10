"""창고 슬롯 위 클릭 통과 가격 레이어 및 배치 설정."""
import ctypes
import ctypes.wintypes as wt
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialogButtonBox, QMessageBox)
from core import storage_grid as G


class StorageGridOverlay(QWidget):
    def __init__(self, rows_provider, config, context_provider, window_provider=None, server_provider=None, status_callback=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.rows_provider, self.config, self.context_provider = rows_provider, config, context_provider
        if window_provider is None:
            from live.window import find_game_window
            window_provider = find_game_window
        self.window_provider = window_provider
        self.server_provider = server_provider
        self.status_callback = status_callback
        self._auto = None
        self.enabled = False
        self.values = []
        self.status = ''
        self.timer = QTimer(self, interval=300, timeout=self.refresh)

    def start(self):
        self.enabled = True
        if self.config.get('mode') == 'auto':
            from ui.storage_auto import StorageAutoController
            if self._auto:
                self._auto.stop();self._auto.deleteLater()
            self._auto=StorageAutoController(self,self.rows_provider,self.config,self.context_provider,
                                             self.window_provider,self.server_provider)
            if self.status_callback:self._auto.status_changed.connect(self.status_callback)
            self._auto.start()
            return
        self.timer.start()
        self.refresh()

    def stop(self):
        self.enabled = False
        self.timer.stop()
        if self._auto:
            self._auto.stop();self._auto.deleteLater();self._auto=None
        self.hide()

    def show_automatic(self, rect, values, client):
        self.values=values
        x,y,w,h=rect
        self.show()
        fn=ctypes.windll.user32.SetWindowPos
        fn.argtypes=(wt.HWND,wt.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wt.UINT)
        if not fn(int(self.winId()),wt.HWND(-1),client[0]+x,client[1]+y,w,h,0x0010|0x0040):
            self.hide();raise OSError('가격 레이어 위치 변경 실패')
        self.update()

    def refresh(self):
        if not self.enabled: return
        self.values = G.labels(self.rows_provider(), self.config, self.context_provider())
        if self.values is None:
            self.status = '창고 데이터가 변경되었습니다. 슬롯 배치를 다시 확인하세요.'
            self.stop()
            return
        try:
            window = self.window_provider()
            if not window or window.minimized or not window.foreground:
                self.hide()
                return
            rect = G.physical_rect(self.config, window.client)
            # Qt 논리 좌표와 Windows 물리 픽셀 혼용을 피하기 위해 최종 배치만 Win32로 수행.
            self.show()
            fn = ctypes.windll.user32.SetWindowPos
            fn.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
            if not fn(int(self.winId()), wt.HWND(-1), *rect, 0x0010 | 0x0040):
                raise OSError('슬롯 레이어 배치에 실패했습니다.')
            self.status = ''
            self.update()
        except (ValueError, OSError) as exc:
            self.status = str(exc)
            self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.scale(1/self.devicePixelRatioF(), 1/self.devicePixelRatioF())
        if self.config.get('mode')=='auto':
            for value in self.values:
                x,y,w,h=value['label']
                font=QFont('Malgun Gothic');font.setPixelSize(max(8,h-2));p.setFont(font)
                # A small price chip below the name: don't cover the item icon, quantity or text.
                label=QRectF(x,y,min(w,p.fontMetrics().horizontalAdvance(value['text'])+8),h)
                p.fillRect(label,QColor(20,27,37,140 if value['stale'] else 215))
                p.setPen(QColor('#989898' if value['stale'] else '#ffe694' if value['price'] is not None else '#d9e2f0'))
                p.drawText(label,Qt.AlignCenter,value['text'])
            p.end();return
        p.setFont(QFont('Malgun Gothic', self.config.get('font_size', 9)))
        width = self.config['columns']*self.config['cell_w']
        p.fillRect(QRectF(0,0,width,23),QColor(15,20,28,220))
        p.setPen(QColor('#b8c4d8'))
        title = p.fontMetrics().elidedText('슬롯 기준가 · 수동 배치', Qt.ElideRight, max(1,width-6))
        p.drawText(QRectF(3,0,width-6,23), Qt.AlignVCenter, title)
        cols, cw, ch = self.config['columns'], self.config['cell_w'], self.config['cell_h']
        for i, value in enumerate(self.values or []):
            if value is None: continue
            x, y = (i % cols)*cw, 24+(i//cols)*ch
            label = QRectF(x+1,y+ch-17,cw-2,16)
            p.fillRect(label,QColor(0,0,0,195))
            p.setPen(QColor('#ffe694' if value['price'] is not None else '#c4c4c4'))
            font = QFont('Malgun Gothic')
            font.setPixelSize(12)
            p.setFont(font)
            text_width = p.fontMetrics().horizontalAdvance(value['text'])
            if text_width > cw-4:
                font.setPixelSize(max(7,int(12*(cw-4)/text_width)))
                p.setFont(font)
            p.drawText(label,Qt.AlignCenter,value['text'])
        p.end()

    def closeEvent(self,event):
        self.stop()
        super().closeEvent(event)


class StorageGridDialog(QDialog):
    def __init__(self, settings, rows, context='', parent=None):
        super().__init__(parent)
        self.s, self.data, self.context = settings, rows, context
        self.setWindowTitle('창고 슬롯에 시세 표시 — 배치 설정')
        self.resize(780, 720)
        cfg = settings.get('storage_grid') or {}
        layout = QVBoxLayout(self)
        note = QLabel('자동 인식은 창고의 아이콘과 불러온 Replay의 창고 목록을 대조합니다. 스크롤·탭 변경 후 다시 인식합니다.\n'
                      '? = 구분 안 됨 · - = 판매 시세 없음 · ~ = 장비 옵션을 구분하지 않은 기준가 · 흐린 가격 = 24시간 이상 지난 시세.\n'
                      '처음에는 아이콘을 2초 간격으로 받습니다. 창고 전체 목록이 기록된 최신 Replay를 사용하세요.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.source = QComboBox()
        self.source.addItems(sorted({r.entry.source for r in rows if r.entry.location_group == '창고'}))
        self.source.setCurrentText(cfg.get('source',''))
        form.addRow('창고',self.source)
        self.mode=QComboBox()
        self.mode.addItem('자동 인식 (목록·격자)', 'auto')
        self.mode.addItem('수동 배치 (예비)', 'manual')
        self.mode.setCurrentIndex(self.mode.findData(cfg.get('mode','manual' if cfg.get('confirmed') else 'auto')))
        form.addRow('표시 방식',self.mode)
        self.layout_mode=QComboBox()
        self.layout_mode.addItem('목록형 — 아이템 이름 아래', 'list')
        self.layout_mode.addItem('격자형 — 칸 아래 (테두리 인식)', 'grid')
        self.layout_mode.setCurrentIndex(max(0,self.layout_mode.findData(cfg.get('layout','list'))))
        form.addRow('게임 창고 모양',self.layout_mode)
        layout.addLayout(form)
        self.auto_box=QWidget();auto_layout=QVBoxLayout(self.auto_box)
        auto_note=QLabel('자동 모드에서는 슬롯 위치·아이템 순서를 직접 연결하지 않습니다.\n'
                        '기본 카프라 창고 스킨을 사용하며, 창고를 닫거나 다른 창으로 전환하면 가격이 숨겨집니다.')
        auto_note.setWordWrap(True);auto_layout.addWidget(auto_note)
        preview=QPushButton('현재 게임 화면 인식 결과 미리보기')
        preview.clicked.connect(self._preview_auto);auto_layout.addWidget(preview)
        layout.addWidget(self.auto_box)
        self.manual_box=QWidget();manual=QVBoxLayout(self.manual_box);form=QFormLayout()
        self.fields = {}
        for key,label,default,low,high in [('x','첫 칸 왼쪽 X',100,0,100000),('y','첫 칸 위쪽 Y',100,24,100000),
            ('columns','가로 칸 수',5,1,12),('rows','세로 칸 수',6,1,12),
            ('cell_w','칸 너비 (물리 픽셀)',40,24,200),('cell_h','칸 높이 (물리 픽셀)',40,24,200),
            ('offset','초안 시작 아이템 번호 (0부터)',0,0,10000)]:
            w=QSpinBox(minimum=low,maximum=high,value=cfg.get(key,default))
            self.fields[key]=w
            form.addRow(label,w)
        manual.addLayout(form)
        self.capture=QPushButton('3초 후 첫 슬롯의 왼쪽 위 위치 가져오기')
        self.capture.clicked.connect(self._capture_later)
        manual.addWidget(self.capture)
        self.timer=QTimer(self,interval=3000,singleShot=True,timeout=self._capture)
        self.finished.connect(lambda _: self.timer.stop())
        self.table=QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels(['슬롯 (행,열)','연결할 아이템','판매 최저가 / 갱신일'])
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.table.setColumnWidth(2,190)
        manual.addWidget(self.table,1)
        self.confirm=QCheckBox('아래 배치가 현재 게임 창고의 보이는 슬롯과 일치합니다')
        manual.addWidget(self.confirm)
        layout.addWidget(self.manual_box,1)
        self.auto_spacer=QWidget()
        layout.addWidget(self.auto_spacer,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('저장하고 슬롯 시세 표시')
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source.currentIndexChanged.connect(self._populate)
        for key in ('columns','rows','offset'): self.fields[key].valueChanged.connect(self._populate)
        self._populate()
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()
        if cfg.get('fingerprint') == G.fingerprint(rows,self.source.currentText(),context):
            for i,idx in enumerate(cfg.get('slots',[])[:self.table.rowCount()]):
                combo=self.table.cellWidget(i,1)
                combo.setCurrentIndex(max(0,combo.findData(idx)))

    def _mode_changed(self,*_):
        automatic=self.mode.currentData()=='auto'
        self.manual_box.setVisible(not automatic);self.auto_box.setVisible(automatic)
        self.auto_spacer.setVisible(automatic)
        self.layout_mode.setEnabled(automatic)

    def _preview_auto(self):
        from ui.storage_preview import StoragePreview
        cfg=dict(mode='auto',source=self.source.currentText(),layout=self.layout_mode.currentData())
        parent=self.parent()
        server=lambda:getattr(getattr(parent,'replay',None),'server',None)
        StoragePreview(lambda:self.data,cfg,lambda:self.context,server,self).exec()

    def _populate(self,*_):
        self.confirm.setChecked(False)
        self.entries=G.inventory(self.data,self.source.currentText())
        count=self.fields['columns'].value()*self.fields['rows'].value()
        self.table.setRowCount(0)
        self.table.setRowCount(count)
        for i in range(count):
            cols=self.fields['columns'].value()
            self.table.setItem(i,0,QTableWidgetItem('%d,%d' % (i//cols+1,i%cols+1)))
            combo=QComboBox()
            combo.addItem('빈 칸 / 표시 안 함',None)
            for j,r in enumerate(self.entries):
                e=r.entry
                combo.addItem('%s%s ×%d · ID %d · #%s' % ('+%d ' % e.refine if e.refine else '',e.name,e.quantity,e.item_id,e.raw_data.get('index','?')),j)
            combo.setCurrentIndex(min(i+self.fields['offset'].value()+1,len(self.entries)) if i+self.fields['offset'].value()<len(self.entries) else 0)
            self.table.setCellWidget(i,1,combo)
            combo.currentIndexChanged.connect(lambda _,i=i:self._changed(i))
            self._changed(i)

    def _changed(self,i):
        self.confirm.setChecked(False)
        idx=self.table.cellWidget(i,1).currentData()
        r=self.entries[idx] if idx is not None else None
        price=r.price if r else None
        import datetime
        text=('%s Z / %s' % (format(price.lowest_price,','),datetime.datetime.fromtimestamp(price.updated_at).strftime('%m-%d %H:%M'))
              if price and price.has_price else '시세 없음' if r else '-')
        self.table.setItem(i,2,QTableWidgetItem(text))

    def _capture_later(self):
        self.capture.setEnabled(False)
        self.capture.setText('첫 슬롯의 왼쪽 위로 마우스를 이동하세요…')
        self.timer.start()

    def _capture(self):
        from core.mouse_coords import capture_position
        try:
            x,y=capture_position('game')
            self.fields['x'].setValue(x)
            self.fields['y'].setValue(y)
        except (ValueError,OSError) as exc: QMessageBox.warning(self,'위치 가져오기',str(exc))
        self.capture.setEnabled(True)
        self.capture.setText('3초 후 첫 슬롯의 왼쪽 위 위치 가져오기')

    def _save(self):
        if self.mode.currentData()=='auto':
            if not self.entries:
                QMessageBox.warning(self,'창고 목록 필요','선택한 창고가 기록된 Replay를 먼저 불러오세요.');return
            self.s['storage_grid']=dict(mode='auto',enabled=True,source=self.source.currentText(),layout=self.layout_mode.currentData())
            self.s.save();self.accept();return
        if not self.confirm.isChecked() or not self.entries:
            QMessageBox.warning(self,'배치 확인','창고 데이터와 슬롯 연결을 확인한 뒤 일치함을 체크하세요.')
            return
        cfg={key:w.value() for key,w in self.fields.items()}
        cfg.update(mode='manual',source=self.source.currentText(),confirmed=True,
                   slots=[self.table.cellWidget(i,1).currentData() for i in range(self.table.rowCount())],
                   fingerprint=G.fingerprint(self.data,self.source.currentText(),self.context))
        self.s['storage_grid']=cfg
        self.s.save()
        self.accept()
