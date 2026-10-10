"""몬스터 속성·종족·크기와 속성 상성표로 '추천 공격 속성'을 계산한다.

자료 우선순위
 1. 라그나로크 공식 홈페이지 (data/official): 룬미드가츠 도서관 몬스터 목록·출현 지역, 기본시스템 속성표
 2. rAthena db/re (data/rathena, GPL-3.0-or-later): 공식 목록에서 찾지 못한 몬스터만 보충 — 화면에 '추정'으로 표시
"""
import json
import logging
import os
from collections import Counter

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATHENA_DIR = os.path.join(BASE_DIR, 'data', 'rathena')
OFFICIAL_DIR = os.path.join(BASE_DIR, 'data', 'official')

ELEMENTS = ('Neutral', 'Water', 'Earth', 'Fire', 'Wind', 'Poison', 'Holy', 'Dark', 'Ghost', 'Undead')
ELEMENT_KO = dict(zip(ELEMENTS, ('무', '수', '지', '화', '풍', '독', '성', '암', '염', '불사')))   # 공식 표기
RACE_KO = {'Formless': '무형', 'Undead': '불사형', 'Brute': '동물형', 'Plant': '식물형', 'Insect': '곤충형',
           'Fish': '어패형', 'Demon': '악마형', 'Demihuman': '인간형', 'Angel': '천사형', 'Dragon': '용족', None: '모름'}
SIZE_KO = {'Small': '소형', 'Medium': '중형', 'Large': '대형', None: '모름'}
SOURCE_OFFICIAL, SOURCE_RATHENA = '공식', 'rAthena'
OFFICIAL_NOTE = '공식 홈페이지 몬스터 정보·속성표 기준 (홈페이지는 업데이트에 따라 다를 수 있음)'
MIN_COVERAGE = 0.5          # 관측한 몬스터(마리 수) 중 자료가 있는 비율이 이보다 낮으면 추천하지 않는다


def _yaml_body(path):
    import yaml
    with open(path, encoding='utf-8') as f:
        return (yaml.safe_load(f) or {}).get('Body') or []


def build_mob_index(yml_path):
    """mob_db.yml → {class_id: [이름, 레벨, 크기, 종족, 속성, 속성레벨, HP, AegisName]} (필요한 칸만)."""
    out = {}
    for m in _yaml_body(yml_path):
        out[str(m['Id'])] = [m.get('Name') or m.get('AegisName'), m.get('Level', 1), m.get('Size', 'Small'),
                             m.get('Race', 'Formless'), m.get('Element', 'Neutral'), m.get('ElementLevel', 1),
                             m.get('Hp', 1), m.get('AegisName')]
    return out


def load_attr_table(yml_path):
    """rAthena attr_fix.yml → table[방어 속성 레벨][공격 속성][방어 속성] = % (없는 칸은 100)."""
    table = {}
    for row in _yaml_body(yml_path):
        lv = table.setdefault(row['Level'], {})
        for atk in ELEMENTS:
            lv[atk] = {d: (row.get(atk) or {}).get(d, 100) for d in ELEMENTS}
    return table


def _norm(name):
    return (name or '').replace(' ', '')


