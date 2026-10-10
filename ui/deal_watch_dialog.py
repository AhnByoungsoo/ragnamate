"""관심 아이템 저가 매물 알림 설정. 왼쪽: 분류 + 자동완성으로 관심 아이템 등록, 오른쪽: 저장된 시세로 본 추천 매물·최근 알림.
오른쪽 표는 앱이 10분마다 확인해 저장한 시세만 쓰며 이 창에서는 공식 요청을 하지 않는다."""
import datetime
from PySide6.QtCore import Qt,QStringListModel
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QSpinBox,QPushButton,QLineEdit,QCompleter,
    QTableWidget,QTableWidgetItem,QCheckBox,QMessageBox,QDialogButtonBox,QAbstractItemView,QSplitter,QWidget)
from core.item_category import CATEGORIES,categorize_all
from services import popular_option as PO
from services.gnjoy_market import SERVERS,RateLimited,join_name
from services.deal_watch import validate_watchlist,MAX_WATCH_ITEMS,deal_candidates,recent_alerts
from ui.economy_tasks import BackgroundTask
from ui.widgets import Table,Col,comma

def _pct(x):return '-' if x is None else '%.1f%%'%x
def _when(t):return '-' if not t else datetime.datetime.fromtimestamp(t).strftime('%m-%d %H:%M')

