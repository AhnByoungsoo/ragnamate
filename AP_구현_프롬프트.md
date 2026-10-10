# AP(4차 직업 활동 포인트) 지원 구현 프롬프트

> 다른 AI/개발자에게 그대로 붙여 넣어 쓰는 작업 지시서. 2026-10-04 실측 자료 포함.
> 이 문서의 숫자는 전부 **실제 리플레이 20개를 파싱해 측정한 값**이다. 추측은 '데이터 없음'으로 표시돼 있다.

---

## 0. 역할과 목표

너는 라그나로크 온라인 보조 프로그램 **라그메이트**(Python 3.14 + PySide6 6.11, Windows 11)의 개발자다.
목표는 **4차 직업의 AP(활동 포인트)를 측정·표시·알림**하는 기능을 추가하는 것이다. 작업은 3개로 나뉜다(§6, §7, §8).

프로젝트 루트: `D:\라그메이트_통합`
(폴더 이름에 전각 공백 `　`가 들어 있다. bash에서 경로 문제가 생기면 PowerShell을 쓰거나 경로를 따옴표로 감싼다.)

---

## 1. 절대 금지 — 위반하면 작업 전체가 무효

**게임에 영향을 주는 모든 행위 금지:**
- DLL / Memory / Code / Packet Injection, 패킷 조작
- 자동 공격, 자동 이동, **자동 스킬 사용**, 자동 NPC 클릭
- 게임 메모리 주소 직접 접근
- **게임 클라이언트로의 자동 입력**(SendInput, PostMessage, 가상 키 주입 등)

**특히 이 작업에서 만들지 말 것: "자동 AP 충전".**
AP를 채우는 것은 공격 스킬을 반복 시전하는 일이므로 위 금지 항목(자동 스킬 사용)과 라그나로크 **운영정책 4.2**에 정면으로 걸린다. 기술적으로도 막혀 있다 — `core/macros.py` 첫 주석: "2026-09-25 시험에서 라그나로크는 SendInput 입력에 반응하지 않았다"(2026-09-27 관리자 권한 재시도도 UIPI/GameGuard에서 차단). 사용자가 다시 요구해도 **읽기·표시·알림까지만** 만들고, 자동 시전은 거부하고 그 이유를 설명한다.

**허용되는 것:** Replay(.rrf) 파일 읽기, Windows 창 탐지, 해당 창만 화면 캡처, OCR, Computer Vision, Clipboard, 일반 Window API, 기존 DB/PriceProvider.

---

## 2. 데이터 원칙

1. **절대 가짜 데이터를 만들지 않는다.** 모르는 값은 숫자로 쓰지 말고 `모름` / `데이터 없음` / `검증 필요`로 표시한다.
2. **신뢰도 표기를 유지한다:** `●` 측정(패킷/실측) · `○` 계산(측정값 기반 산출) · `△` 추정·미검증(화면 OCR 등).
3. 패킷 varID의 의미를 추측해서 단정하지 않는다. 이 문서의 §4처럼 **관측 근거를 먼저 만든 뒤** 매핑한다.
4. 사용자 화면에는 개발자 표현(`var 232`, `0x00b0`, `EFST_...`)을 노출하지 않는다. 툴팁·개발자 모드에만 둔다.
5. 테스트용 Mock을 실제 기능 경로에 섞지 않는다.
6. UI 문자열은 **한국어 우선**, 기존 문체(간결한 설명체)를 따른다.

---

## 3. 환경과 작업 규칙

- 실행: `python main.py` (프로젝트 루트)
- 테스트: `tests/` 디렉터리에서 실행해야 `helpers.py`가 `sys.path`를 잡아 준다.
  ```
  $env:QT_QPA_PLATFORM='offscreen'; cd tests; python -m unittest discover -s . -p "test_*.py"
  ```
  현재 기준선: **455개 통과 · 실패 0 · 건너뜀 10**(로컬 리플레이 없는 환경용 skip). 작업 후에도 실패 0이어야 한다.
