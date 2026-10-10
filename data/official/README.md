# 라그나로크 공식 홈페이지 자료 (2026-09-25 수집)

- `attr_table.json`: 게임정보 > 라그 시작하기 > 기본시스템의 속성표 (피해자 속성 Lv.1~4)
  https://ro.gnjoy.com/guide/ragstart/basic4.asp
- `monsters.json`: 룬미드가츠 도서관 > 몬스터 목록(이름·분류·레벨·크기·종족·속성·HP)과 몬스터 상세의 출현 지역
  https://ro.gnjoy.com/guide/runemidgarts/monsterlist.asp
  - `maps` 칸이 없는 몬스터는 출현 지역을 아직 모으지 못한 것(홈페이지 요청 제한으로 천천히 수집)
  - 홈페이지 표 일부는 칸이 밀려 있어 크기·종족이 `null`(모름)인 몬스터가 있다. 속성은 그대로 사용

홈페이지 안내대로 게임 업데이트에 따라 실제와 다를 수 있다. © Gravity Co., Ltd.
`core/elements.py`는 이 자료를 먼저 쓰고, 여기서 찾지 못한 몬스터만 `data/rathena` 자료로 추정한다.
