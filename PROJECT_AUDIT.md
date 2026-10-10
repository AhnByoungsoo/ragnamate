# 프로젝트 진단 및 LIVE 변경 기록

검증일: 2026-09-20. 첨부 ZIP의 실제 Python 소스와 로컬 Replay 15개를 읽어 작업했다.
원본 ZIP, 게임 파일, 외부 Replay는 수정하지 않았다. 패킷 테이블을 외부에서 가져와 적용하지 않았다.

## 발견한 문제

* `live/replay_link.py`는 녹화 중 파일 전체를 임시 복사하고 `normalize()`를 다시 실행했다. `live/engine.py`의 활성 Replay 주기는 10초였다.
* `rrf.Replay.packets()`는 마지막 payload의 실제 길이가 선언된 길이에 도달했는지 검사하지 않고 잘린 데이터를 yield했다. 짧은 헤더도 인덱스 오류가 가능했다.
* Replay 파일 identity, confirmed offset, pending buffer, packet offset 중복 제거, reorder queue가 없었다.
* Zeny 변경을 매번 과거부터 재생하면서 직전 분석의 마지막 잔액과 연결했다. 반복된 과거 잔액과 변화가 Screen 세션에 다시 들어갈 수 있었다.
* 캐릭터/몬스터 dictionary가 runtime ID에만 의존했다. 맵 이동 뒤 이전 몬스터 정보와 공격 이력이 남을 수 있었고, 중복 death도 처치로 집계될 수 있었다.
* Replay 처리와 OCR이 같은 작업 루프였다. UI는 worker가 변경하는 세션과 이벤트 목록을 직접 읽었다.
* 무기록 60초를 곧바로 종료로 취급했고, FINAL 재분석·LIVE 비교·복구 정보가 없었다.
* 임시 Replay 복사 파일명이 PID 기준이라 같은 프로세스의 동시 분석끼리 충돌할 수 있었다.

## 실제 구현 상태 분류

| 영역 | 원래 상태와 데이터 출처 | 이번 처리 |
|---|---|---|
| Replay / Storage parser | 실제 RRF v5 복호화·컨테이너·창고 목록 구현 | 유지, 공통 Framer와 길이 보호 추가 |
| 아이템 DB | 클라이언트 LUB 및 JSON cache | 유지 |
| 시세 / 자산 | SQLite 로컬 시세, CSV·수동 입력·기존 cache 이관 | 유지; 실시간 원격 노점 API는 없음 |
| Mock 시세 | 별도 `MockPriceProvider`, item ID 기반 가상 가격 | 보존; 실제 시세로 간주하지 않음 |
| Combat / Skill | 실제 이벤트 분석; 일부 패킷 후보는 설정으로 제외 | 유지, ID 재사용·중복 death 보호; LIVE 누적기 추가 |
| HP / SP | 검증된 var 5/6/7/8 절대값 | LIVE 상태·출처·오래됨 표시·reconciliation 추가 |
| AP | Replay var 매핑 없음, 화면 비율 감지는 구현 | Replay AP는 미지원 표시; reducer는 AP 절대/증감 이벤트 수용 준비 |
| Buff | START/END 구간 분석 구현 | refresh 단일 상태, 예상 만료와 확인 종료 구분; 기존 uptime 의미 유지 |
| EXP | 0x0acc/0x07f6 후보만 존재 | 자동 검증 승격 없음; 실험 옵션 시 △ |
| Loot / Profit | 획득 packet decoder 없음. 인벤토리 앞뒤 순증감과 가격 계산 | LIVE도 순증감 추정, 가격 누락·드랍 귀속 미확인 표시 |
| History / A-B / Export | 실제 SQLite 기록, 빌드 라벨, 비교, Excel/JSON/HTML export | 유지; 별도 Replay FINAL 기록과 재열기 추가 |
| Developer Packet Inspector | 실제 raw packet 목록·미지원 집계 | 유지; 별도 Live Diagnostics/Accuracy 추가 |
| RO Navigator / Dungeon / Job / Quest | JSON guide pack, 검색·조건·진행·공식 페이지 import 구현 | 유지; LIVE 위치 및 목표 맵 도착 표시 추가 |
| NPC /navi | 확인된 맵·좌표로 명령 생성, 클립보드 복사 | 유지; 자동 입력 없음 |
| 재료 / 창고 / 시세 연동 | 실제 보유량 합산과 부족량·로컬 시세 계산 | 유지 |
| World Map | 완성된 World Map UI 없음 | 신규 구현하지 않음 |
| Route Planner | guide 선행 순서와 Replay 좌표 기록만 있음 | 부분 구현으로 분류; 월드 최단경로 기능 없음 |
| Build Lab | History 빌드 이름/메모와 A/B 비교만 있음 | 부분 구현으로 분류; 장비 event decoder 없음 |
| Refine Simulator | 대응 기능 없음 | 신규 구현하지 않음 |
| Market History | 시세 DB/cache 기반 저장 구조 | 전용 장기 시계열 분석 화면은 미구현 |
| Price Alert / Asset Portfolio | 공통 알림 기반·창고 자산 합계는 존재 | 전용 가격 조건 알림/포트폴리오 성과 도구는 미구현 |
| Planner / Dungeon Timer / MVP Timer | 완성된 전용 기능 없음 | 부정확한 이벤트로 자동 등록하지 않음 |
| Overlay / Hotkey / Window follow | 실제 Qt/Windows UI 코드 | 보존; HUD는 Replay snapshot 값을 우선 표시 |
| Manual Buff Timer | Buff utility의 완성된 수동 타이머 기능은 확인되지 않음 | Replay 활성 상태만 연결, 없는 기능을 구현 완료로 표시하지 않음 |