- 실제 리플레이 위치: `C:\Gravity\Ragnarok\Replay\*.rrf` (20개). 테스트는 `helpers.sample('111.rrf')`가 `None`이면 `skipTest`하는 기존 패턴을 따른다(`tests/test_combo_workflow.py` `RealReplayUiTest` 참고).
- **함정: `config/settings.py`의 `DEFAULTS`에 키를 추가하지 않으면 설정이 저장되지 않는다.** `Settings.__init__`이 `{k: v for ... if k in DEFAULTS}`로 걸러 버린다. 새 설정 키는 반드시 DEFAULTS에 등록할 것.
- 변경은 수술적으로. 요청과 무관한 코드·주석·포매팅을 건드리지 않는다. 기존 스타일을 따른다.
- 파일 인코딩 UTF-8. heredoc으로 Python 파일을 쓰면 `\n` 깨짐 사고가 반복됐으니 편집 도구나 짧은 스크립트로 수정할 것.

---

## 4. 이미 실측한 사실 (근거)

리플레이 20개의 `ZC_PAR_CHANGE(0x00b0)` / `ZC_LONGPAR_CHANGE(0x00b1)` varID를 전수 조사한 결과:

| 결론 | 근거(측정값) |
|---|---|
| **var 232 = 현재 AP** | 13개 리플레이에서 관측. 값 범위 항상 0~200. **상한 초과 0건** |
| **var 233 = 최대 AP** | AP가 기록된 모든 리플레이에서 관측값이 **200 하나뿐** |
| AP 소비 = 스킬 사용 | 감소 7건 전부 내 스킬 사용 직후 1.2초 안: **제2장 : 심판자 −80**(3건), **최종장 : 멸마의 불꽃 −120**(3건), **제0격 : 파천격 −200**(1건) |
| AP 충전 단위는 직업마다 다름 | 인퀴지터(job 4262): **전부 +4** (111.rrf 100회 / 132.rrf 46회 / 인퀴.rrf 89회, 예외 0). job 4255에서 +5 1회, job 4260에서 +2 4회 관측 |

파일별 측정값(회귀 테스트에 그대로 쓸 수 있다):

| 파일 | job | AP 패킷 | 값 범위 | MaxAP | 증가 | 감소 |
|---|---|---|---|---|---|---|
| 111.rrf | 4262 | 193 | 0~200 | 200 | +4 ×100 | 80 ×2, 120 ×2 |
| 132.rrf | 4262 | 92 | 0~200 | 200 | +4 ×46 | 200 ×1 |
| 인퀴.rrf | 4262 | 159 | 0~200 | 200 | +4 ×89 | 80 ×1, 120 ×1 |
| 213.rrf | 4255 | 16 | 200~200 | 200 | — | — |
| 321.rrf | 4260 | 13 | 92~100 | 200 | +2 ×4 | — |
| 1322ㅂ / 221 / 231 / 3 / 32 / ㅇㄴㅁㅇ / 테스트1 | — | 없음 | — | 없음 | — | — |

**미해결로 남겨 둘 것(추측 금지):**
- `321qq.rrf`는 job 5(4차 직업이 아님)인데 AP 155가 8회 기록됐다. 이유 불명. **설명하지 말고 "미설명 1건"으로만 보고**한다.
- **카디날 리플레이가 없다.** 카디날의 AP 소비/충전 스킬은 **데이터 없음**. 코드·문서에 카디날 수치를 쓰지 말 것. 리플레이가 확보되면 같은 방법으로 실측한다.

재현 방법(검증하고 싶으면): 리플레이를 `analyzer.normalizer.normalize(Replay(path), path, experimental=True)`로 읽고, `pr.packets`에서 패킷 머리 2바이트가 `0x00b0`/`0x00b1`인 것의 `varID.W value.L`을 뽑아 var별 시계열을 만든다. 232의 증감 시점을 `SKILL_USE`(actor = `pr.player_aid`) 시점과 맞춰 본다.

