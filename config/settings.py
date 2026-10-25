"""settings.json 로드/저장. 없는 키는 기본값으로 채운다."""
import json
import logging
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(BASE_DIR, 'config', 'settings.json')

APP_NAME = '라그메이트'                     # 화면에 보이는 프로그램 이름 (패키지/모듈 이름은 그대로 둔다)
APP_SUBTITLE = '라그나로크 플레이 도우미'
APP_TITLE = '%s · %s' % (APP_NAME, APP_SUBTITLE)
APP_VERSION = '2026.09'

PRICE_BASIS = {'lowest': '최저가 기준', 'median': '중앙값 기준', 'average': '평균가 기준'}
SORT_KEYS = {'value_desc': '예상금액 높은 순', 'name_asc': '아이템명', 'quantity_desc': '수량 많은 순'}

DEFAULTS = {
    'replay_dir': r'C:\Gravity\Ragnarok\Replay',
    'ro_dir': r'C:\Gravity\Ragnarok',          # itemInfo_true.lub 위치(System 폴더의 상위)
    'replay_dir_confirmed': False,             # 최초 1회 폴더 확인 여부
    'cache_minutes': 30,
    'price_basis': 'lowest',
    'sell_min_value': 100_000,
    'default_sort': 'value_desc',
    'provider': 'local',                       # local | mock
    'db_path': 'data/market.db',               # 상대경로는 프로그램 폴더 기준
    'max_concurrency': 2,                      # 원격 Provider 동시 요청 수
    'request_interval': 1.5,                   # 원격 Provider 요청 간격(초)
    'outlier_filter': True,                    # 평균가 계산 시 IQR 이상치 제거
    'merge_stackables': True,                  # 같은 위치의 일반 아이템 수량 합산
    # ---- Replay 분석 ----
    'auto_watch': False,                       # Replay 폴더 감시(새 .rrf 감지)
    'auto_analyze': True,                      # 감지 시 자동 분석
    'profit_basis': 'lowest',                  # 수익 계산 가격 기준
    'rare_item_value': 1_000_000,              # 희귀 드랍 강조 기준(단가)
    'combat_gap_ms': 3000,                     # 이 간격 이상 피해가 없으면 비전투
    'developer_mode': False,                   # 패킷 인스펙터 표시
    'experimental_packets': False,             # 미검증 후보 패킷 정의 사용(△ 표시)
    'expected_delays': {},                     # {skill_id: 기대 사용간격(초)} 스킬 효율 비교용
    # ---- RO Navigator / Overlay ----
    'navigator_mode': 'beginner',              # beginner | expert
    'overlays': {},                            # {key: {pos, opacity, click_through}}
    # ---- Passive Live Mode (화면 캡처/OCR 기반, 게임 입력·메모리 접근 없음) ----
    'live_enabled': True,                      # 프로그램 시작 시 Ragnarok 자동 감지
    'live_process_names': ['Ragexe.exe', 'Ragnarok.exe'],
    'live_auto_overlay': True,                 # 게임 감지 시 HUD 자동 표시
    'live_replay_auto': True,                  # Replay 녹화 감지 시 HYBRID 자동 전환
    'packet_profile': 'kro_2026',
    'live_reorder_ms': 250,                    # conservative default; tune with local fixtures
    'live_snapshot_ms': 200,
    'live_poll_ms': 100,
    'live_stall_sec': 5,
    'live_finalize_sec': 60,                   # inactivity heuristic, resumes if file grows
    'live_ocr_lang': 'ko',
    'live_fps': 5,                             # 활동 중 분석 빈도
    'live_idle_fps': 1,                        # 대기 중 분석 빈도
    'live_pause_sec': 120,                     # 이 시간 활동 없으면 자동 일시정지
    'live_start_window': 60,                   # 세션 시작 판정: 이 시간(초) 안에
    'live_start_min': 2,                       #   활동 이벤트 N회
    'alert_hp': 30, 'alert_sp': 20, 'alert_weight': 85, 'alert_ap_min': 0,
    'alert_cooldown': 30,                      # 같은 알림 반복 간격(초)
    'high_value_threshold': 5_000_000,
    # 득템 알림(ui/loot_alert): LIVE 리플레이에서 얻은 아이템의 노점 시세(1개)가 이 값 이상이면 축하 효과음. 0 = 끔
    'jackpot_enabled': True, 'jackpot_min_price': 10_000_000,
    'jackpot_sound': '',                       # 비우면 기본 효과음(ui/assets/jackpot.wav). wav 파일만
    'overcharge_rate': 24,                     # 상점에 팔 때 오버차지 %(Lv10 = 24, 스킬 없음 = 0) — 획득 표의 '상점가'
    'overcharge_auto': True,                   # 리플레이의 스킬 목록에서 오버챠지 레벨을 읽어 위 값을 맞춘다
    'overcharge_seen': {},                     # {캐릭터: 오버챠지 레벨} 리플레이에서 읽은 것(가장 높은 레벨을 쓴다)
    # 인던 대기 순번이 이 값 이하가 되면 알림(Live 화면의 [대기열 알림] 옆에서 1~30 선택).
    # 예전 이름 queue_alert_at(기본 10)은 읽지 않는다 — 모든 기기가 15에서 다시 시작한다.
    'queue_alert': False, 'queue_alert_below': 15,
    # 대기열 알림 때 '탈퇴 키를 누르라'고 함께 안내한다(게임 Alt+M 매크로에 탈퇴 명령을 넣어 둔 키). 누르는 것은 사용자
    'queue_leave_hint': False, 'queue_leave_key': 'Alt+0',
    'queue_banner': True,                      # 대기 중인 게임 창 위에 순위를 크게 표시
    # 레벨업 코치(오버레이 메뉴): 코스 · 직접 정한 레벨(0 = 리플레이에서 읽기) · 직접 저장한 네비 {단계: dict(map, x, y)}
    'leveling_route': 'fast_skip', 'leveling_manual_level': 0, 'leveling_navi': {},
    'leveling_routes': {},                     # {캐릭터 이름: 코스} 화면에서 읽은 캐릭터마다 고른 코스
    'drop_alert': True,                        # 고가 아이템이 바닥에 떨어졌을 때(줍기 전) 알림 — 기준은 jackpot_min_price
    'hud_preset': 'hunt',                      # compact | hunt | status | detailed
    'hud_scale': 1.0,
    'hud_items': {},                           # HUD 메뉴에서 켜고 끈 항목 {key: bool} (프리셋 위에 덮어씀)
    'hud_layout': 'vertical',                  # vertical | horizontal
    'hud_warn_pct': 30,                        # HP/SP/AP 노란색 기준(%)
    'hud_danger_pct': 10,                      # HP/SP/AP 빨간색 기준(%)
    # ---- Buff Timer (사용자가 직접 등록한 타이머. 화면에서 Buff 아이콘을 읽지 않는다) ----
    'buff_timers': [],                         # [{name, duration(초), alert_sec, sound}]
    'buff_keep_expired': False,                # 만료된 타이머를 목록에 남길지
    'buff_warn_pct': 30,                       # 남은 비율 경고(노랑)
    'buff_danger_pct': 10,                     # 남은 비율 위험(빨강)
    'buff_overlay_auto': False,                # 게임 감지 시 Buff 오버레이 자동 표시
    # ---- LIVE 상태 모니터(버프·소모품·스킬 효과) ----
    # 공식 홈페이지 요청 간격(초). 짧을수록 빠르지만 429로 막히면 5분 전체 중지라 결국 느려진다
    'market_interval': 1.2,
    'status_warn_sec': 60,                     # 남은 시간이 이 아래면 '종료 임박'
    'status_critical_sec': 10,                 # 이 아래면 '곧 종료'
    'status_reuse_sec': 60,                    # 효과가 끝난 뒤 '재사용!!'을 몇 초 보여줄지 (0=표시 안 함)
    'status_favorites': [],                    # 항상 위에 고정할 효과 key
    'status_alerts': {},                       # {key: {warn, critical, reuse, sound}} 효과별 알림 설정
    'status_show_internal': False,             # 우편 개수 같은 게임 내부 상태까지 볼지
    'status_show_nonbuff': False,              # 버프가 아닌 것(무게 70%·탑승 같은 타이머 없는 상태, 상태이상)도 볼지
    'status_max_items': 12,
    'buff_display': 'list',                    # 오버레이 표시 방식: list | compact | icon
    'buff_hide_expired': True,                 # 만료된 줄 감추기
    'buff_hide_long': True,                    # 24시간 넘는 상시 버프 감추기
    'buff_max_rows': 12,                       # 한 번에 보여줄 최대 개수(0=전부)
    'buff_overlay_under_sec': 30,              # 오버레이에는 남은 시간이 이 초 이하인 것 + '재사용!!'만(0=전부)
    'buff_windows_popup': False,               # 버프 종료·임박 알림을 Windows 알림으로도 띄울지(기본: 오버레이만)
    'buff_icon_size': 26,                      # 아이콘 보기 한 칸 크기(px)
    'buff_icon_columns': 6,                    # 아이콘 보기 가로 개수
    # ---- 창고 시세 Overlay ----
    'storage_overlay_prices': ['sale'],        # 표시할 가격: sale(판매 최저가) · average(거래 평균가)
    'storage_overlay_only_sellable': True,     # '팔 만한 것만'(sell_min_value 이상)
    'storage_overlay_auto': False,             # 게임 감지 시 창고 시세 오버레이 자동 표시
    'storage_grid': {},                        # 확인한 슬롯 배치·게임 기준 좌표, 가격은 실시간 DB 참조
    'debug_ocr': False,                        # OCR 진단 기록(이미지는 저장하지 않음)
    # ---- 매크로 (core/macros.py). 트리거 키를 누를 때만 동작 ----
    'macros': [],                              # [{name, key, mod, mode: hold|once, gap, enabled, steps}]
    'macro_app_enabled': False,                # 매크로 켜기
    'macro_game_only': True,                   # 라그나로크 창이 맨 앞일 때만 실행
    'macro_skill_keys': {},                    # {캐릭터 이름: {스킬 id: 키}} 리플레이 단축키 창·직접 지정 (콤보 만들기용)
    'buff_routine': {},                        # 버프 루틴 순서 {name, steps:[{skill_id, name, scope, target, hotkey, note}]}
    'vend_assist': True,                       # 게임 '노점 개설' 창을 화면에서 찾아 추천 판매가 패널 표시(리플레이 없어도 됨)
    # 관심 아이템 저가 매물 알림(ui/deal_watch_dialog). 여기 없으면 다시 켤 때 저장한 목록을 버린다
    'deal_watch': {'enabled': False, 'server': '129', 'items': []},
    # +10 쉐도우 저가 TOP 20 자동 확인(services/shadow_deals). families=None 이면 기본 계열 전체
    'shadow_watch': {'enabled': False, 'server': '129', 'families': None, 'percent': 15},
    # 고가 매물: 기준 시세 1억 이상, 기준가의 80% 이하, 세후 예상 차익 알림
    'resale_watch': None,
    # 노점 검색 창(ui/vend_search_dialog): 마지막 서버·옵션·최근 검색어
    'vend_search': {'server': '129', 'all_words': True, 'pages': 2, 'recent': []},
    # 오버레이 모양(ui/overlay_styles): 퀘스트 card|steps|strip, 노점 table|cards, 창고 table|summary
    'overlay_style': {'quest': 'card', 'vend': 'table', 'storage': 'table'},
    'minimize_to_tray': False,
    'ui_zoom': 1.0,                            # 화면 글자·여백 배율(Ctrl +/-/0). 창이 작으면 여기서 더 줄어든다
    'hotkeys': {'hud': 'Ctrl+Alt+H', 'compact': 'Ctrl+Alt+C', 'nav': 'Ctrl+Alt+N', 'buff': 'Ctrl+Alt+B',
                'storage': 'Ctrl+Alt+S', 'storage_grid': 'Ctrl+Alt+G', 'pause': 'Ctrl+Alt+P',
                # 등록한 버프 타이머 1~4번을 게임 화면을 보면서 바로 시작/갱신 (게임에는 입력하지 않음)
                'buff1': 'Ctrl+Alt+1', 'buff2': 'Ctrl+Alt+2', 'buff3': 'Ctrl+Alt+3', 'buff4': 'Ctrl+Alt+4'},
}


class Settings(dict):
    def __init__(self, path=SETTINGS_PATH):
        super().__init__(DEFAULTS)
        self.path = path
        try:
            if os.path.exists(path):
                with open(path, encoding='utf-8') as f:
                    self.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
                # 예전 설정 파일에 없던 단축키는 기본값을 채운다(새로 생긴 기능이 등록되지 않는 것을 막는다)
                self['hotkeys'] = dict(DEFAULTS['hotkeys'], **(self.get('hotkeys') or {}))
        except Exception:
            logging.getLogger(__name__).exception('설정 파일을 읽지 못해 기본값 사용: %s', path)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(dict(self), f, ensure_ascii=False, indent=2)

    def abs_path(self, key):
        p = self[key]
        return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)