## 수정한 구조

```
RRF 파일 → IncrementalReplayReader → PacketFramer → EventSequencer
        → 공통 PacketNormalizer → GameStateReducer → LiveSnapshot → UI/HUD

Offline Replay → 같은 PacketFramer → 같은 PacketNormalizer → 기존 분석기
기록 종료 추정 → Offline 전체 분석 → LIVE 비교 → FINAL 상태/검증 기록
```

상태를 가진 Decoder가 잘못된 순서로 actor/map을 바꾸지 않도록 **완전한 packet frame을 먼저 정렬**한다.
이벤트 타입은 기존 `ReplayEvent`를 공유한다. Inventory 목록은 기존 파서를 재사용하며 목록이 완성될 때만 전달한다.

### 읽기와 패킷

* append 데이터만 읽고, 작은 헤더와 최대 64-byte anchor를 재확인한다. 메타데이터는 세션 초기 한 번 읽는다.
* 파일 identity: device/inode + 날짜를 포함한 고정 헤더 hash. 축소/교체/확인된 tail anchor 변경 시 새 세션.
* confirmed offset, read offset, file size, pending bytes, session UUID, confirmed timestamp를 별도로 관리한다.
* RRF envelope의 length가 packet 경계 기준이다. 알 수 없는 opcode는 경계 추정에 사용하지 않는다.
* 부족한 tail은 INCOMPLETE로 보류한다. 명백한 손상은 인접한 검증 가능 frame 2개를 찾았을 때만 resync한다.
* 모르는 profile은 기본 profile로 조용히 fallback하지 않고 오류를 표시한다.
* offset 기반 중복 제거로 동일한 공격이 실제로 연속 발생한 경우는 보존한다.

### 순서, 상태와 통계