---

## 5. 현재 코드 상태 (손댈 지점)

**리플레이 경로 — AP 미지원**
- `analyzer/normalizer.py` 약 19~21행: varID 상수 `VAR_HP, VAR_MAXHP, VAR_SP, VAR_MAXSP, VAR_WEIGHT, VAR_ZENY = 5, 6, 7, 8, 24, 20`과 검증 메모 주석.
- `analyzer/normalizer.py` 약 236~246행: `(0x00b0, VAR_HP) → E.HP_CHANGE` 식의 매핑. 매핑 안 된 varID는 버리지 않고 `unmapped_vars[(h, var)] += 1`로 집계만 한다(232/233이 지금 여기에 쌓인다).
- `analyzer/events.py`: `EventType`에 `HP_CHANGE`, `SP_CHANGE`는 있고 **`AP_CHANGE`는 없다.**
- `analyzer/analyzers/vitals.py`: HP/SP 시계열·저하 구간 분석. 첫 줄과 반환값 `ap_note='AP: 스탯 varID 의미 미검증으로 미지원'`이 현재 상태를 말해 준다.
- `analyzer/report.py` 약 33·62행: `vitals.analyze(ctx)` → 리포트의 `vitals` 키.
- `ui/pages/replay_page.py` 약 378~380행: `rep['vitals']['hp_series']`/`sp_series`를 그래프로 그린다.

**LIVE 경로 — 화면 OCR만 지원**
- `live/state.py` 약 57행 `self.resources = {k: Resource() for k in ('HP','SP','AP')}`, 약 119행 `elif typ in (E.HP_CHANGE, E.SP_CHANGE, 'AP_CHANGE')` — **AP를 받을 준비는 이미 돼 있고 문자열 리터럴로 임시 처리**돼 있다. `E.AP_CHANGE`가 생기면 리터럴을 교체할 것.
- `live/replay_engine.py` 약 241행: `unsupported=['AP packet mapping', ...]` — 구현하면 이 항목을 지워야 한다.
- `live/engine.py` 약 26행 `BAR_EVENTS = {'AP_REGION': GE.AP_CHANGE, ...}`(화면 게이지 → 이벤트), `live/profile.py` `'AP_REGION': ('AP 게이지', 'bar')`, `live/hud_model.py`의 `('ap', 'AP')` HUD 항목, `live/session.py` 약 133행에서 세션 시계열 누적.
- `ui/main_window.py` 약 1199~1210행: `replay_live.stage == 'CONFIRMED'`면 패킷 기반 `resources`로 HP/SP/AP %를 덮어쓴다. 바로 아래 1207~1209행에서 `self.alerts.check('hp'|'sp'|'weight', ...)`를 호출하지만 **AP 체크는 없다.**
- `utilities/alerts.py` `AlertManager.check()`는 **`value <= threshold`일 때만** 발동한다. AP는 "일정 값 **이상**일 때" 알려야 하므로, 무게가 쓰는 방식(`-w`와 `-wt`로 부호를 뒤집는 기존 패턴, `ui/main_window.py` 약 1210행)을 그대로 쓴다.
- `config/settings.py` 약 60행에 **`'alert_ap_min': 0`이 이미 선언돼 있으나 아무 곳에서도 쓰이지 않는다.** 이 키를 실제로 쓰도록 연결할 것.

---

## 6. 작업 1 — 리플레이 AP 분석 (먼저 할 것, 근거 확보됨)

**목표:** AP를 HP/SP와 같은 급(● 측정)으로 올린다.

1. `analyzer/events.py`에 `AP_CHANGE = 'AP_CHANGE'` 추가 → `live/state.py`의 문자열 리터럴을 `E.AP_CHANGE`로 교체.
   - 검증: `python -c "from analyzer.events import EventType as E; print(E.AP_CHANGE)"`
