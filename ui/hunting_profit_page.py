from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton
import datetime
from services.gnjoy_market import SERVERS
from ui.widgets import Table,Col,comma
from ui.economy_tasks import BackgroundTask

class HuntingProfitPage(QWidget):
    def __init__(self,service,parent=None):
        super().__init__(parent);self.service=service;box=QVBoxLayout(self)
        note=QLabel('사냥터 수익은 참고용입니다. 기록의 사냥 시간·처치·획득량과 저장된 공식 판매 시세만 사용합니다.\n'
            '표의 Zeny/h는 아이템 판매가치이며 직접 Zeny·소모품·판매세는 포함하지 않습니다. 시세 없는 항목은 제외합니다.\n'
            '실제 획득 기록 우선 · 인벤토리 변화는 획득 추정 · rAthena 드랍표는 국내 서버와 다를 수 있는 추정입니다.')
        note.setWordWrap(True);box.addWidget(note)
        top=QHBoxLayout();self.server=QComboBox()
        for sid,name in SERVERS.items():self.server.addItem(name,sid)
        top.addWidget(QLabel('서버'));top.addWidget(self.server)
        self.button=QPushButton('저장 기록 다시 계산');self.button.clicked.connect(self.reload);top.addWidget(self.button);top.addStretch();box.addLayout(top)
        self.status=QLabel('저장 기록을 계산하세요.');self.status.setWordWrap(True);box.addWidget(self.status)
        self.maps=Table([Col('사냥터',lambda r:r['name'],width=220),Col('Zeny/h',lambda r:r['per_hour'],lambda x:'시세 없음' if x is None else comma(int(x)),numeric=True,width=130),
            Col('계산 기준',lambda r:r['basis'],width=145),Col('관측 시간(h)',lambda r:r['hours'],lambda x:'%.2f'%x,numeric=True,width=100),
            Col('처치',lambda r:r['kills'],comma,width=70),Col('추천 공격 속성',lambda r:r['element'],width=140,tooltip=lambda r:r['element']),Col('누락·주의',lambda r:r['note'],width=320,tooltip=lambda r:r['note'])],sort_col=1)
        self.maps.clicked.connect(lambda idx:self._show(self.maps.row_at(idx)));box.addWidget(self.maps,1)
        self.detail=QLabel('사냥터를 선택하면 돈 되는 아이템을 표시합니다.');self.detail.setWordWrap(True);box.addWidget(self.detail)
        self.items=Table([Col('돈 되는 드랍 아이템',lambda r:r['name'],width=240),Col('기록/추정 수량',lambda r:r['quantity'],lambda x:'%.3f'%x,numeric=True,width=115),
            Col('저장된 최저가',lambda r:r['price'],comma,width=130),Col('아이템 Zeny/h',lambda r:r['per_hour'],lambda x:comma(int(x)),numeric=True)],sort_col=3)
        box.addWidget(self.items,1);self.task=BackgroundTask(self,self._done,self._error)
    def reload(self):
        sid=self.server.currentData()
        if self.task.start(lambda stop:self.service.load(sid)):
            self.button.setEnabled(False);self.server.setEnabled(False);self.status.setText('저장된 분석 기록 계산 중 · 홈페이지 요청 없음')
    def _done(self,data):
        self.button.setEnabled(True);self.server.setEnabled(True);self.maps.set_rows(data['rows']);self.items.set_rows([])
        self.detail.setText('사냥터를 선택하세요.');self.status.setText(data['status']+(' · 계산 가능한 기록이 없습니다.' if not data['rows'] else ''))
        if data.get('price_at'):
            self.status.setText(self.status.text()+' · 계산 시세 중 가장 오래된 조회: '+datetime.datetime.fromtimestamp(data['price_at']).strftime('%Y-%m-%d %H:%M'))
    def _error(self,exc):
        self.button.setEnabled(True);self.server.setEnabled(True);self.status.setText('계산 실패: '+str(exc))
    def _show(self,row):
        if not row:return
        self.items.set_rows(row['items'])
        self.detail.setText(row['name']+' · '+row['basis']+' · 시세 없음: '+(', '.join(row['unpriced'][:12]) or '없음'))