* 기본 reorder 250ms, 설정 가능. 테스트 Replay에서 뒤바뀐 timestamp가 없었으므로 보수적 시작값이다. 실제 녹화 지연을 측정해 최적화한 값은 아니다.
* window 밖의 late frame은 provisional/quality 진단에 남기고 LIVE 상태를 역행시키지 않는다. FINAL에서 재분석·차이 보고한다.
* Entity의 generation, 종류, class, 좌표, 생존/사망/사라짐을 관리한다. 맵 변경은 transient entity/target/cast 연결을 초기화한다.
* 미확인 pet/homunculus/summon은 UNKNOWN으로 남긴다. 숫자만 보고 종류를 추측하지 않는다.
* 절대 HP/SP/AP가 예측 증감과 다르면 절대값으로 보정하고 기록한다. 현재 decoder는 HP/SP만 생성한다.
* Buff refresh는 중복 Buff를 만들지 않는다. 패킷의 remain time으로 예상 만료를 표시하지만, Offline과 비교하는 기존 uptime은 명시적 END 또는 Replay 끝 기준으로 별도 보존한다.
* Skill은 cast 횟수와 hit packet 횟수를 구분한다. `div` 합은 관측 hit 구성으로 별도 보존한다. 기존 50ms 사용 묶음은 호환성을 위해 유지하며, 모든 cast-hit 연결이 확정된 것으로 주장하지 않는다.
* 세션 DPS, 최근 1/5/10초 DPS, 기존 정의의 Combat DPS를 제공한다. Combat 시간은 기존 `min(gap, 간격)` 합, 최소 1초 정의를 유지한다. 마지막 타격 뒤 timeout tail을 더하는 새로운 정의로 기존 보고서를 바꾸지 않았다.
* 통계는 ReplayClock만 사용한다. 파일 감시/유휴/화면 신선도는 monotonic clock을 사용한다.
* recent event/resource는 500개, entity는 8,192개, 최근 피해는 10초 밀리초 bucket으로 제한한다. 초과/유실은 진단에 표시한다.

### 스레드와 FINAL

* Replay worker는 게임 창/OCR이 없어도 녹화를 추적한다. Screen worker와 SQLite writer를 분리했다.
* UI에는 frozen `LiveSnapshot`의 JSON payload를 복사해 전달한다. session ID/revision이 오래된 동일 세션 결과를 차단한다.
* 100ms read, 200ms publish가 기본이다. 전체 packet 처리마다 repaint하지 않는다.
* STARTING / ACTIVE / STALLED / FINALIZING / FINISHED를 구현했다. Map transition은 map event로 transient 상태를 원자적으로 바꾸며 별도 지속 상태 라벨은 두지 않았다.
* 5초 정체 표시, 60초 종료 **추정**. RRF에 검증된 recording-close marker가 없으므로 기록 중단 확정이라고 주장하지 않는다.
* FINAL은 원본의 stat이 읽기 전후 동일하고 framing이 완전할 때만 받아들인다. 기록 재개 시 기존 FINAL은 superseded 처리하고 LIVE를 이어간다.
* `data/live_replay.db`에 batch transaction으로 checkpoint와 FINAL 비교/결과를 저장한다. 원래 DB schema를 파괴적으로 변경하지 않는다.
* 재시작은 identity가 맞으면 session ID를 복구하고 prefix부터 상태를 재구성한다. offset만 건너뛰어 통계가 빠지는 복구는 하지 않는다. 재시작 때 한 번 O(file size) 재생 비용이 있다.
* FINAL 기록을 더블클릭하면 기존 전체 Replay 분석으로 열어 History/A-B/Export를 계속 사용할 수 있다.

## 검증

* 수정 전: 66 tests, 65 통과 / OCR 1 skip.
* 최종: 86 tests, 85 통과 / OCR 1 skip, 실패·오류 0. Qt UI 테스트 포함.
* 실제 파일 15개 × 100 seed: 1,500회 모두 Offline와 일치. 이후 최종 수정본으로 실제 15개 다시 확인, 모두 일치.
* 기본 설정은 미검증 스킬 피해를 제외하므로 실제 파일의 기본 Damage/Kill/EXP 비교에는 0=0이 포함된다. 이 결과를 실제 전투 의미 검증으로 과장하지 않는다.
* 추가 실제 전투 샘플 `인퀴 천라.rrf`, 실험적 해석 100회: Damage 2,634,642 / Kill 84 / skill 2327 Cast 58, Hit packet 142, Use 58 일치.
* 실제 EXP packet을 가진 fixture는 확인하지 못했다. 합성 데이터 Base EXP 1,234 / Job EXP 456으로 별도 검증했다.
* 1-byte 분할, 17+11-byte 분할, 중복/겹침 공급, 새 inode 교체, truncate, 같은 크기 덮어쓰기, 열린 container/header patch, 맵 이동·ID 재사용, Buff refresh/expire/reconcile, multi-hit, FINAL 후 append, journal 복구, 100,000-event 세션 테스트가 있다.
* GUI: 기존 MainWindow 실행, 실제 Replay 로드, LIVE와 Diagnostics 렌더링, setting 저장, stale revision 거부 테스트 통과. 실제 게임 실행 중의 화면 캡처/녹화 latency 및 한국어 OCR은 이번 환경에서 검증하지 못했다.