class ElementAdvisor:
    def __init__(self, data_dir=RATHENA_DIR, official_dir=OFFICIAL_DIR):
        self.data_dir, self.official_dir = data_dir, official_dir
        self._mobs = self._attr = self._official = None
        self.attr_source = None

    # ------------------------------------------------------------ 자료 읽기
    @property
    def mobs(self):
        if self._mobs is None:
            self._mobs = self._load_mobs()
        return self._mobs

    @property
    def official(self):
        """공식 몬스터: by_aegis, by_name(공백 무시), by_map(출현 지역 이름)."""
        if self._official is None:
            by_aegis, by_name, by_map, map_names = {}, {}, {}, {}
            try:
                with open(os.path.join(self.official_dir, 'monsters.json'), encoding='utf-8') as f:
                    for m in json.load(f)['monsters']:
                        by_aegis[m['aegis']] = m
                        by_name.setdefault(_norm(m['name']), []).append(m)
                        for mp in dict.fromkeys(m.get('maps') or []):
                            by_map.setdefault(_norm(mp), []).append(m)
                            map_names[_norm(mp)] = mp
            except FileNotFoundError:
                pass
            except Exception:
                log.exception('공식 몬스터 자료 읽기 실패')
            self._official = dict(by_aegis=by_aegis, by_name=by_name, by_map=by_map, map_names=map_names)
        return self._official

    @property
    def attr(self):
        if self._attr is None:
            self._attr = {}
            try:
                with open(os.path.join(self.official_dir, 'attr_table.json'), encoding='utf-8') as f:
                    self._attr = {int(lv): t for lv, t in json.load(f)['table'].items()}
                    self.attr_source = SOURCE_OFFICIAL
            except FileNotFoundError:
                try:
                    self._attr = load_attr_table(os.path.join(self.data_dir, 'attr_fix.yml'))
                    self.attr_source = SOURCE_RATHENA
                except Exception:
                    log.exception('attr_fix.yml 읽기 실패')
            except Exception:
                log.exception('공식 속성표 읽기 실패')
        return self._attr

    def _load_mobs(self):
        """큰 YAML(약 2MB)은 한 번만 읽고 mob_index.json 으로 저장해 다음부터 빠르게 쓴다."""
        yml = os.path.join(self.data_dir, 'mob_db.yml')
        idx = os.path.join(self.data_dir, 'mob_index.json')
        try:
            if os.path.isfile(idx) and (not os.path.isfile(yml) or os.path.getmtime(idx) >= os.path.getmtime(yml)):
                with open(idx, encoding='utf-8') as f:
                    return json.load(f)
            if not os.path.isfile(yml):
                return {}
            mobs = build_mob_index(yml)
            with open(idx, 'w', encoding='utf-8') as f:
                json.dump(mobs, f, ensure_ascii=False, separators=(',', ':'))
            return mobs
        except Exception:
            log.exception('몬스터 속성 자료 읽기 실패')
            return {}

    # ------------------------------------------------------------ 조회
    def info(self, class_id, name=None):
        """몬스터 하나의 속성 정보. 공식(Aegis 이름 → 한글 이름) → rAthena 순서로 찾는다."""
        row = self.mobs.get(str(class_id))
        aegis = row[7] if row and len(row) > 7 else (class_id if isinstance(class_id, str) else None)
        off = self.official['by_aegis'].get(aegis) if aegis else None
        if off is None and name:
            same = self.official['by_name'].get(_norm(name)) or []
            # 이름이 같은 몬스터가 여럿이면 속성·레벨이 모두 같을 때만 쓴다
            if same and len({(m['element'], m['element_level']) for m in same}) == 1:
                off = same[0]
        if off is not None:
            # 홈페이지 표에서 칸이 밀려 크기·종족을 모르는 몬스터는 rAthena 값으로 채운다(속성은 공식 그대로)
            return dict(name=off['name'], level=off.get('level'), size=off.get('size') or (row[2] if row else None),
                        race=off.get('race') or (row[3] if row else None), element=off['element'],
                        element_level=off['element_level'], hp=off.get('hp'), source=SOURCE_OFFICIAL)
        if row:
            nm, level, size, race, element, elv, hp = row[:7]
            return dict(name=nm, level=level, size=size, race=race, element=element, element_level=elv, hp=hp,
                        source=SOURCE_RATHENA)
        return None

    def modifier(self, atk, element, level):
        return self.attr.get(level, {}).get(atk, {}).get(element, 100)

    def map_monsters(self, map_name, include_boss=False):
        """공식 출현 지역 이름으로 그 맵의 몬스터 목록(각 1마리 가중치)."""
        rows = self.official['by_map'].get(_norm(map_name)) or []
        seen = {}
        for m in rows:
            if include_boss or '보스' not in (m.get('grade') or ''):
                seen.setdefault(m['aegis'], dict(class_id=m['aegis'], name=m['name'], weight=1))
        return list(seen.values())

    def recommend(self, monsters):
        """monsters: [{class_id, name, weight}] — weight 는 관측 마리 수 등.

        반환: 공격 속성별 평균 배율(가중 평균)·최저 배율, 몬스터 속성·종족·크기 분포, 자료에 없는 몬스터.
        """
        attr = self.attr                     # 먼저 읽어야 attr_source 가 정해진다
        known, unknown = [], []
        for m in monsters:
            if not m.get('weight'):
                continue
            inf = self.info(m['class_id'], m.get('name'))
            (known if inf else unknown).append(dict(m, info=inf))
        total = sum(m['weight'] for m in known)
        seen = total + sum(m['weight'] for m in unknown)
        coverage = total / seen if seen else 0
        sources = Counter(m['info']['source'] for m in known)
        base = dict(known=known, unknown=unknown, coverage=coverage, sources=sources, attr_source=self.attr_source)
        if not total or not attr or coverage < MIN_COVERAGE:
            return dict(base, available=False, elements=[], defense=[], races=[], sizes=[])
        elements = []
        for atk in ELEMENTS:
            mods = [(self.modifier(atk, m['info']['element'], m['info']['element_level']), m['weight']) for m in known]
            elements.append(dict(element=atk, avg=sum(p * w for p, w in mods) / total, worst=min(p for p, _ in mods)))
        elements.sort(key=lambda r: (-r['avg'], -r['worst'], ELEMENTS.index(r['element'])))

        def dist(key):
            acc = {}
            for m in known:
                acc[key(m['info'])] = acc.get(key(m['info']), 0) + m['weight']
            return sorted(((k, v / total) for k, v in acc.items()), key=lambda kv: -kv[1])
        return dict(base, available=True, elements=elements,
                    defense=dist(lambda i: (i['element'], i['element_level'])),
                    races=dist(lambda i: i['race']), sizes=dist(lambda i: i['size']))


