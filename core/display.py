"""사용자 화면에 보일 이름을 한 곳에서 만든다. 내부 ID/영문 Symbol은 그대로 두고 표시만 바꾼다.

원칙
- 확인된 것(사용자가 직접 지정, 클라이언트 아이템/맵 이름)은 그대로 쓴다.       → CONFIRMED ●
- 외부 목록의 영문 Symbol은 아는 단어만 한국어로 풀어 쓰고 '추정'으로 표시한다. → DERIVED ◐
- 풀 수 없으면 '알 수 없는 효과'로 두고, 원문은 개발자 모드에서만 보여준다.     → UNKNOWN ○
추측으로 아이템 이름을 확정하지 않는다(예: ALMIGHTY를 올마이티로 단정하지 않고 '확인 필요'로 남긴다).
"""
import re
from dataclasses import dataclass
from core.identifiers import numeric_id

CONFIRMED, DERIVED, UNKNOWN = 'CONFIRMED', 'DERIVED', 'UNKNOWN'
CONF_ICON = {CONFIRMED: '●', DERIVED: '◐', UNKNOWN: '○'}
CONF_TEXT = {CONFIRMED: '확인된 이름', DERIVED: '외부 자료 또는 영문 이름에서 얻은 참고 이름 — 확인 필요', UNKNOWN: '이름 확인 안 됨'}

UNKNOWN_STATUS = '알 수 없는 효과'
UNKNOWN_SKILL = '알 수 없는 스킬'

