# rAthena 자료

- `attr_fix.yml`: 속성 상성표 (db/re/attr_fix.yml)
- `mob_index.json`: `mob_db.yml`(db/re/mob_db.yml)에서 추천 속성 계산에 필요한 칸만 뽑은 목록. `core/elements.py`가 만든다.
  원본 `mob_db.yml`(약 2MB)을 이 폴더에 두면 더 새로울 때 자동으로 다시 만든다.

- `item_prices.json`: 상점(NPC)에 팔 때의 기준가. db/re 의 item_db_etc / item_db_usable / item_db_equip 에서 아이템 번호와
  Buy·Sell 만 뽑았다(Sell 이 없으면 Buy 의 절반). `tools/build_item_prices.py`가 만든다(2026-10-10 내려받음, 11,055종).
  획득 아이템 표의 "상점가 △"가 이 값에 오버차지 %를 곱한 것이다. 국내 서버 실제 판매가와 대조한 적은 없다.

출처: https://github.com/rathena/rathena (2026-09-25 내려받음) · © rAthena Development Team · GPL-3.0-or-later.
외부 서버 구현 자료라 국내 서버 실제 값과 다를 수 있고, 최신 국내 몬스터는 빠져 있을 수 있다.
지금은 `data/official`(공식 홈페이지)에서 찾지 못한 몬스터·속성표가 없을 때만 보충용으로 쓴다.
