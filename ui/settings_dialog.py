import os

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget, QScrollArea, QVBoxLayout)

from config.settings import PRICE_BASIS, SORT_KEYS
from services.price_provider import PROVIDERS
from ui import overlay_styles


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle('설정')
        self.s = settings
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        f = QFormLayout(body)
        self.resize(760, 800)

        self.replay_dir = QLineEdit(settings['replay_dir'])
        f.addRow('Replay 폴더', self._with_browse(self.replay_dir))
        self.ro_dir = QLineEdit(settings['ro_dir'])
        f.addRow('라그나로크 설치 폴더', self._with_browse(self.ro_dir))
        self.cache = QSpinBox(minimum=0, maximum=10080, suffix=' 분')
        self.cache.setValue(int(settings['cache_minutes']))
        f.addRow('시세 캐시 시간', self.cache)
        self.basis = QComboBox()
        for k, v in PRICE_BASIS.items():
            self.basis.addItem(v, k)
        self.basis.setCurrentIndex(list(PRICE_BASIS).index(settings['price_basis']))
        f.addRow('기본 가격 기준', self.basis)
        self.sell_min = QSpinBox(minimum=0, maximum=2_000_000_000, singleStep=10_000, suffix=' Z')
        self.sell_min.setGroupSeparatorShown(True)
        self.sell_min.setValue(int(settings['sell_min_value']))
        f.addRow('팔 만한 아이템 최소금액', self.sell_min)
        self.sort = QComboBox()
        for k, v in SORT_KEYS.items():
            self.sort.addItem(v, k)
        self.sort.setCurrentIndex(list(SORT_KEYS).index(settings['default_sort']))
        f.addRow('기본 정렬', self.sort)
        self.provider = QComboBox()
        for k, p in PROVIDERS.items():
            self.provider.addItem(p.label, k)
        self.provider.setCurrentIndex(list(PROVIDERS).index(settings['provider']) if settings['provider'] in PROVIDERS else 0)
        f.addRow('시세 Provider', self.provider)
        self.outlier = QCheckBox('평균가 계산 시 이상치(IQR) 제거')
        self.outlier.setChecked(bool(settings['outlier_filter']))
        f.addRow('', self.outlier)
        self.merge = QCheckBox('같은 위치의 일반 아이템 수량 합산')
        self.merge.setChecked(bool(settings['merge_stackables']))
        f.addRow('', self.merge)
        self.db_path = QLineEdit(settings['db_path'])
        f.addRow('DB 위치 (시세+Replay 기록, 재시작 후 적용)', self.db_path)

        f.addRow(QLabel('<b>Replay 분석</b>'))
        self.auto_watch = QCheckBox('Replay 폴더 자동 감시 (새 .rrf 감지)')
        self.auto_watch.setChecked(bool(settings['auto_watch']))
        f.addRow('', self.auto_watch)
        self.auto_analyze = QCheckBox('새 Replay 감지 시 자동 분석')
        self.auto_analyze.setChecked(bool(settings['auto_analyze']))
        f.addRow('', self.auto_analyze)
        self.profit_basis = QComboBox()
        for k, v in PRICE_BASIS.items():
            self.profit_basis.addItem(v, k)
        self.profit_basis.setCurrentIndex(list(PRICE_BASIS).index(settings['profit_basis']))
        f.addRow('수익 계산 가격 기준', self.profit_basis)
        self.rare = QSpinBox(minimum=0, maximum=2_000_000_000, singleStep=100_000, suffix=' Z')
        self.rare.setGroupSeparatorShown(True)
        self.rare.setValue(int(settings['rare_item_value']))
        f.addRow('희귀 드랍 강조 기준(단가)', self.rare)
        self.gap = QSpinBox(minimum=1, maximum=60, suffix=' 초')
        self.gap.setValue(int(settings['combat_gap_ms']) // 1000)
        f.addRow('비전투 판정 간격', self.gap)
        self.dev = QCheckBox('개발자 모드 (패킷 인스펙터 탭)')
        self.dev.setChecked(bool(settings['developer_mode']))
        f.addRow('', self.dev)
        self.experimental = QCheckBox('실험적 패킷 해석 (샘플로 검증 못 한 후보 정의 사용, 결과에 △ 표시)')
        self.experimental.setChecked(bool(settings['experimental_packets']))
        f.addRow('', self.experimental)

        f.addRow(QLabel('<b>Passive Live (화면 기반 — 게임 입력/메모리 접근 없음)</b>'))
        self.live_on = QCheckBox('프로그램 시작 시 Ragnarok 자동 감지')
        self.live_on.setChecked(bool(settings['live_enabled']))
        f.addRow('', self.live_on)
        self.live_proc = QLineEdit(', '.join(settings['live_process_names']))
        f.addRow('게임 프로세스 이름(,)', self.live_proc)
        self.live_hud_auto = QCheckBox('게임 감지 시 HUD 자동 표시')
        self.live_hud_auto.setChecked(bool(settings['live_auto_overlay']))
        f.addRow('', self.live_hud_auto)
        self.live_replay = QCheckBox('Replay 녹화 감지 시 HYBRID 자동 전환')
        self.live_replay.setChecked(bool(settings['live_replay_auto']))
        f.addRow('', self.live_replay)
        self.replay_timing = {}
        for key, label, minimum, maximum, default in (
                ('live_reorder_ms', 'Replay 순서 안정화 (ms, 다음 세션부터)', 0, 5000, 250),
                ('live_snapshot_ms', 'LIVE 화면 갱신 (ms, 재시작 후)', 100, 1000, 200),
                ('live_poll_ms', 'Replay 읽기 간격 (ms)', 20, 2000, 100),
                ('live_stall_sec', '기록 정체 표시 (초)', 1, 120, 5),
                ('live_finalize_sec', '기록 종료 추정 (초)', 10, 3600, 60)):
            box = QSpinBox(minimum=minimum, maximum=maximum)
            box.setValue(int(settings.get(key, default)))
            self.replay_timing[key] = box
            f.addRow(label, box)
        self.live_fps = QSpinBox(minimum=1, maximum=15, suffix=' fps')
        self.live_fps.setValue(int(settings['live_fps']))
        f.addRow('분석 빈도(활동 중)', self.live_fps)
        self.live_pause = QSpinBox(minimum=10, maximum=3600, suffix=' 초')
        self.live_pause.setValue(int(settings['live_pause_sec']))
        f.addRow('자동 일시정지', self.live_pause)
        self.a_hp, self.a_sp, self.a_wt = (QSpinBox(minimum=0, maximum=100, suffix=' %') for _ in range(3))
        self.a_hp.setValue(int(settings['alert_hp']))
        self.a_sp.setValue(int(settings['alert_sp']))
        self.a_wt.setValue(int(settings['alert_weight']))
        f.addRow('HP 경고(미만, 0=끔)', self.a_hp)
        f.addRow('SP 경고(미만, 0=끔)', self.a_sp)
        f.addRow('무게 경고(이상, 0=끔)', self.a_wt)
        self.hv = QSpinBox(minimum=0, maximum=2_000_000_000, singleStep=1_000_000, suffix=' Z')
        self.hv.setGroupSeparatorShown(True)
        self.hv.setValue(int(settings['high_value_threshold']))
        f.addRow('고가 드랍 알림 기준', self.hv)
        self.jackpot = QSpinBox(minimum=0, maximum=2_000_000_000, singleStep=1_000_000, suffix=' Z')
        self.jackpot.setGroupSeparatorShown(True)
        self.jackpot.setValue(int(settings['jackpot_min_price']) if settings['jackpot_enabled'] else 0)
        self.jackpot.setToolTip('리플레이를 켠 채 사냥할 때, 얻은 아이템 1개의 노점 시세가 이 값 이상이면 축하 효과음을 냅니다.')
        f.addRow('득템 축하 효과음 기준(노점 시세, 0=끔)', self.jackpot)
        self.overcharge = QSpinBox(minimum=0, maximum=24, suffix=' %')
        self.overcharge.setValue(int(settings['overcharge_rate']))
        self.overcharge.setToolTip('획득 아이템 표의 "상점가"에 적용합니다. 오버차지 Lv10 = 24%, 스킬이 없으면 0.')
        f.addRow('상점 판매 오버차지', self.overcharge)
        self.overcharge_auto = QCheckBox('오버챠지 레벨을 리플레이의 스킬 목록에서 읽어 자동으로 맞춤')
        self.overcharge_auto.setChecked(bool(settings['overcharge_auto']))
        seen = settings.get('overcharge_seen') or {}
        self.overcharge_auto.setToolTip('읽은 값: ' + (', '.join('%s Lv%s' % kv for kv in seen.items()) or '아직 없음'))
        f.addRow('', self.overcharge_auto)
        self.drop_alert = QCheckBox('고가 아이템이 바닥에 떨어지면(줍기 전) 알림')
        self.drop_alert.setChecked(bool(settings['drop_alert']))
        f.addRow('', self.drop_alert)
        self.jackpot_sound = QLineEdit(settings['jackpot_sound'])
        self.jackpot_sound.setPlaceholderText('비우면 기본: 효과음 + "득템 축하드려요!" 음성 · wav 파일')
        f.addRow('득템 효과음 파일', self._with_file(self.jackpot_sound, '효과음 (*.wav)'))
        self.hud_preset = QComboBox()
        for k in ('compact', 'hunt', 'status', 'detailed'):
            self.hud_preset.addItem(k, k)
        self.hud_preset.setCurrentIndex(max(0, self.hud_preset.findData(settings['hud_preset'])))
        f.addRow('HUD 프리셋', self.hud_preset)
        f.addRow(QLabel('<b>Overlay / 알림</b>'))
        self.hud_warn, self.hud_danger = (QSpinBox(minimum=0, maximum=100, suffix=' %') for _ in range(2))
        self.hud_warn.setValue(int(settings['hud_warn_pct']))
        self.hud_danger.setValue(int(settings['hud_danger_pct']))
        f.addRow('HUD 노란색 기준(HP/SP/AP 이하)', self.hud_warn)
        f.addRow('HUD 빨간색 기준(HP/SP/AP 이하)', self.hud_danger)
        self.alert_cd = QSpinBox(minimum=1, maximum=3600, suffix=' 초')
        self.alert_cd.setValue(int(settings['alert_cooldown']))
        f.addRow('같은 경고 반복 간격', self.alert_cd)
        self.market_interval = QDoubleSpinBox(minimum=1.0, maximum=6.0, singleStep=0.1, decimals=1, suffix=' 초')
        self.market_interval.setValue(float(settings['market_interval']))
        self.market_interval.setToolTip('공식 홈페이지 요청 사이 간격. 짧을수록 빠르지만 429로 막히면 5분 전체가 멈춥니다. '
                                        '막히면 자동으로 간격을 늘립니다.')
        f.addRow('노점 시세 요청 간격(빠르게 ← → 안전하게)', self.market_interval)
        self.st_warn, self.st_crit, self.st_reuse = (QSpinBox(minimum=0, maximum=3600, suffix=' 초') for _ in range(3))
        self.st_warn.setValue(int(settings['status_warn_sec']))
        self.st_crit.setValue(int(settings['status_critical_sec']))
        self.st_reuse.setValue(int(settings['status_reuse_sec']))
        f.addRow('효과 종료 임박 기준(남은 시간)', self.st_warn)
        f.addRow('곧 종료 기준(남은 시간)', self.st_crit)
        f.addRow('효과 종료 후 재사용 알림 유지(0=끔)', self.st_reuse)
        self.st_internal = QCheckBox('우편 개수 같은 게임 내부 상태도 표시')
        self.st_internal.setChecked(bool(settings['status_show_internal']))
        f.addRow('', self.st_internal)
        self.st_nonbuff = QCheckBox('버프가 아닌 것도 표시 (무게 70%·탑승처럼 타이머 없는 상태, 상태이상)')
        self.st_nonbuff.setChecked(bool(settings['status_show_nonbuff']))
        f.addRow('', self.st_nonbuff)
        self.buff_auto = QCheckBox('게임 감지 시 버프 타이머 오버레이 자동 표시')
        self.buff_auto.setChecked(bool(settings['buff_overlay_auto']))
        f.addRow('', self.buff_auto)
        self.buff_keep = QCheckBox('만료된 버프 타이머를 목록에 남기기')
        self.buff_keep.setChecked(bool(settings['buff_keep_expired']))
        f.addRow('', self.buff_keep)
        self.storage_auto = QCheckBox('게임 감지 시 창고 시세 오버레이 자동 표시')
        self.storage_auto.setChecked(bool(settings['storage_overlay_auto']))
        f.addRow('', self.storage_auto)
        self.tray_min = QCheckBox('최소화하면 트레이로')
        self.tray_min.setChecked(bool(settings['minimize_to_tray']))
        f.addRow('', self.tray_min)
        f.addRow(QLabel('<b>오버레이 모양</b>'))
        hint = QLabel('각 오버레이의 오른쪽 클릭 메뉴 [모양]에서도 바꿀 수 있습니다')
        hint.setWordWrap(True)
        f.addRow('', hint)
        self.styles = {}
        for name, (label, options, _default) in overlay_styles.STYLES.items():
            box = QComboBox()
            for key, text in options:
                box.addItem(text, key)
            box.setCurrentIndex(max(0, box.findData(overlay_styles.get(settings, name))))
            self.styles[name] = box
            f.addRow(label, box)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _with_browse(self, edit):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit)
        b = QPushButton('찾기')
        b.clicked.connect(lambda: edit.setText(QFileDialog.getExistingDirectory(self, '폴더 선택', edit.text()) or edit.text()))
        h.addWidget(b)
        return w

    def _with_file(self, edit, pattern):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit)
        pick = QPushButton('찾기')
        pick.clicked.connect(lambda: edit.setText(
            QFileDialog.getOpenFileName(self, '파일 선택', edit.text(), pattern)[0] or edit.text()))
        h.addWidget(pick)
        play = QPushButton('들어 보기')
        play.clicked.connect(lambda: self._preview_sound(edit.text().strip()))
        h.addWidget(play)
        say = QPushButton('문구 바꾸기')
        say.setToolTip('적은 문구를 Windows 한국어 음성으로 읽어 효과음 뒤에 붙입니다(인터넷으로 보내지 않습니다).')
        say.clicked.connect(lambda: self._make_voice(edit))
        h.addWidget(say)
        return w

    def _make_voice(self, edit):
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from config.settings import BASE_DIR
        from ui.loot_alert import VOICE_TEXT, make_voice_sound
        text, ok = QInputDialog.getText(self, '득템 음성 문구', '득템했을 때 읽어 줄 문구:', text=VOICE_TEXT)
        if not ok or not text.strip():
            return
        out = os.path.join(BASE_DIR, 'data', 'sounds', 'jackpot_custom.wav')
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            make_voice_sound(text.strip(), out)
        except Exception as exc:
            QMessageBox.warning(self, '득템 음성 문구', str(exc))
            return
        edit.setText(out)
        self._preview = None                      # 같은 파일 이름을 다시 만들었으니 캐시된 소리를 버린다
        self._preview_sound(out)

    def _preview_sound(self, path):
        from ui.loot_alert import sound_path
        from utilities.alerts import AlertManager
        self._preview = getattr(self, '_preview', None) or AlertManager()
        self._preview._play(sound_path(dict(jackpot_sound=path)))

    def apply(self):
        self.s.update({key: box.value() for key, box in self.replay_timing.items()})
        self.s['live_finalize_sec'] = max(self.s['live_finalize_sec'], self.s['live_stall_sec'] + 1)
        self.s.update(replay_dir=self.replay_dir.text().strip(), ro_dir=self.ro_dir.text().strip(),
                      cache_minutes=self.cache.value(), price_basis=self.basis.currentData(),
                      sell_min_value=self.sell_min.value(), default_sort=self.sort.currentData(),
                      provider=self.provider.currentData(), outlier_filter=self.outlier.isChecked(),
                      merge_stackables=self.merge.isChecked(), db_path=self.db_path.text().strip() or 'data/market.db',
                      replay_dir_confirmed=True, auto_watch=self.auto_watch.isChecked(),
                      auto_analyze=self.auto_analyze.isChecked(), profit_basis=self.profit_basis.currentData(),
                      rare_item_value=self.rare.value(), combat_gap_ms=self.gap.value() * 1000,
                      developer_mode=self.dev.isChecked(), experimental_packets=self.experimental.isChecked(),
                      live_enabled=self.live_on.isChecked(),
                      live_process_names=[x.strip() for x in self.live_proc.text().split(',') if x.strip()] or ['Ragexe.exe'],
                      live_auto_overlay=self.live_hud_auto.isChecked(), live_replay_auto=self.live_replay.isChecked(),
                      live_fps=self.live_fps.value(), live_pause_sec=self.live_pause.value(),
                      alert_hp=self.a_hp.value(), alert_sp=self.a_sp.value(), alert_weight=self.a_wt.value(),
                      high_value_threshold=self.hv.value(), hud_preset=self.hud_preset.currentData(),
                      market_interval=self.market_interval.value(),
                      status_warn_sec=self.st_warn.value(), status_critical_sec=self.st_crit.value(),
                      status_reuse_sec=self.st_reuse.value(), status_show_internal=self.st_internal.isChecked(),
                      status_show_nonbuff=self.st_nonbuff.isChecked(),
                      jackpot_enabled=self.jackpot.value() > 0, jackpot_sound=self.jackpot_sound.text().strip(),
                      overcharge_rate=self.overcharge.value(), overcharge_auto=self.overcharge_auto.isChecked(),
                      drop_alert=self.drop_alert.isChecked(),
                      jackpot_min_price=self.jackpot.value() or self.s['jackpot_min_price'],
                      hud_warn_pct=self.hud_warn.value(), hud_danger_pct=self.hud_danger.value(),
                      alert_cooldown=self.alert_cd.value(), buff_overlay_auto=self.buff_auto.isChecked(),
                      buff_keep_expired=self.buff_keep.isChecked(), storage_overlay_auto=self.storage_auto.isChecked(),
                      minimize_to_tray=self.tray_min.isChecked())
        for name, box in self.styles.items():
            overlay_styles.set_style(self.s, name, box.currentData(), save=False)
        self.s.save()
