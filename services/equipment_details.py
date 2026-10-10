"""노점의 슬롯 이름을 로컬 아이템 설명과 대조한다. 유사 이름으로 효과를 추정하지 않는다."""
import re

from core.item_category import category


def clean_description(text):
    return re.sub(r'\^[0-9a-fA-F]{6}', '', text or '').strip()


def item_description(item_db, item_id):
    item = (getattr(item_db, 'items', {}) or {}).get(item_id) or {}
    return clean_description(item.get('description')) or '저장된 아이템 DB에 장비 옵션 설명이 없습니다.'


def name_key(name):
    # 공식 사이트와 클라이언트의 띄어쓰기 차이만 허용. 레벨·괄호·숫자는 보존한다.
    return ''.join((name or '').split())


class OptionIndex:
    def __init__(self, item_db):
        self.item_db = item_db
        self._items = None
        self._count = -1
        self._names = {}

    def find(self, name):
        items = getattr(self.item_db, 'items', {}) or {}
        if items is not self._items or len(items) != self._count:
            self._items, self._count = items, len(items)
            self._names = {}
            for iid, item in items.items():
                if isinstance(item, dict) and item.get('name'):
                    self._names.setdefault(name_key(item['name']), []).append(dict(item, item_id=iid))
        return self._names.get(name_key(name), [])

    def option(self, name):
        matches = self.find(name)
        kind = '카드·인챈트'
        if len(matches) == 1:
            item = matches[0]
            # 상세의 슬롯정보에는 카드와 인챈트가 함께 온다.
            kind = '카드' if category(item) == '카드' or name_key(item['name']).endswith('카드') else '인챈트'
        return dict(name=name, kind=kind, matches=matches)


def spec_text(row):
    refine = '+%d 제련' % row['refine'] if row.get('refine') else '제련 표시 없음'
    grade = '등급 %s' % row['grade'] if row.get('grade') else '등급 표시 없음'
    slots = '슬롯 %d' % row['slots'] if row.get('slots') is not None else '슬롯 미확인'
    return ' · '.join((refine, grade, slots))


def option_summary(detail):
    if detail is None:
        return '상세 미조회 — 두 번 눌러 확인'
    names = detail.get('cards') or []
    return '\n'.join(names) if names else '등록된 카드·인챈트 없음'
