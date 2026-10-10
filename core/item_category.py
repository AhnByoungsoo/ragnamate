"""아이템 분류: 게임 아이템 설명의 '계열 :' 줄(약 1만 종)과 Replay에서 관측한 분류로 나눈다.

설명에 계열이 없는 소비·기타 아이템은 관측 분류가 없으면 설명 문구('회복', '사용 시')로 소비를 어림한다.
"""
import re

CATEGORIES = ['전체', '무기', '방어구', '쉐도우 장비', '의상', '카드', '인챈트', '소비', '기타']
_WEAPON = ('검', '창', '활', '지팡이', '단검', '둔기', '도끼', '책', '수리검', '리볼버', '라이플', '샷건', '개틀링',
           '그레네이드런처', '악기', '채찍', '카타르', '너클', '투사체', '클로', '총')
_ARMOR = ('투구', '갑옷', '걸칠것', '신발', '방패', '액세서리')
_OBSERVED = {'소비': '소비', '회복': '소비', '소비(지연)': '소비', '무기': '무기', '방어구': '방어구', '카드': '카드',
             '기타': '기타', '화살/탄환': '기타', '캐시': '기타', '펫 장비': '기타'}
_COLOR = re.compile(r'\^[0-9a-fA-F]{6}')


def category(item, observed=None):
    """item: item_db 항목 dict(name, description). observed: Replay 관측 분류 글."""
    desc = _COLOR.sub('', (item or {}).get('description') or '')
    m = re.search(r'계열\s*:\s*([^\n]+)', desc)
    if m:
        kind = m.group(1)
        head = kind.split()[0] if kind.split() else ''
        for word, cat in (('카드', '카드'), ('쉐도우', '쉐도우 장비'), ('의상', '의상'), ('인챈트', '인챈트')):
            if word in kind:
                return cat
        if '공격' in kind or any(w in head for w in _WEAPON):
            return '무기'
        if any(w in kind for w in _ARMOR):
            return '방어구'
    if observed in _OBSERVED:
        return _OBSERVED[observed]
    if re.search(r'회복|사용\s*시|사용하면', desc):
        return '소비'
    return '기타'


def categorize_all(item_db):
    """{item_id: 분류} — item_db.items 전체(관측 분류가 있으면 함께 씀)."""
    observed = getattr(item_db, 'categories', {}) or {}
    return {iid: category(it, observed.get(iid)) for iid, it in (getattr(item_db, 'items', {}) or {}).items()
            if isinstance(it, dict)}