class DealWatchDialog(QDialog):
    def __init__(self,parent,settings,item_db,store=None):
        super().__init__(parent);self.settings=settings;self.item_db=item_db;self.store=store
        self.setWindowTitle('관심 아이템 저가 매물 알림');self.resize(1320,680)
        cfg=settings.get('deal_watch') or {};outer=QVBoxLayout(self)
        note=QLabel('최대 20개 · 각 아이템은 최소 10분 간격 확인 · 7일 평균보다 지정 비율 이상 싼 판매 매물만 알림 · 공식 사이트 반영은 노점 개설 후 3분 이상 걸림\n'
                    '같은 상점·가격·수량은 한 번만 알립니다. 알림은 참고용이며 자동 구매하지 않습니다. 앱 실행 중에 동작합니다.')
        note.setWordWrap(True);outer.addWidget(note)
        split=QSplitter(Qt.Horizontal);outer.addWidget(split,1)
        left=QWidget();box=QVBoxLayout(left);box.setContentsMargins(0,0,0,0);split.addWidget(left)
        top=QHBoxLayout();self.enabled=QCheckBox('알림 켜기');self.enabled.setChecked(cfg.get('enabled',False))
        top.addWidget(self.enabled);top.addStretch();self.server=QComboBox()
        for sid,name in SERVERS.items():self.server.addItem(name,sid)
        self.server.setCurrentIndex(max(0,self.server.findData(cfg.get('server','129'))));top.addWidget(QLabel('서버'));top.addWidget(self.server)
        box.addLayout(top)
        # 분류 + 자동완성: 이름 일부만 쳐도 목록이 뜬다(대소문자 무시, 가운데 글자 일치)
        self._cats=categorize_all(item_db);self._entries={}
        for iid,item in (getattr(item_db,'items',{}) or {}).items():
            if isinstance(item,dict) and item.get('name'):self._entries['%s · %d'%(item['name'],iid)]=(iid,item['name'])
        add=QHBoxLayout();self.category=QComboBox();self.category.addItems(CATEGORIES)
        self.search=QLineEdit(placeholderText='아이템 이름 일부 입력 — 예) 오리데오콘, 쉐도우, 카드')
        self.search.setMinimumWidth(220)
        self.model=QStringListModel(self);self.completer=QCompleter(self.model,self)
        self.completer.setFilterMode(Qt.MatchContains);self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setMaxVisibleItems(15);self.search.setCompleter(self.completer)
        self.category.currentTextChanged.connect(self._filter);self._filter()
        self.search.returnPressed.connect(self._add)
        self.percent=QSpinBox(minimum=1,maximum=99,value=15,suffix='%')
        add.addWidget(self.category);add.addWidget(self.search,1);add.addWidget(QLabel('7일 평균보다'));add.addWidget(self.percent)
        button=QPushButton('추가');button.clicked.connect(self._add);add.addWidget(button);box.addLayout(add)
        # 장비: 고르면 국민 옵션(공식 노점에 가장 많이 올라온 제련·등급·슬롯·카드)을 채우고, 그 구성끼리만 비교한다. 필요하면 바꿔서 추가
        self.equip=QWidget();eq=QHBoxLayout(self.equip);eq.setContentsMargins(0,0,0,0)
        self.refine=QSpinBox(minimum=0,maximum=20,prefix='+');self.grade=QComboBox();self.grade.setEditable(True);self.grade.addItem('')
        self.grade.lineEdit().setPlaceholderText('등급 없음');self.slots=QSpinBox(minimum=-1,maximum=4,prefix='슬롯 ')
        self.slots.setSpecialValueText('슬롯 표시 없음');self.slots.setValue(-1)
        self.same_cards=QCheckBox('카드·인챈트도 같은 것만');self.same_cards.setEnabled(False)
        self.reload=QPushButton('국민 옵션 다시 불러오기');self.reload.clicked.connect(lambda:self._load_popular(True))
        for w in (QLabel('장비 구성'),self.refine,self.grade,self.slots,self.same_cards,self.reload):eq.addWidget(w)
        eq.addStretch();box.addWidget(self.equip);self.equip.hide()
        self.popular_note=QLabel();self.popular_note.setWordWrap(True);box.addWidget(self.popular_note)
        self.picked=None;self.popular=None
        self.finder=PO.PopularOption(store) if store is not None else None
        self.task=BackgroundTask(self,self._popular_done,self._popular_error);self.finished.connect(lambda _:self.task.close())
        self.completer.activated[str].connect(self._pick_changed)
        self.search.textEdited.connect(lambda _:self._pick_changed(None))
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['아이템','구성(장비)','분류','기준 할인율(%)','아이템 번호'])
        for i,w in enumerate((200,200,90,100,80)):self.table.setColumnWidth(i,w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        box.addWidget(self.table,1)
        delete=QPushButton('선택 삭제');delete.clicked.connect(self._delete);box.addWidget(delete)
        for item in cfg.get('items',[]):self._row(item)
        # 오른쪽: 추천 매물(저장 시세) + 최근 알림
        right=QWidget();rbox=QVBoxLayout(right);rbox.setContentsMargins(0,0,0,0);split.addWidget(right)
        head=QHBoxLayout();head.addWidget(QLabel('<b>추천 매물</b> — 관심 아이템별 저장된 최저 판매 매물 (요청 없음)'),1)
        again=QPushButton('다시 보기');again.clicked.connect(self.refresh_side);head.addWidget(again);rbox.addLayout(head)
        self.deals=Table([Col('상태',lambda r:('★ ' if r['state']=='추천' else '')+r['state']+(' · %s'%r['age'] if r.get('age') else ''),
                              width=150,tooltip=lambda r:'저장된 시세가 30분보다 오래됨 — 앱이 다음 확인 때 새로 받습니다' if r.get('age') else r['state']),
            Col('매물',lambda r:r['listing_name'] or r['name'],width=190,tooltip=lambda r:r['listing_name'] or r['name']),
            Col('최저가',lambda r:r['price'],comma,width=95),Col('7일 평균',lambda r:r['avg7'],lambda x:'-' if x is None else comma(int(x)),numeric=True,width=95),
            Col('할인',lambda r:r['discount'],_pct,numeric=True,width=65),Col('기준',lambda r:r['percent'],lambda x:'%d%%'%x,numeric=True,width=50),
            Col('상점',lambda r:r['shop'],width=110),Col('확인',lambda r:r['fetched_at'],_when,numeric=True,width=80)])
        rbox.addWidget(self.deals,3)
        rbox.addWidget(QLabel('<b>최근 알림</b>'))
        self.history=Table([Col('알림 시각',lambda r:r['seen_at'],_when,numeric=True,width=90),
            Col('매물',lambda r:r['name'],width=190,tooltip=lambda r:r['name']),Col('가격',lambda r:r['price'],comma,width=95),
            Col('할인',lambda r:r['discount'],_pct,numeric=True,width=65),Col('상점',lambda r:r['shop'],width=110)])
        rbox.addWidget(self.history,2)
        split.setSizes([560,740])
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('저장');buttons.button(QDialogButtonBox.Cancel).setText('취소')
        buttons.accepted.connect(self._save);buttons.rejected.connect(self.reject);outer.addWidget(buttons)
        self.server.currentIndexChanged.connect(lambda _:self.refresh_side());self.refresh_side()

    def _filter(self,*_):
        cat=self.category.currentText()
        keys=sorted(k for k,(iid,_n) in self._entries.items() if cat=='전체' or self._cats.get(iid)==cat)
        self.model.setStringList(keys)

    def _pick(self,text):
        """입력 글 → (id, 이름). '이름 · 번호' 그대로, 같은 이름이 하나뿐인 이름, 또는 지금 분류에서 글을 포함한 것이 하나뿐일 때."""
        text=(text or '').strip()
        if text in self._entries:return self._entries[text]
        same=[v for v in self._entries.values() if v[1]==text]
        if len(same)==1:return same[0]
        hits=[self._entries[k] for k in self.model.stringList() if text and text.lower() in k.lower()]
        return hits[0] if len(hits)==1 else None

    def _items(self):
        out=[]
        for i in range(self.table.rowCount()):
            extra=self.table.item(i,0).data(Qt.UserRole) or {}
            out.append(dict(extra,item_id=int(self.table.item(i,4).text()),name=self.table.item(i,0).text(),
                            percent=self.table.cellWidget(i,3).value()))
        return out

    def _row(self,item):
        i=self.table.rowCount();self.table.insertRow(i)
        conf=item.get('query') or '-'
        if item.get('cards'):conf+=' · '+', '.join(item['cards'])
        for col,text in ((0,item['name']),(1,conf),(2,self._cats.get(item['item_id'],'')),(4,str(item['item_id']))):
            cell=QTableWidgetItem(text);cell.setFlags(cell.flags()&~Qt.ItemIsEditable);self.table.setItem(i,col,cell)
        self.table.item(i,1).setToolTip(conf)
        self.table.item(i,0).setData(Qt.UserRole,{k:item[k] for k in ('query','cards') if item.get(k)})
        spin=QSpinBox(minimum=1,maximum=99,value=int(item.get('percent',15)));spin.valueChanged.connect(lambda _:self.refresh_side())
        self.table.setCellWidget(i,3,spin)

    # ------------------------------------------------------------ 장비 국민 옵션
    def _pick_changed(self,text):
        picked=self._pick(text) if text else None
        self.picked,self.popular=picked,None
        equip=bool(picked and PO.is_equipment(self.item_db,picked[0]))
        self.equip.setVisible(equip);self.popular_note.setText('')
        if equip:self._load_popular()

    def _load_popular(self,force=False):
        if not self.picked or self.finder is None:return
        iid,name=self.picked;server=self.server.currentData()
        if force:self.store.set_meta(self.finder._key(server,iid),'')
        if self.finder.client.retry_after:
            self.popular_note.setText('공식 요청 중지 중(429) · %d초 뒤 다시'%self.finder.client.retry_after);return
        if self.task.start(lambda stop:(iid,self.finder.find(server,iid,name,stop))):
            self.reload.setEnabled(False)
            self.popular_note.setText('%s 국민 옵션 찾는 중… (목록 최대 3쪽 + 상세 최대 5건, 2.5초 간격)'%name)

    def _popular_done(self,value):
        iid,opt=value;self.reload.setEnabled(True)
        if not self.picked or self.picked[0]!=iid:return
        self.popular=opt
        if opt.get('name'):
            self.refine.setValue(opt['refine'])
            if opt['grade'] and self.grade.findText(opt['grade'])<0:self.grade.addItem(opt['grade'])
            self.grade.setCurrentText(opt['grade']);self.slots.setValue(-1 if opt['slots'] is None else opt['slots'])
        self.same_cards.setEnabled(bool(opt.get('cards')));self.same_cards.setChecked(bool(opt.get('cards')))
        self.same_cards.setText('카드·인챈트도 같은 것만: '+', '.join(opt['cards']) if opt.get('cards') else '카드·인챈트도 같은 것만')
        self.popular_note.setText('국민 옵션 — '+PO.describe(opt)+' · 바꾸려면 제련·등급·슬롯을 고친 뒤 추가')

    def _popular_error(self,exc):
        self.reload.setEnabled(True)
        self.popular_note.setText(str(exc) if isinstance(exc,RateLimited) else '국민 옵션을 받지 못했습니다: %s'%exc)

    def _add(self):
        if self.table.rowCount()>=MAX_WATCH_ITEMS:QMessageBox.information(self,'등록 한도','최대 20개까지 등록할 수 있습니다.');return
        picked=self._pick(self.search.text())
        if not picked:QMessageBox.information(self,'아이템 선택','자동완성 목록에서 아이템을 고르세요(이름 일부를 치면 목록이 뜹니다).');return
        iid,name=picked;item=dict(item_id=iid,name=name,percent=self.percent.value())
        if PO.is_equipment(self.item_db,iid) and self.picked and self.picked[0]==iid:
            opt=self.popular or {}
            item['query']=join_name(self.refine.value(),self.grade.currentText().strip().upper(),opt.get('base') or name,
                                    None if self.slots.value()<0 else self.slots.value())
            if self.same_cards.isChecked() and opt.get('cards'):item['cards']=list(opt['cards'])
        if any(int(self.table.item(i,4).text())==iid and (self.table.item(i,0).data(Qt.UserRole) or {}).get('query')==item.get('query')
               for i in range(self.table.rowCount())):return
        self._row(item);self.search.clear();self._pick_changed(None);self.refresh_side()

    def _delete(self):
        for i in sorted({x.row() for x in self.table.selectedIndexes()},reverse=True):self.table.removeRow(i)
        self.refresh_side()

    def refresh_side(self):
        if self.store is None:return
        server=self.server.currentData()
        self.deals.set_rows(deal_candidates(self.store,server,self._items()))
        self.history.set_rows(recent_alerts(self.store,server))

    def _save(self):
        try:items=validate_watchlist(self._items())
        except ValueError as exc:QMessageBox.warning(self,'목록 확인',str(exc));return
        self.settings['deal_watch']=dict(enabled=self.enabled.isChecked(),server=self.server.currentData(),items=items)
        self.settings.save();self.accept()