## 성능 측정

`43ww.rrf` 292,196 bytes를 1,024-byte 단위로 286번 공급한 로컬 실험:

| 항목 | 전체 재분석 비교 경로 | 증분 경로 |
|---|---:|---:|
| 읽기량 | 32,061,796 bytes | payload 292,196 + header/anchor 116,448 bytes |
| 디코딩 순회 packet 수 | 107,024 | 1,955 |
| 실행 시간 | 3.967초 | 1.172초 |

읽기량 98.73% 감소, 측정 실행 시간 약 3.38배 개선. 과거 임시 복사와 Combat/Skill 전체 재계산 비용은 비교 경로에서 제외한 보수적 측정이다. 디스크 상태/환경에 따라 달라지며, 100MB Replay나 게임 실시간 latency의 측정값이 아니다.

## 남아 있는 packet/정확도 한계

* 기본 설정의 빈도가 높은 미해석 ID: 0x0121(1,511), 0x01d0(419), 0x01d7(384), 0x0141(368), 0x013a(194). 숫자는 실제 15개 파일 한 번씩의 합계다. 의미를 추측하여 통계에 넣지 않았다.
* 0x01de(144)는 실제 전투 샘플에서 길이와 actor/target 연결을 확인했으나 damage/div 의미 전체를 외부 ground truth와 교차 검증한 것은 아니다. 기존의 실험 옵션과 △를 유지했다.
* 0x0acc/0x07f6 EXP, 0x07fa item remove는 후보 정의다. AP, ITEM_GAIN, 장비 변경, cast cancel/fail/post-delay 전용 packet 매핑은 미지원.
* Kill은 '자신이 피해를 준, 사망 이벤트가 확인된 몬스터'이며 게임의 단독 처치권/드랍 소유권 확정은 아니다.
* Loot는 inventory net change 추정이다. 구매/창고 이동/판매/소모를 모두 정확하게 구분할 수 없고, 몬스터별 drop attribution은 UNKNOWN이다.
* recorder가 기존 packet이 있는 임의의 중간 영역을 몰래 수정하는 모든 경우까지 탐지하는 full-file hash 감시는 하지 않는다. 일반 replace/truncate/append와 고정 header/최근 확인 영역 변경을 감지한다.
* corruption은 보수적으로 처리한다. resync가 필요한 손상 파일을 완전한 FINAL로 확정하지 않는다. metadata가 packet stream 뒤에 있는 변형 RRF 및 64MiB를 넘는 metadata는 LIVE에서 명시적으로 거부한다.
* 이것은 통합 도우미 전체 기능 완성판이 아니다. World Map, Planner, Refine Simulator, 완성된 Build Lab 등의 부재는 위 표에 별도로 남겼다.

## 다음 개선 (우선순위)

1. 게임 녹화와 화면 수치를 함께 확인할 수 있는 전투/EXP/AP fixture로 packet 의미를 교차 검증하고 검증된 profile만 승격.
2. ITEM_GAIN·inventory reason·장비 변경 packet을 검증하여 순증감 추정을 실제 획득/소모/Build 이벤트로 발전.
3. 실제 장시간 녹화에서 file/header 기록 방식과 지연을 측정하여 reorder/종료 감지와 Overlay 표시를 조정.
