# 라그메이트 (RagnaMate) — 라그나로크 플레이 도우미

> 2026-09-20 LIVE 개선판: `run_replay.bat`으로 실행합니다. 최신 실행 방법은
> [RUNNING_KO.md](RUNNING_KO.md), 실제 구현 범위·검증·제한 사항은
> [PROJECT_AUDIT.md](PROJECT_AUDIT.md)를 참고하세요. 아래 기존 설명 중
> Replay 전체 재분석/10초 갱신 방식은 증분 엔진으로 대체되었습니다.

Replay(.rrf) 파일 하나를 읽어 **창고 자산**과 **사냥 기록**(전투, 스킬, 버프, EXP, 드랍/수익)을 분석합니다.
게임 프로세스에는 접근하지 않고 Replay 파일만 읽습니다.

## 실행
- `라그메이트_실행.bat` 더블클릭 (특정 파일은 `.rrf`를 bat 위에 끌어다 놓기)
- `python main.py` (GUI) / `python main.py --export` (창고 엑셀만)
- 테스트: `cd tests && python -m unittest`

### 필요한 패키지
- `requirements.txt` — 전부(분석 + 화면 인식). `라그메이트_실행.bat`이 이걸 씁니다.
- `requirements-replay.txt` — 분석·창고 시세만. `run_replay.bat`이 만드는 `.venv`의 기본값입니다.
- `requirements-live.txt` — Passive Live 전용(화면 캡처 `windows-capture`, Windows OCR `winrt-*`). **이게 빠지면
  `Windows OCR 초기화 실패: No module named 'winrt'`가 나고 화면 인식만 꺼집니다.** Live 탭 위쪽에 이유와 설치 명령이 함께 표시됩니다.
  `.venv`에 직접 넣으려면: `.venv\Scripts\python.exe -m pip install -r requirements-live.txt`

탭: 홈 · LIVE · 네비게이션 · 리플레이 분석 · 빌드 비교 · 기록 · 창고 · 시세 · (개발자 모드) 패킷 · Guide

상단 바는 세 갈래입니다. **[최신 Replay 불러오기 ▾]**(파일 선택·새로고침·폴더 변경) · **[시세 새로고침 ▾]**(선택만·CSV 가져오기·엑셀 내보내기) ·
**[오버레이 ▾]**(Live HUD · 버프 타이머 · 창고 시세 · Navigator, 각 단축키 표시). 같은 오버레이 메뉴를 Live 탭과 트레이에서도 그대로 씁니다.

## 데이터 신뢰도
● 패킷에서 직접 측정 · ○ 측정값으로 계산 · △ 추정 또는 미검증 패킷

## 현재 해석되는 패킷 (kRO 2026-09 클라이언트, 샘플 Replay로 교차검증)
| Event | 패킷 | 검증 근거 |
|---|---|---|
| MAP_CHANGE | 0x0091, 0x0ac7 | 맵명 .gat, 도착좌표 = 이후 이동 시작좌표 |
| PLAYER_MOVE | 0x0087, 0x0088 | 좌표 일치 |
| PLAYER/MONSTER/NPC_SPAWN, MONSTER_MOVE | 0x09ff, 0x09fd (0x09fe는 이름만) | 이름·레벨·좌표, 유닛 타입(0 PC, 5 몬스터 '포링', 6 NPC, 7 펫, 10 정령) |
| MONSTER_DEATH / UNIT_VANISH | 0x0080 | 구조 검증(type 0/2/3 관측, 1=사망은 문서 기준) |
| DAMAGE / ACTION | 0x08c8 | 길이·필드 검증(샘플은 '앉기' 동작) |
| SKILL_CAST_START / SKILL_USE | 0x07fb / 0x09cb | 시전자·스킬 ID 짝 일치, 지면 좌표 |
| BUFF_START / BUFF_END | 0x0983, 0x043f, 0x0196, 0x0984 | 같은 EFST가 자신/시야 패킷에 일치 |
| HP/SP/무게/Zeny | 0x00b0 (5,6,7,8,24), 0x00b1 (20) | HP 6초 회복, SP 스킬 소모, Zeny = 메타데이터 |
| INVENTORY/CART/STORAGE_UPDATE | 0x0b08/0b09/0b39/0b0b | 기존 창고 파서 재사용 |