# EFST 영문 Symbol에 자주 나오는 낱말 (rAthena 공개 목록 기준). 아는 낱말만 옮긴다.
WORDS = {
    'STR': '힘', 'AGI': '민첩', 'VIT': '체력', 'INT': '지력', 'DEX': '손재주', 'LUK': '행운',
    'POW': '파워', 'STA': '스태미나', 'WIS': '위즈덤', 'SPL': '스펠', 'CON': '컨센트레이션', 'CRT': '크리에이티브',
    'ATK': '공격력', 'MATK': '마법 공격력', 'DEF': '방어력', 'MDEF': '마법 방어력', 'HIT': '명중',
    'FLEE': '회피', 'CRI': '크리티컬', 'ASPD': '공격 속도', 'MOVE': '이동', 'SPEED': '속도',
    'HP': 'HP', 'SP': 'SP', 'AP': 'AP', 'EXP': '경험치', 'PLUSEXP': '경험치 증가', 'DROP': '드랍률',
    'FOOD': '음식', 'CASH': '캐시', 'SCROLL': '스크롤', 'POTION': '포션', 'BUFF': '버프', 'BOX': '상자',
    'HEAL': '회복', 'REGEN': '회복', 'RECOVERY': '회복', 'RESIST': '저항', 'IMMUNE': '면역',
    'STUN': '기절', 'SLEEP': '수면', 'FREEZE': '빙결', 'CURSE': '저주', 'POISON': '독', 'SILENCE': '침묵',
    'BLIND': '암흑', 'BLEEDING': '출혈', 'CONFUSION': '혼란', 'STONE': '석화',
    'GUILD': '길드', 'PARTY': '파티', 'PET': '펫', 'HOMUN': '호문클루스', 'MERCENARY': '용병',
    'CART': '카트', 'WEIGHT': '무게', 'ZENY': '제니', 'STORE': '노점', 'VENDING': '노점',
    'INC': '증가', 'DEC': '감소', 'UP': '증가', 'DOWN': '감소', 'PLUS': '증가', 'MINUS': '감소',
    'ITEM': '아이템', 'SKILL': '스킬', 'MAGIC': '마법', 'ELEMENT': '속성', 'SHIELD': '방패', 'ARMOR': '방어구',
    'WEAPON': '무기', 'REFINE': '제련', 'ENCHANT': '인챈트', 'TIME': '시간', 'COUNT': '수', 'BONUS': '보너스',
}
_TRAILING = ('증가', '감소')
# 통째로 뜻이 굳어진 것 (낱말 조합으로는 풀 수 없는 것만)
PHRASES = {
    'ALMIGHTY': '올마이티(전 능력치)',
    'PCCAFE_PLAY_TIME': 'PC방 이용 시간',
    'PLAYTIME_STATISTICS': '접속 시간 기록',
    'DAILYSENDMAILCNT': '하루 우편 발송 수',
    'ASSISTANT_VENDING': '노점 도우미',
    'M_DEFSCROLL': '방어 스크롤',
    'CASH_PLUSEXP': '경험치 증가(캐시)',
    'CASH_RECEIVEITEM': '드랍률 증가(캐시)',
    'CASH_DEATHPENALTY': '사망 경험치 감소(캐시)',
    # 2026-09-24 실제 Replay(바포메트 서버)에 자주 나온 것. 추정(◐) — 더블클릭으로 고치면 그 이름이 확정(●)된다
    'BLESSING': '블레싱',
    'EXPLOSIONSPIRITS': '폭기',
    'MAGICPOWER': '마력 증폭',
    'RAISINGDRAGON': '잠룡승천',
    'GENTLETOUCH_ENERGYGAIN': '점혈 -쾌-',
    'GENTLETOUCH_REVITALIZE': '점혈 -활-',
    'GN_CARTBOOST': '카트 부스트',
    'COMPETENTIA': '콤페텐티아',
    'LIMIT_POWER_BOOSTER': '리미트 파워 부스터',
    'S_LIFEPOTION': '생명력 회복 물약(소)',
    'M_LIFEPOTION': '생명력 회복 물약(중)',
    'L_LIFEPOTION': '생명력 회복 물약(대)',
    'S_MANAPOTION': '마나 회복 물약(소)',
    'M_MANAPOTION': '마나 회복 물약(중)',
    'L_MANAPOTION': '마나 회복 물약(대)',
}
# 버프가 아니라 게임이 내부적으로 쓰는 표시(우편 개수 등). 기본 화면에서는 감춘다.
INTERNAL_PATTERNS = (
    re.compile(r'^GET_CNT_'), re.compile(r'^AID_PERIOD_'), re.compile(r'^PLAYTIME_'),
    re.compile(r'^DAILYSEND'), re.compile(r'^PCCAFE_'), re.compile(r'^ASSISTANT_'),
    re.compile(r'^PERIOD_'), re.compile(r'^POSTDELAY$'), re.compile(r'^ON_PUSH_CART$'),
)
INTERNAL_LABEL = {
    'GET_CNT_UNREAD_RODEX_CHARDB': '읽지 않은 우편(캐릭터)',
    'GET_CNT_UNREAD_RODEX_GLOBALDB': '읽지 않은 우편(계정)',
    'GET_CNT_UNREAD_RETURN_RODEX_CHARDB': '반송된 우편',
    'POSTDELAY': '스킬 후딜레이',          # 스킬을 쓸 때마다 걸리는 내부 상태 — 다시 쓸 버프가 아님
    'ON_PUSH_CART': '카트 사용 중',
}
# 몬스터·상대가 거는 상태이상/약화 — 내가 다시 걸 '버프'가 아니므로 버프 타이머에서 뺀다(이름이 뜻을 그대로 말하는 것만).
DEBUFF_SYMBOLS = frozenset((
    'STONE', 'FREEZE', 'STUN', 'SLEEP', 'POISON', 'CURSE', 'SILENCE', 'CONFUSION', 'BLIND', 'BLEEDING', 'DPOISON',
    'FEAR', 'BURNING', 'FREEZING', 'CRYSTALIZE', 'DEEP_SLEEP', 'HALLUCINATION', 'DEC_AGI', 'DECREASEAGI', 'SLOWCAST',
    'CRITICALWOUND', 'ALL_STAT_DOWN', 'MANDRAGORA', 'CURSEDCIRCLE_TARGET', 'WIDEWEB', 'QUAGMIRE', 'ANKLESNARE',
    'LEXAETERNA', 'LEXDIVINA', 'NETHERWORLD', 'MARSHOFABYSS', 'TOXIN', 'PARALYSE', 'VENOMBLEED', 'MAGICMUSHROOM',
    'DEATHHURT', 'PYREXIA', 'OBLIVIONCURSE', 'LEECHESEND', 'STRIPWEAPON', 'STRIPSHIELD', 'STRIPARMOR', 'STRIPHELM',
    'NOEQUIPWEAPON', 'NOEQUIPSHIELD', 'NOEQUIPARMOR', 'NOEQUIPHELM', 'SPIDERWEB', 'ELECTRICSHOCKER', 'BITE',
    'THORNS_TRAP', 'VACUUM_EXTREME', 'WHITEIMPRISON', 'STASIS', 'GRAVITATION', 'DEFENDER_DOWN', 'HANDICAPSTATE_DEEPBLIND',
))
PERIOD = re.compile(r'^PERIOD_(.+)$')
SPELLBOOK = re.compile(r'^SPELLBOOK\d+$')
STAT_FOOD = re.compile(r'^FOOD_(STR|AGI|VIT|INT|DEX|LUK|POW|STA|WIS|SPL|CON|CRT)(_CASH)?$')
AID_PERIOD = re.compile(r'^AID_PERIOD_(.+)$')