_shared = None


def shared():
    """화면 여러 곳에서 같은 자료를 한 번만 읽도록 공유한다."""
    global _shared
    if _shared is None:
        _shared = ElementAdvisor()
    return _shared


def traits_text(i):
    """'성4 · 천사형 · 소형' (자료 없으면 '자료 없음')."""
    if not i:
        return '자료 없음'
    return '%s%d · %s · %s%s' % (ELEMENT_KO[i['element']], i['element_level'], RACE_KO.get(i['race'], i['race']),
                                 SIZE_KO.get(i['size'], i['size']), '' if i['source'] == SOURCE_OFFICIAL else ' (rAthena 추정)')


def source_note(rec):
    parts = [OFFICIAL_NOTE if rec.get('attr_source') == SOURCE_OFFICIAL else 'rAthena 속성표 기준 추정']
    if rec['sources'].get(SOURCE_RATHENA):
        parts.append('공식 목록에 없는 %d종은 rAthena 자료로 추정' % rec['sources'][SOURCE_RATHENA])
    return ' · '.join(parts)


def summary_text(rec, top=3, basis='관측'):
    """화면용 여러 줄 요약. 추천이 불가능하면 이유를 적는다. basis: '관측' 또는 '공식 출현 목록'."""
    names = list(dict.fromkeys(m.get('name') or '#%s' % m['class_id'] for m in rec['unknown']))
    missing = ', '.join(names[:5]) + (' 외 %d종' % (len(names) - 5) if len(names) > 5 else '')
    if not rec['available']:
        if rec['unknown']:
            return ('추천 속성: 계산하지 않음 — 몬스터 대부분이 속성 자료에 없음 (자료 있는 비율 %d%%)\n'
                    '자료 없음: %s\n공식 홈페이지 목록에도 없는 몬스터입니다') % (round(rec['coverage'] * 100), missing)
        return '추천 속성: %s한 몬스터 없음' % ('관측' if basis == '관측' else '공식 출현 목록에 등록')
    els = rec['elements']
    best = [r for r in els if r['avg'] > 100][:top] or els[:1]
    bad = [r for r in els if r['avg'] < 100]
    pct = lambda r: '%s %d%%' % (ELEMENT_KO[r['element']], round(r['avg']))  # noqa: E731
    share = lambda items, names: ', '.join('%s %d%%' % (names(k), round(v * 100)) for k, v in items[:4])  # noqa: E731
    lines = ['추천 공격 속성: ' + ' · '.join(pct(r) for r in best) +
             ('   |   피할 속성: ' + ' · '.join(pct(r) for r in bad[-3:][::-1]) if bad else ''),
             '몬스터 속성: ' + share(rec['defense'], lambda k: '%s%d' % (ELEMENT_KO.get(k[0], k[0]), k[1])) +
             '   |   종족: ' + share(rec['races'], lambda k: RACE_KO.get(k, k)) +
             '   |   크기: ' + share(rec['sizes'], lambda k: SIZE_KO.get(k, k))]
    tail = '%s %d종 %s · %s' % (basis, len(rec['known']), '마리 수 가중 평균' if basis == '관측' else '평균(보스 제외)',
                               source_note(rec))
    if rec['unknown']:
        tail += ' · 자료 없어 제외(%d%%): %s' % (round((1 - rec['coverage']) * 100), missing)
    return '\n'.join(lines + [tail])