캐릭터명, 직업, 레벨, 시작 Zeny는 Replay 메타데이터(Session/ReplayData 컨테이너)에서 읽습니다.

미검증 후보(설정 > 실험적 패킷 해석): 0x01de 스킬 피해, 0x0acc/0x07f6 EXP, 0x07fa 아이템 소모.
해석하지 못한 패킷은 개발자 탭과 `logs/unknown_packets.log`에 횟수와 함께 남습니다.

## 구조
```
rrf.py, iteminfo.py          기존 파서 (inventory_lists에 packets/with_time 옵션, meta_chunks 추가)
analyzer/  packets.py(레지스트리·버전) events.py normalizer.py session.py gamedata.py report.py
           analyzers/ combat skill buff vitals monster exp_zeny loot(드랍·소모품·수익) timeline
core/      기존 창고 분석 (models, items, pricing, analysis)
services/  replay_analysis_service.py report_export.py + 기존 replay/market/export 서비스
database/  database.py migrations.py(schema_version) history.py
ui/        main_window.py(창고 탭 포함) widgets.py pages/(dashboard, replay, compare, history, dev)
tests/     test_regression.py test_analyzers.py test_storage_features.py
```

## Passive Live Mode (Replay 없이 기본 동작) + Replay Enhanced
프로그램을 켜 두면 Ragnarok 창을 자동으로 감지하고, 화면을 분석해 HUD를 띄웁니다.
Replay 녹화가 감지되면 HYBRID로 올라가 Kill·DPS 등을 보강합니다.

- 게임 클라이언트에는 접근하지 않습니다. 사용하는 것은 창 탐지(Windows API), 해당 창만 캡처(Windows Graphics Capture), Windows 내장 한국어 OCR, Replay 파일 읽기뿐입니다. 메모리·DLL·패킷 인젝션, 자동 입력은 없습니다.
- 캡처 프레임은 디스크에 저장하지 않습니다.
- 상태 흐름: `WAITING` → 게임 감지 `LIVE ● SCREEN` → Replay 녹화 감지 `LIVE ● HYBRID` → 녹화 중단 `SCREEN` → 게임 종료 시 세션 저장 후 `WAITING`.
- **처음 한 번 해야 할 일**: 게임에 접속한 상태에서 Live 탭 > [영역 설정]으로 HP/SP/AP 바, 채팅창, EXP % 영역을 드래그합니다. 해상도별로 저장되며, 다른 해상도에는 환산값을 '확인 필요'로 제안합니다.
  - [막대 자동 추천]은 화면에서 게이지 막대처럼 보이는 곳을 찾아 HP/SP/AP 후보로 넣어 줍니다. 추천값은 **지금 채워져 있는 부분**이라, 좌우로 끌어 막대 전체(빈 부분 포함)를 덮도록 고친 뒤 저장해야 % 계산이 맞습니다. 확인 전에는 저장되지 않습니다.
