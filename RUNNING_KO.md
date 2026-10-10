# 실행 방법

이 PC의 현재 작업 폴더에서는 ZIP 옆의 **`이_PC에서_실행.cmd`**로 바로 실행할 수 있다. 이번 검증에 준비한 Codex Python과 `work/deps`를 사용하며 설치/삭제를 수행하지 않는다. 다른 PC로 이동하거나 작업 폴더를 정리한 경우에는 아래 일반 설치 방법을 사용한다. ZIP 안의 소스 실행은 특정 Codex 환경을 요구하지 않는다.

## 일반 실행

1. ZIP을 **새 폴더**에 풀어 실행한다. 기존 설치 폴더 위에 덮어쓰면 기존 설정/DB 파일이 덮일 수 있으므로 새 폴더 실행을 권장한다.
2. Python 3.12 환경에서 `run_replay.bat`을 실행한다. 처음에는 이 프로젝트 안에 `.venv`를 만들고 `requirements-replay.txt`를 설치한다.
3. 설정에서 Ragnarok 설치 폴더와 Replay 폴더를 선택한다.
4. Live를 켜고 게임에서 Replay 녹화를 시작한다. 게임 창 캡처 설정을 하지 않아도 Replay worker는 동작한다.
5. Live 탭에서 값과 품질 표시를 확인한다. 설정 > 개발자 모드를 켜면 Diagnostics/Accuracy가 나온다.

직접 실행:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-replay.txt
.\.venv\Scripts\python.exe main.py
```

Python 위치를 직접 지정할 경우 `RO_PYTHON` 환경변수에 python.exe의 절대경로를 지정하고 `run_replay.bat`을 실행할 수 있다.

이 작업의 GUI 검증은 Python 3.12.14, PySide6 6.11.2에서 했다. 기존 프로젝트에는 EXE 빌드 설정이 없었으며, 이번 결과물은 소스 실행판이다. EXE를 빌드했다고 주장하지 않는다.

## 기존 사용자 데이터

첨부본의 `config/settings.json`, `data/market.db`, `price_cache.json`, `data/guides`를 보존했다. 이후 원래 설치 폴더에서 갱신된 데이터가 있다면 앱을 종료한 상태에서 원하는 설정/DB를 새 폴더로 복사하여 사용한다.

새 LIVE 기록은 `data/live_replay.db`에 저장된다. `live_replay_checkpoints`와 `live_accuracy_reports`가 추가되며 기존 DB는 그대로 둔다. 실제 개인 Replay fixture는 결과물에 넣지 않았다.

## 화면 캡처/OCR 기능

Replay 실행용 최소 의존성은 `requirements-replay.txt`, 기존의 Windows 캡처·OCR까지 포함한 목록은 `requirements.txt`이다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

한국어 Windows OCR이 없으면 화면 글자 인식 기능은 사용할 수 없지만 Replay 분석은 계속 동작한다. 기존 Capture/Hotkey/Overlay 기능 코드는 유지했다.

## 중요한 표시 의미

* PROVISIONAL: 아직 확인된 packet이 없거나 입력/순서를 확인하는 중.
* CONFIRMED: 완전한 packet과 reorder 과정을 통과한 현재 집계. packet 내용의 의미 검증 여부(△)와는 별개다.
* FINAL: 종료 추정 후 전체 Offline 분석으로 생성한 결과. 기록이 다시 자라면 이전 FINAL을 폐기 표시하고 이어서 분석한다.
* `-` / 미지원·미수신은 0이 아니다. AP 매핑, 실제 아이템 획득 packet, 일부 EXP/Skill packet은 아직 검증 범위 밖이다.
* 기본 설정에서 `0x01de` 스킬 피해 후보는 제외된다. 설정의 실험적 패킷 해석을 켜면 후보를 사용하지만 미검증 △로 표시된다. 설정을 바꿨으면 Live를 껐다 켜서 새 Decoder에 적용한다.
* Buff 예상 종료와 패킷으로 확인한 종료를 구분한다. Accuracy의 buff_ms는 기존 Offline의 명시적 END/파일 끝 기준이다.
* Profit는 로컬 시세를 이용한 인벤토리 순증감 추정이다. 실제 판매 수익/몬스터별 드랍 확정값이 아니다.

설정에서 read/reorder/snapshot/정체/종료 추정 간격을 조절할 수 있다. 기본값은 100ms / 250ms / 200ms / 5초 / 60초다. 종료 감지는 파일 무변화에 근거한 추정이다.

## 회귀 테스트와 외부 Golden fixture

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$env:RO_REPLAY_FIXTURES = 'C:\Gravity\Ragnarok\Replay'
.\.venv\Scripts\python.exe -m live.simulator --fixture-dir $env:RO_REPLAY_FIXTURES --runs 100 --output accuracy.json
.\.venv\Scripts\python.exe -m live.simulator --fixture-dir $env:RO_REPLAY_FIXTURES --runs 100 --experimental --output accuracy-experimental.json
```

Simulator는 원본 파일을 읽은 뒤 **별도의 임시 파일**에 임의 크기 조각을 쓰며 테스트한다. 원본 Replay에는 쓰지 않는다. `validation/`에 이번 테스트 결과를 포함했다.

`Replay FINAL 기록`을 더블클릭하면 기존 Offline 분석 화면으로 다시 열 수 있다. 기존 History·빌드 라벨·A/B 비교·Export 기능은 그 경로로 계속 사용한다.

게임 메모리, DLL injection, 네트워크 interception/injection, 자동 키·스킬·NPC·이동은 사용하지 않는다.
