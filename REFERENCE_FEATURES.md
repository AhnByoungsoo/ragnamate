# 라그타임 UX 벤치마킹 정리

`라그타임` 배포본 가운데 사용자 설정 파일(`data/config.json`, `data/status_config.json`, `data/hud_magnifier.json`)의 **항목 이름**만 보고 기능 개념을 정리했습니다.
실행 파일 디컴파일, 코드나 리소스 추출, 라이선스·업데이트 서버 분석은 하지 않았습니다. 네트워크 주소와 라이선스 관련 내용도 여기에 적지 않았습니다.
설정 파일에 들어 있던 화면 좌표(ROI/HP·SP 박스), 사운드 파일, 프리셋 값은 **가져오지 않았습니다**. 영역은 이 프로그램에서 직접 보정합니다.

| 기능 | 라그타임에서 확인된 개념(설정 항목) | 우리 적용 여부 | 적용 방식 | 우선순위 |
|---|---|---|---|---|
| Navigation Mini Overlay | (라그타임에는 없음) Overlay 창 개념 참고 | **적용함** | `ui/overlay.py` NavOverlay: 항상 위, 투명도, Click-through, 위치 저장, 복사/완료/이전/다음 | 1 |
| 공통 Overlay 기반 | overlay 표시/숨김, 위치 고정(`lock_pos`), 배경 투명도(`bg_alpha`), 레이아웃 | **기반 구현** | `OverlayWindow`(위치/투명도/Click-through 저장). Buff/Status/Storage Overlay에서 재사용 예정 | 1 |
| 사용자 Preset | 동적 버프 프리셋, 2K/4K 템플릿 프리셋 | **적용함(일부)** | HUD 프리셋 4종 + 표시 항목 on/off(`hud_items`), 해상도별 Region 프로필. 버프 프리셋 묶음은 미구현 | 2 |
| 전역 토글 Hotkey | 전역 hotkey 켜기/끄기, 토글 키(Pause), 전체 일시정지 | **적용함** | Ctrl+Alt+H/C/N/B/S/P + 1~4(버프 타이머 1~4번 시작/갱신). 게임에 입력은 보내지 않음 | 3 |
| 알림 시스템 공통화 | 만료 알림(초), 알림음, 반복 알림, 볼륨, 임계치 경고 | **기반 구현** | `utilities/alerts.py` AlertManager(소리/팝업/강조/임계치/반복/음소거) | 4 |
| 고정 Buff Timer | 고정 버프(이름·지속시간·알림 시각·소리) | **적용함** | `live/buffs.py` + Live 탭 '버프 타이머' + `ui/buff_overlay.py`. 지속시간은 사용자가 입력(게임에서 읽지 않음) | 4 |
| 동적 Buff Timer | 동적 버프 목록, 만료 후 유지 옵션 | **적용함(일부)** | 만료 후 유지 옵션 구현. 자동 감지는 Replay의 BUFF_START remain time(●)만 사용. 화면 아이콘 인식은 미구현 | 5 |
| Status HUD (HP/SP/AP/Lv/무게) | 상태창 영역 지정, 표시 항목 선택, 추적 창 제목 | **적용함** | Region 보정 + HUD 표시 항목 메뉴 + 게임 창 따라가기. Lv은 Replay 메타데이터에만 있음 | 5 |
| HP/SP/무게 임계치 경고 | HP·SP 경고 %, 무게 경고 %, 반복 대기 | **적용함** | AlertManager 임계치 + 반복 간격(`alert_cooldown`), HUD 색(노랑/빨강) | 5 |
| 창고 시세 Overlay | 창고 Overlay, 표시할 가격 선택(`storage_overlay_prices`), 자동 시작 | **적용함** | `ui/storage_overlay.py`: 검색·팔 만한 것만·판매 최저가/거래 평균가 열·시세 시각. 가격은 기존 로컬 시세 DB | 5 |
| HUD Magnifier | 확대 배율, 영역, Click-through, 창 위치 | 선택 기능 | Overlay 기반의 화면 확대(캡처만 사용) | 6 |
| Cursor Highlight | 크기, 두께, 투명도, 색 | 낮음 | Overlay 기반 | 7 |
| 2K / 4K UI Preset, DPI | 템플릿 해상도 프리셋, DPI 배율 기록 | **적용함(기본)** | Qt High-DPI PassThrough(100/125/150/200%), 논리 픽셀 기준 위치 저장 | - |
| 창 위치 저장 | Overlay·설정창 위치 | **적용함** | Overlay 위치를 settings.json `overlays`에 저장 | - |
| 채팅 시 편의 처리 | 채팅 모드 | 보류 | 게임 입력을 가로채거나 보내는 방식은 사용하지 않음 | - |
| 클라이언트 최적화류 기능 | 최적화 시작 옵션 | **적용 안 함** | 게임 프로세스를 조작하는 기능은 범위 밖 | - |

## 원칙
- 게임 메모리 접근, DLL/패킷 인젝션, 자동 이동, NPC 자동 클릭, 자동 입력은 하지 않습니다.
- 이 프로그램이 하는 일은 분석, 정보 제공, 가이드, Overlay, 클립보드 복사, 알림까지입니다.

## 이번에 적용하면서 지킨 것
- 라그타임 실행 파일은 열지 않았고, 설정 JSON의 **항목 이름과 기본값 개념**만 참고했습니다.
- 라그타임이 쓰는 창고 읽기 방식(메모리 리더)은 따라가지 않았습니다. 이 프로그램의 창고 자료는 Replay 파싱 결과입니다.
- 알림음 파일과 화면 좌표는 복사하지 않았습니다. 소리는 사용자가 직접 지정하고, 없으면 기본 비프입니다.