- **채팅 메시지 패턴**: `data/chat_patterns.json`. 실제 문구를 확인하기 전에는 비어 있습니다. 개발자 모드 Live > Screen Inspector에서 실제 획득 메시지 줄을 골라 [채팅 줄로 패턴 학습]으로 등록합니다. OCR 오탈자는 고정 문구의 80% 이상이 일치하면 허용합니다.
- 아이템 이름 OCR 결과는 정확 일치 → OCR 별칭(`data/item_ocr_alias.json`) → 유사 일치 순서로 대조합니다. 유사 일치는 '검토 필요'로 분류되어 수익에서 빠지고, 더블클릭으로 교정하면 별칭으로 학습합니다.
- 화면만으로는 Kill/DPS/Skill을 알 수 없어 표시하지 않습니다. Replay가 붙을 때만 ●로 표시합니다.
- 단축키: Ctrl+Alt+H (HUD), Ctrl+Alt+C (Compact), Ctrl+Alt+N (Navigator 오버레이), Ctrl+Alt+B (버프 타이머 오버레이),
  Ctrl+Alt+S (창고 시세 오버레이), Ctrl+Alt+P (버프 타이머 전체 일시정지), Ctrl+Alt+1~4 (버프 타이머 1~4번 시작/갱신).
  다른 프로그램이 이미 쓰는 키는 등록되지 않으며, 시작 직후 상태줄에 어떤 키가 실패했는지 알려 줍니다. 바꾸려면 `config/settings.json`의 `hotkeys`.

## LIVE 상태 모니터 (효과 · 재사용 알림)
LIVE 화면 위쪽 '현재 효과' 줄에 지금 걸려 있는 효과를 아이콘 + 남은 시간으로 보여 줍니다.

- 효과 하나의 수명: **적용 중 → 종료 임박(60초) → 곧 종료(10초) → 재사용!!(종료 후 60초) → 사라짐**.
  '재사용!!'은 *효과가 끝났다*는 알림이며, 게임의 스킬 재사용 대기시간이 끝났다는 뜻이 아닙니다(섞지 않습니다).
- 기준 시간은 설정의 `status_warn_sec`(60) · `status_critical_sec`(10) · `status_reuse_sec`(60)입니다.
- 아이템 효과와 스킬 버프, 직접 만든 수동 타이머가 **같은 컴포넌트**로 표시됩니다.
- [★ 고정]한 효과는 항상 위에 옵니다. 우편 개수 같은 게임 내부 상태는 기본 화면에서 감춥니다(`status_show_internal`).
- 이름은 확인된 것(●) > 영문 이름을 풀어 쓴 추정(◐) > `알 수 없는 효과`(○) 순서로 보여 주고,
  내부 Symbol(`FOOD_STR_CASH` 등)은 Tooltip과 개발자 모드에서만 나옵니다.

## 버프 타이머 · HUD · 창고 시세 Overlay
게임 옆에 띄우는 작은 창 세 가지입니다. 모두 표시 전용이고, 게임에 입력을 보내지 않습니다.
공통으로 오른쪽 클릭 메뉴에서 투명도 · 위치 고정 · 숨기기를 바꾸고, 위치는 `settings.json`에 저장됩니다.

**버프 타이머** (Live 탭 > 버프 타이머, Ctrl+Alt+B)
- 이름 · 지속시간 · 미리 알림(초) · 알림음(.wav)을 등록해 두고 버프를 걸 때 [시작/갱신]을 누릅니다(더블클릭도 같음).
- 목록 위 4개는 게임 화면을 보면서 **Ctrl+Alt+1~4**로 바로 걸 수 있습니다. 시작하면 HUD 아래줄에 `⏱ 이름 시작 (남은시간)`이 잠깐 뜹니다.
  번호는 목록 순서이고 [▲][▼]로 바꿉니다. 이 단축키는 이 프로그램의 타이머만 조작하며 게임에는 아무 입력도 보내지 않습니다.
- 남은 시간이 등록한 '미리 알림' 초에 닿으면 한 번, 종료되면 한 번 알립니다(소리 + 트레이 + HUD 줄).
- 남은 비율에 따라 초록/노랑/빨강으로 표시하고, 기준은 설정의 `buff_warn_pct` / `buff_danger_pct`입니다.
- [전체 일시정지]는 남은 시간을 그대로 붙잡아 둡니다(자리 비울 때).
- **화면에서 버프 아이콘을 읽는 기능은 없습니다.** ○ 줄은 사용자가 누른 시각 기준 계산값이고,
  ● 줄은 Replay 패킷(BUFF_START의 remain time)에서 확인된 값입니다. remain time이 없으면 숫자 대신 `?`입니다.