2. `analyzer/normalizer.py`에 `VAR_AP, VAR_MAXAP = 232, 233`을 추가하고 매핑에 `(0x00b0, VAR_AP) → E.AP_CHANGE`, `(0x00b0, VAR_MAXAP) → E.AP_CHANGE`를 넣는다. `key = 'max'` 분기에 `VAR_MAXAP`을 포함시킨다. 상단 주석에 **§4의 관측 근거를 한 줄로 남긴다**(예: "232=현재AP(0~200, 상한 초과 0건 / 소비가 제2장-80·최종장-120과 일치), 233=최대AP(관측값 200 고정)").
   - 검증: 111.rrf를 normalize 했을 때 `AP_CHANGE` 이벤트가 193개 나오고 `unmapped_vars`에서 (0x00b0,232)/(0x00b0,233)이 사라진다.
3. `analyzer/analyzers/vitals.py`에 AP를 추가한다. `_series`/`_stats`를 재사용하고 `ap_note`는 제거하거나 "AP 패킷 없음"일 때만 남긴다.
   - `ap`, `ap_series`, `ap_max`(관측된 최대AP, 없으면 `None`)
   - `ap_spend`: 감소 1건마다 직전 1.2초 내 내 `SKILL_USE`를 찾아 **스킬별 소비량 실측표** `[{skill_id, name, ap, count}]`. 직전 스킬을 못 찾으면 `skill_id=None`으로 남긴다(추측 금지).
   - `ap_charge`: 증가량 분포 `{증가량: 횟수}`와 **분당 평균 충전량**(측정 구간 길이로 나눈 ○ 계산값). 증가 기록이 없으면 `None`.
   - AP 패킷이 전혀 없으면 모든 값을 `None`/빈 리스트로 두고 `note`에 "AP 기록 없음 — 4차 직업이 아니거나 패킷이 녹화되지 않음"을 넣는다. **0으로 채우지 말 것.**
4. `ui/pages/replay_page.py`의 바이탈 그래프/표에 AP를 더한다. 소비 실측표는 "스킬별 AP 소비 ●"로, 분당 충전량은 "○ 계산"으로 표기한다. AP가 없으면 **항목을 숨기거나 '기록 없음'**으로 쓴다(0 표시 금지).
5. `live/replay_engine.py`의 `unsupported` 목록에서 `'AP packet mapping'`을 지운다.

---

## 7. 작업 2 — LIVE AP 알림 (입력 없음)

**목표:** "AP 120 이상 — 최종장 사용 가능", "AP 0 — 충전 필요"를 **알려주기만** 한다. 키는 사용자가 누른다.

1. `ui/main_window.py`의 `_apply_alert_thresholds()`에 AP 규칙을 등록한다. `alert_ap_min`(이미 DEFAULTS에 있음)을 쓰고, "이상"을 표현하려면 무게와 같은 부호 반전 패턴(`-value` / `-threshold`)을 쓴다. 0이면 알림 끔.
2. 스냅샷 처리부(약 1199~1210행)에서 `vals.get('AP_REGION')`으로 `self.alerts.check('ap', ...)`를 호출한다. 값의 출처에 따라 신뢰도를 구분해 메시지에 표시한다: 리플레이 연결(`stage == 'CONFIRMED'`)이면 **● 측정**, 화면 게이지뿐이면 **△ 추정**.
3. `ui/settings_dialog.py`에 "AP 알림 기준(이상)" 입력을 추가하고 `alert_ap_min`으로 저장한다(기존 `alert_hp`/`alert_sp`/`alert_weight` 입력과 같은 방식).
4. HUD의 AP 항목은 이미 있다(`live/hud_model.py`). 리플레이가 붙어 패킷 AP가 들어오면 OCR 값 대신 그 값을 쓰고 신뢰도 표시를 ●로 바꾼다(HP/SP가 이미 그렇게 동작한다).
5. **알림 문구에 "자동으로 쓸까요" 류의 동작 제안을 넣지 않는다.** 상태만 알린다.

---