@dataclass
class Name:
    """화면에 쓸 이름 + 어디서 왔는지."""
    text: str
    confidence: str = UNKNOWN
    raw: str = ''                 # 내부 Symbol/ID (개발자 모드에서만 표시)
    internal: bool = False        # 게임 내부 상태(버프가 아님)
    source: str = ''

    @property
    def icon(self):
        return CONF_ICON.get(self.confidence, '○')

    def with_raw(self, developer):
        """개발자 모드면 원문을 같이 보여준다."""
        return '%s  (%s)' % (self.text, self.raw) if developer and self.raw else self.text

    def tooltip(self, extra=''):
        parts = [self.text, CONF_TEXT.get(self.confidence, '')]
        if self.raw:
            parts.append('내부 이름: %s' % self.raw)
        if extra:
            parts.append(extra)
        if self.source:
            parts.append(self.source)
        return '\n'.join(p for p in parts if p)


def _words(symbol):
    """영문 Symbol을 아는 낱말만 한국어로. 하나도 모르면 None."""
    parts = [p for p in symbol.split('_') if p]
    out, known = [], 0
    for p in parts:
        word = WORDS.get(p)
        if word:
            out.append(word)
            known += 1
        else:
            out.append(p)
    if not known or known < len(parts) / 2:
        return None
    if len(out) > 1 and out[0] in _TRAILING:        # INC_AGI → '민첩 증가'
        out = out[1:] + [out[0]]
    return ' '.join(out)


def status_kind(symbol):
    """EFST 영문 이름 → 'debuff'(상태이상·약화) | 'buff'. 모르면 'buff'로 둔다(함부로 숨기지 않는다)."""
    sym = (symbol or '')
    sym = sym[5:] if sym.startswith('EFST_') else sym
    return 'debuff' if sym in DEBUFF_SYMBOLS or sym.startswith('HANDICAPSTATE_') else 'buff'


def status_text(symbol):
    """EFST 영문 이름 → (한국어, 신뢰도, 내부상태 여부). 모르면 (None, ...)."""
    if not symbol:
        return None, UNKNOWN, False
    sym = symbol[5:] if symbol.startswith('EFST_') else symbol
    internal = any(p.match(sym) for p in INTERNAL_PATTERNS)
    if sym in INTERNAL_LABEL:
        return INTERNAL_LABEL[sym], DERIVED, True
    if sym in PHRASES:
        return PHRASES[sym], DERIVED, internal
    m = STAT_FOOD.match(sym)
    if m:
        return '%s 증가 음식%s' % (WORDS[m.group(1)], '(캐시)' if m.group(2) else ''), DERIVED, internal
    m = AID_PERIOD.match(sym) or PERIOD.match(sym)
    if m:
        return '기간제 혜택', DERIVED, True
    if SPELLBOOK.match(sym):
        return '저장된 마법(리딩 스펠북)', DERIVED, internal
    text = _words(sym)
    return (text, DERIVED, internal) if text else (None, UNKNOWN, internal)