- **버프 이름**: 클라이언트에서 이름을 읽을 수 없어(`data.grf` 암호화) 이름을 지어내지 않습니다. 확인 전에는 `버프 #993`처럼 EFST 번호로 둡니다.
  - **이름 후보 학습**: 리플레이를 분석할 때마다 *내가 쓴 스킬 직후에 생긴 효과*를 짝지어 `data/buff_origins.json`에 쌓습니다.
    버프 타이머 목록의 비고에 `후보: ○○ 사용 직후 (N회 관측)`으로 뜨고, [이름 지정]을 누르면 그 후보가 기본값으로 채워집니다.
    **자동으로 확정하지 않습니다** — 누를 때만 이름이 됩니다. 접속 전부터 걸려 있던 효과, 남이 걸어 준 버프, 음식류는 이 방식으로 잡히지 않습니다.
  - 이름을 모르는 효과가 여러 개면 `알 수 없는 효과 1`, `알 수 없는 효과 2`처럼 번호를 붙여 구분합니다(같은 효과는 항상 같은 번호).
  - 개발자 탭 [버프 이름 자동 수집(rAthena)]을 누르면 rAthena 공개 저장소의 EFST 목록에서 이름을 채웁니다(약 1,470개).
    클라이언트에서 읽은 값이 아니라 **외부 목록**이므로 `외부 목록(rAthena) · 확인 필요`로 표시하고, 영어 상수명(`FOOD_STR_CASH`)으로 나옵니다.
  - 게임에서 버프를 걸면 그 줄의 상태가 **「새로 걸림」**(파란색)으로 바뀝니다. 그 줄을 더블클릭해 한국어 이름을 적으면
    `data/buff_names.json`에 '직접 확인'으로 저장되고, 이후 자동 수집이 그 이름을 덮어쓰지 않습니다.
  - 파일로 한꺼번에 넣으려면 개발자 탭의 [버프 이름 가져오기](rAthena `*.yml` 또는 `{"ID": "이름"}` JSON).
- **표시 방식**(오버레이 오른쪽 클릭 또는 Live 탭 > 버프 타이머 > [표시 설정]):
  - 목록(막대) · 간단(이름+시간) · **아이콘**(작은 칸에 남은 시간). 아이콘 칸 크기는 20~40px.
  - 아이콘 이미지는 [아이콘 지정]으로 직접 넣습니다(`data/buff_icons.json`). 지정 전에는 이름 앞 3글자 또는 EFST 번호가 들어갑니다. 게임 파일에서 아이콘을 꺼내오지 않습니다.
  - 기본으로 **만료된 것**과 **24시간 넘는 상시 버프**를 감추고 최대 12개만 보여줍니다. 둘 다 메뉴에서 끌 수 있고, 개수는 6/8/12/20/전부 중에 고릅니다. Live 탭의 목록에는 감춘 것까지 전부 나옵니다.

**HUD** (Ctrl+Alt+H)
- 오른쪽 클릭 메뉴: 프리셋(compact/hunt/status/detailed), 표시 항목 켜고 끄기, 가로/세로 배치, 투명도, 크기, 위치 고정, 클릭 통과.
- 표시 항목: Time(세션 시간) · EXP/h · Profit/h · Kill/h · DPS · Session · Top Drops · HP/SP/AP/WT · 버프 타이머.
- HP/SP/AP는 `hud_warn_pct`(노랑) / `hud_danger_pct`(빨강), 무게는 `alert_weight` 이상이면 빨강입니다.
- 값의 출처는 그대로 ○ 화면 / ● Replay로 표시하고, 모르는 값은 줄 자체를 만들지 않습니다.