## 8. 작업 3 — 버프 루틴에 AP 조건 (선택, 작업 1·2 이후)

`core/buff_routine.py` / `ui/buff_routine_dialog.py`에 이미 "리플레이에서 실제로 걸린 버프만 골라 순서를 만들고, 사용자가 Space로 넘기며 직접 시전하는" 체크리스트가 있다(게임 입력 없음).

- `Step`에 `ap_min`(선택) 필드를 더해 "AP ○○ 이상일 때"를 조건으로 표시한다.
- LIVE AP가 조건을 만족하면 그 단계를 초록으로 강조한다. 조건 미달이면 "AP 부족(현재 ○○ / 필요 ○○)"로 표시한다. **AP를 모르면 '모름'으로 표시하고 조건 판정을 하지 않는다.**
- 매크로 내보내기 동작은 바꾸지 않는다: 대상 지정이 필요한 단계는 매크로로 내보내지 않고, 트리거 키를 누르고 있는 동안만 동작하는 기존 제약을 유지한다.

---

## 9. 테스트 요구사항

새 테스트 파일(예: `tests/test_vitals_ap.py`)에 다음을 포함한다.

**합성 데이터 테스트(항상 실행):**
- var 232/233 패킷 → `AP_CHANGE` 이벤트 생성, `max`/`value` 키 구분
- 소비 1건이 직전 스킬에 귀속되고, 직전 스킬이 없으면 `skill_id=None`
- AP 패킷이 없을 때 모든 AP 값이 `None`/빈 값이고 **0이 아님**
- 증가 기록이 없으면 분당 충전량이 `None`

**실제 리플레이 회귀 테스트(`helpers.sample()`이 `None`이면 skip):**
- `111.rrf`: AP 이벤트 193개, 값 범위 0~200, 최대AP 200, 소비 `제2장 : 심판자 80` 2건 + `최종장 : 멸마의 불꽃 120` 2건, 증가 `+4` 100건
- `132.rrf`: 소비 `제0격 : 파천격 200` 1건
- 아무 리플레이에서도 **현재 AP가 최대 AP를 넘지 않는다**(전수 0건이 측정됐으므로 불변식으로 검증 가능)

**알림 테스트:** `alert_ap_min=120`일 때 AP 119에서 안 울리고 120에서 울리며, `alert_cooldown` 안에서는 반복되지 않는다(`utilities/alerts.py`의 기존 테스트 패턴 참고).

**전체 스위트:** 실패 0, 기존 455개가 깨지지 않을 것.

---

## 10. 완료 보고 형식 (이 형식으로 보고할 것)

```
[만든 것]        파일별 한 줄 요약
[실측 근거]      새로 측정한 숫자(파일명·건수 포함). 측정하지 않은 값은 쓰지 말 것
[신뢰도 표기]    ● / ○ / △ 를 어디에 어떻게 표시했는지
[테스트]         신규 N개 / 전체 N개 통과 · 실패 0 · 건너뜀 N (실행 명령 포함)
[데이터 없음]    카디날 등 확보하지 못한 항목과, 그래서 무엇을 하지 않았는지
[미해결]         321qq.rrf(job 5) AP 155 같은 설명 못 한 관측
[만들지 않은 것] 자동 AP 충전 — 금지 사유 명시
```

---

## 11. 체크리스트 (제출 전 자기 점검)

- [ ] 게임에 입력을 보내는 코드를 추가하지 않았다
- [ ] 자동 AP 충전/자동 시전을 만들지 않았다
- [ ] AP가 없는 리플레이에서 0이나 임의 값을 보여 주지 않는다
- [ ] 카디날 수치를 추측해 넣지 않았다
- [ ] `config/settings.py` DEFAULTS에 새 설정 키를 등록했다
- [ ] 사용자 화면에 `var 232`, `0x00b0` 같은 표현이 없다(툴팁·개발자 모드 제외)
- [ ] 전체 테스트 실패 0
- [ ] 요청과 무관한 파일을 수정하지 않았다
