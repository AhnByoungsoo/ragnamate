# 스킬·버프 이름과 아이콘 — 2026-09-26

전체 직업에 공통으로 적용되는 번호 기반 목록을 추가했습니다. 스킬 ID와 버프 EFST ID는 별도 공간이며, 번호가 같다는 이유로 연결하지 않습니다.

- 스킬 이름 1,952개: 한국어 1,702개, 나머지는 외부 원문 이름.
- 버프·상태 표시 이름/설명 847개: 한국어 738개. 제목 없이 시간만 표시하는 클라이언트 항목은 `%s` 대신 실제 상태 설명을 사용합니다.
- 실제 이미지 2,241개: 스킬 1,405개, 버프·상태 836개. 실행 중 인터넷 요청 없이 로컬 파일을 읽습니다.
- 6513은 ABC_CHASING_BREAK / 체이싱 브레이크. 공식 웹 목록에서 빠져 있어 공개 kRO DB와 최신 클라이언트 ID 표를 대조했습니다.

적용 화면: 리플레이 콤보 후보·스킬 행, 리플레이 스킬·버프 표, A/B 비교 표, 진행 중 버프 표, 버프 아이콘 오버레이와 LIVE 상태 아이콘. 이름 출처는 스킬·버프 표 및 콤보 툴팁에서 볼 수 있습니다. 기존 사용자 지정 이름·아이콘이 우선합니다. 공식 한국어, 공개 한국 클라이언트, 외부 번역, 과거 가져오기 순으로 기본 이름을 선택합니다.

## 출처

- [한국 공식 스킬 안내](https://ro.gnjoy.com/guide/runemidgarts/skilllist.asp): 기존 공식 이름 1,185개와 목록에 연결된 이미지. BMP URL에 PNG/GIF가 반환되는 항목은 실제 파일 형식으로 저장했습니다.
- [ROResourceCollection kRO](https://github.com/bgamez23/ROResourceCollection/tree/master/kRO): 공개 한국 클라이언트 SkillID/SkillInfoList, EFSTIDs/StateIconInfo. 서비스 운영사가 직접 제공하는 최신 공식 배포본이라는 뜻은 아닙니다.
- [ROenglishRE](https://github.com/llchrisll/ROenglishRE): 최신 스킬·상태 번호, 번역 이름, 상태 아이콘 파일명. 원 번역 zackdreaver, 유지 관리 llchrisll. 해당 번역 자료의 저작자는 위 프로젝트이며 라그메이트가 작성한 번역이 아닙니다.
- [rAthena 스킬 DB](https://github.com/rathena/rathena/blob/master/db/re/skill_db.yml): 이름이 빠진 NPC·공격 하위 ID의 참고 이름.
- [roBrowserLegacy 상태 자료](https://github.com/MrAntares/roBrowserLegacy/tree/master/src/DB/Status): 기본 EFST와 아이콘 파일명의 명시적 매핑. Vincent Thibault 및 프로젝트 기여자.
- [ragassets](https://github.com/adsonpleal/ragassets): 공개 클라이언트에서 추출한 실제 이미지 제공. 한국 서버의 현행 스킬 수치·쿨타임 자료로 사용하지 않았습니다.
- [RagnaPlace 체이싱 브레이크](https://ragnaplace.com/ko/kro/skill/6513): 6513 한국어 이름과 이미지.
- [Divine Pride 이미지 문서](https://www.divine-pride.net/tools/api-doc): 실제 리플레이에서 누락된 스킬 6종·버프 2종의 공개 이미지.

게임 이미지의 권리는 Gravity 및 원 권리자에게 있습니다. 각 데이터의 URL·수집 날짜·심볼은 `data/skill_catalog.json`, `data/buff_catalog.json`, 이미지의 URL·SHA-256은 `data/icon_catalog.json`에 기록했습니다. `data/catalog_sources.json`에는 수집한 원문 자료의 해시가 있습니다.

## 확인 범위와 남은 항목

검증한 실제 리플레이는 321, 111, 213, 3, 인퀴, qewq의 6개(어비스체이서·인퀴지터·아크메이지·슈라)입니다. 이 리플레이에서 관측한 모든 스킬 ID의 이름과 내 캐릭터의 시전 스킬 아이콘이 확인되었습니다. 체이싱 브레이크와 인퀴지터 연격의 등록은 임시 매크로 설정으로 확인했으며 게임 입력은 보내지 않았습니다.

모든 게임 상태에 아이콘이 있는 것은 아닙니다. 우편 개수·접속 시간·카트 등 내부 상태, 아이콘 없는 NPC 공격, 아직 자료 없는 EFST(예: 2161)는 별도 상태로 남깁니다. 모르는 항목을 다른 스킬·버프 이미지로 채우지 않습니다. 최신 데이터의 제목 없는 ID와 수집하지 못한 아이콘은 `data/catalog_coverage.json`에서 확인할 수 있습니다. 자료에 있는 이름도 현재 한국 서버 화면과 다를 수 있어 외부 출처 표시는 유지합니다.

프로그램을 완전히 종료하고 통합 폴더의 실행 파일로 다시 실행해야 새 목록이 로드됩니다. 이미 저장한 분석 기록의 원본과 사용자 매크로 설정은 변경하지 않습니다.