**창고 시세 Overlay** (상단 [시세 오버레이], Ctrl+Alt+S)
- 지금 불러온 Replay의 창고·인벤토리·카트를 아이템별로 합쳐 예상금액 순으로 보여줍니다. 줄을 더블클릭하면 창고 분석 탭에서 그 아이템을 찾아 줍니다.
- 검색, '팔 만한 것만'(합친 금액이 `sell_min_value` 이상), [시세 새로고침]이 있고,
  오른쪽 클릭 > 표시할 가격에서 판매 최저가 / 거래 평균가 열을 켜고 끌 수 있습니다.
- 가격은 기존 로컬 시세 DB 값이며, 시세가 없는 아이템은 0원이 아니라 `-`로 둡니다.

## RO Navigator
검색 → 입장 조건 → 내 준비도(인벤토리+카트+창고 보유량) → 부족 재료 시세 → STEP 진행 → `/navi` 클립보드 복사.
게임에 입력은 보내지 않습니다. 복사한 명령을 채팅창에 Ctrl+V → Enter 하면 됩니다.

- Guide 데이터는 코드가 아니라 JSON Guide Pack에 있습니다.
  - `data/guides/official_job_changes.json`: 공식 룬미드가츠 도서관의 전직 안내 원문
  - `data/guides/seed_curated.json`: 직접 조사한 Guide
  - `data/guides/packs/`: 공유 Guide Pack (예: ro_guides_kr.json)
  - `data/guides/user/`: Guide Editor로 저장한 Guide
- 직업 트리는 `data/guides/job_tree.json`에 있습니다. 새 직업은 데이터만 추가하면 UI에 표시됩니다.
- 퀘스트 완료 상태는 ✅ 완료 / ❌ 미완료 / ? 확인 불가 세 가지입니다. Replay로 확인할 수 없는 것은 '확인 필요'로 표시하고, 실패로 치지 않습니다.
- 개발자 모드의 "개발자: Guide" 탭: Guide 검사(좌표·맵·Item ID·순환·출처·오래된 정보), Import/Export, 별칭 CSV, 검색 실패 로그,
  [공식 전직 가이드 갱신], [공식 퀘스트 전체 수집].
- **공식 퀘스트 수집**: 룬미드가츠 도서관의 퀘스트 목록/상세를 0.7초 간격으로 모두 받아 `data/guides/official_quests.json`을 만듭니다.
  받은 원본은 `official_quests_cache.json`에 남아 중간에 끊겨도 이어받습니다. 페이지에 있는 항목(지역·시작NPC·시작레벨·시작아이템·직업·선행퀘스트·보상)만
  옮기며, **그 페이지에는 맵 ID와 좌표가 없어 `/navi` 명령을 만들지 않습니다**(없는 좌표를 지어내지 않음). 좌표는 게임에서 확인해 Guide Editor로 채웁니다.
- 미니 오버레이: 항상 위, 투명도, Click-through, 위치 저장 (`ui/overlay.py`). 라그타임 참고 정리는 `REFERENCE_FEATURES.md`에 있습니다.

## 이름 데이터
- 아이템명과 맵명은 클라이언트(`itemInfo_true.lub`, `mapInfo_true.lub`)에서 읽습니다.
- 몬스터명은 Replay 유닛 패킷에서 모아 DB에 저장합니다.
- 스킬명과 버프명은 `data/skill_names.json`, `data/buff_names.json`에서 읽습니다. `data.grf`가 암호화 포맷이라 클라이언트에서 직접 읽지 않습니다. 파일이 없으면 `스킬 #ID`로 표시합니다.
  개발자 모드 `개발자: 패킷` 탭의 [스킬 이름 가져오기] / [버프 이름 가져오기]로 rAthena `*.yml` 또는 `{"ID": "이름"}` JSON에서 채울 수 있습니다(이미 있는 이름은 덮어쓰지 않습니다).