class DisplayNames:
    """아이템/스킬/버프/몬스터/맵/직업 이름을 사용자 표시용으로 바꾼다."""

    def __init__(self, gamedata, item_db=None):
        self.g = gamedata
        self.items = item_db if item_db is not None else getattr(gamedata, 'items', None)
        self._unknown_seq = {}                   # efst → 화면에서 서로 구분하기 위한 번호

    # ---------------------------------------------------------------- 개별 해석
    def item(self, item_id):
        item_id = numeric_id(item_id)
        if item_id is None:
            return Name('아이템 ID 확인 안 됨', UNKNOWN)
        name = self.items.name(item_id) if self.items else None
        known = getattr(self.items, 'known', lambda i: True)(item_id) if self.items else False
        if known and name and not str(name).startswith(('아이템 #', '알 수 없는 아이템')):
            return Name(name, CONFIRMED, str(item_id))
        return Name('아이템 #%s' % item_id, UNKNOWN, str(item_id))

    def skill(self, skill_id):
        skill_id = numeric_id(skill_id)
        if skill_id is None:
            return Name(UNKNOWN_SKILL, UNKNOWN)
        raw = self.g.skills.name(skill_id) if self.g else None
        origin = getattr(self.g.skills, 'origin', lambda i: '')(skill_id) if self.g else ''
        if raw and origin:                      # 이름 파일에 있는 것만 실제 이름으로 본다
            meta = getattr(self.g.skills, 'metadata', lambda i: {})(skill_id)
            if origin in ('user', 'official_kro'):
                meta = getattr(self.g.skills, 'metadata', lambda i: {})(skill_id)
                source = ('한국 공식 스킬 ID·이름 · %s\n%s' % (meta.get('checked_at', ''), meta.get('url', ''))
                          if origin == 'official_kro' else '사용자 지정 이름')
                return Name(raw, CONFIRMED, str(skill_id), source=source)
            if origin in ('client_kro', 'client_translation', 'kro_reference'):
                return Name(raw, DERIVED, '%s · %s' % (skill_id, meta.get('symbol', '')),
                            source='공개 클라이언트/외부 DB 이름 · %s\n%s' % (meta.get('checked_at', ''), meta.get('url', '')))
            text, _, _ = status_text(raw)
            return Name(text or raw, DERIVED, '%s · %s' % (skill_id, raw))
        return Name(UNKNOWN_SKILL, UNKNOWN, str(skill_id))

    def status(self, efst):
        """버프/상태 이름. 사용자가 지정한 이름 > 영문 Symbol 풀이 > 알 수 없는 효과."""
        efst = numeric_id(efst)
        if efst is None:
            return Name(UNKNOWN_STATUS, UNKNOWN)
        raw = self.g.buffs.name(efst) if self.g else None
        origin = self.g.buffs.origin(efst) if self.g and hasattr(self.g.buffs, 'origin') else ''
        if raw and origin:                      # 이름 파일에 있는 것만 (없으면 '알 수 없는 효과')
            meta = getattr(self.g.buffs, 'metadata', lambda i: {})(efst)
            symbol = meta.get('symbol', raw)
            _, _, internal = status_text(symbol)
            if origin == 'user':
                return Name(raw, CONFIRMED, 'EFST %s' % efst, internal)
            if origin in ('client_kro', 'client_translation', 'kro_reference'):
                return Name(raw, DERIVED, 'EFST %s · %s' % (efst, symbol), internal,
                            source='공개 클라이언트 상태 이름 · %s\n%s' % (meta.get('checked_at', ''), meta.get('url', '')))
            if re.search(r'[가-힣]', raw):
                return Name(raw, DERIVED, 'EFST %s' % efst, source='가져온 이름 · 게임 내 확인 필요')
            text, conf, internal = status_text(raw)
            link = getattr(self.g, 'buff_links', {}).get(str(efst), {})
            if not internal and link.get('symbol') == raw.removeprefix('EFST_') and link.get('name'):
                return Name(link['name'], DERIVED, 'EFST %s · %s' % (efst, raw),
                            source='rAthena 상태 연결 참고 · 실제 사용 스킬·아이템은 미확정\n' + link.get('skill_url', ''))
            if text:
                return Name(text, conf, 'EFST %s · %s' % (efst, raw), internal)
            # 영문 Symbol을 한국어로 풀 수 없으면 그대로 보여주지 않는다(원문은 Tooltip/개발자 모드에서만)
            return Name(self._unknown_label(efst), UNKNOWN, 'EFST %s · %s' % (efst, raw), internal)
        return Name(self._unknown_label(efst), UNKNOWN, 'EFST %s' % efst)

    def status_kind(self, efst):
        """'debuff' | 'buff' — 상태 이름 파일의 영문 Symbol로 판단한다."""
        efst = numeric_id(efst)
        if efst is None or not self.g:
            return 'buff'
        meta = getattr(self.g.buffs, 'metadata', lambda i: {})(efst) or {}
        return status_kind(meta.get('symbol') or self.g.buffs.name(efst))

    def _unknown_label(self, efst):
        """이름을 모르는 효과가 여러 개일 때 서로 구분되도록 번호를 붙인다(같은 효과 = 같은 번호)."""
        if efst not in self._unknown_seq:
            self._unknown_seq[efst] = len(self._unknown_seq) + 1
        return '%s %d' % (UNKNOWN_STATUS, self._unknown_seq[efst])

    def monster(self, class_id):
        name = self.g.monsters.name(class_id) if self.g else None
        if name and not str(name).startswith(('mob', '몬스터 #', '알 수 없는 몬스터')):
            return Name(name, CONFIRMED, str(class_id))
        return Name('알 수 없는 몬스터', UNKNOWN, str(class_id))

    def map(self, map_id):
        """맵 ID → 한국어 이름. MapDB는 '이름 (id)' 형식이라 ID 부분을 떼어 raw로 옮긴다."""
        if not map_id:
            return Name('위치 확인 안 됨', UNKNOWN, '')
        name = self.g.maps.name(map_id) if self.g and getattr(self.g, 'maps', None) else None
        if name:
            name = name.replace('(%s)' % map_id, '').strip()
        if name and name not in (map_id, '(알 수 없음)'):
            return Name(name, CONFIRMED, map_id)
        return Name('%s(이름 미확인)' % map_id, UNKNOWN, map_id)

    def job(self, job_id):
        name = self.g.job_name(job_id) if self.g else None
        return Name(name, CONFIRMED, str(job_id)) if name and name != '-' else Name('직업 확인 안 됨', UNKNOWN,
                                                                                    str(job_id))
