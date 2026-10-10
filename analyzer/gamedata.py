"""외부 게임 데이터(이름 lookup). Replay 데이터와 분리한다.

ItemDB    : 클라이언트 itemInfo_true.lub (core.items 재사용)
MapDB     : 클라이언트 System/mapInfo_true.lub 의 displayName
MonsterDB : Replay 유닛 패킷(0x09ff 등)에 실린 몬스터 이름을 누적 저장(SQLite)
SkillDB/BuffDB : data/skill_names.json, data/buff_names.json (사용자/가져오기로 채움)
             data.grf가 암호화 포맷이라 클라이언트에서 직접 읽지 않는다.
JobDB     : 직업 ID 표
"""
import json
import logging
import os
import re
import ast
import operator

import iteminfo
from core.identifiers import numeric_id

log = logging.getLogger(__name__)

JOB_NAMES = {
    0: '초보자', 1: '검사', 2: '매지션', 3: '궁수', 4: '복사', 5: '상인', 6: '도둑', 7: '기사', 8: '프리스트',
    9: '위저드', 10: '제철공', 11: '헌터', 12: '어새신', 14: '크루세이더', 15: '몽크', 16: '세이지', 17: '로그',
    18: '연금술사', 19: '바드', 20: '댄서', 23: '슈퍼노비스', 24: '건슬링거', 25: '닌자',
    4054: '룬나이트', 4055: '워록', 4056: '레인저', 4057: '아크비숍', 4058: '미케닉', 4059: '길로틴크로스',
    4060: '룬나이트(전승)', 4061: '워록(전승)', 4062: '레인저(전승)', 4063: '아크비숍(전승)', 4064: '미케닉(전승)',
    4065: '길로틴크로스(전승)', 4066: '로열가드', 4067: '소서러', 4068: '민스트럴', 4069: '원더러', 4070: '슈라',
    4071: '제네릭', 4072: '쉐도우체이서', 4073: '로열가드(전승)', 4074: '소서러(전승)', 4075: '민스트럴(전승)',
    4076: '원더러(전승)', 4077: '슈라(전승)', 4078: '제네릭(전승)', 4079: '쉐도우체이서(전승)',
    4252: '드래곤나이트', 4253: '마이스터', 4254: '쉐도우크로스', 4255: '아크메이지', 4256: '카디널',
    4257: '윈드호크', 4258: '임페리얼가드', 4259: '바이오로', 4260: '어비스체이서', 4261: '엘레멘탈마스터',
    4262: '인퀴지터', 4263: '트루바두르', 4264: '트루베르',
    4302: '천제', 4303: '영도사', 4304: '신키로', 4305: '시라누이', 4306: '나이트와치', 4307: '하이퍼노비스',
    4308: '스피릿핸들러',
}
_COLOR = re.compile(r'\^[0-9a-fA-F]{6}')


EFST_ENUM = re.compile(r'enum\s+efst_type\s*:?[^{]*\{(.*?)\n\};', re.S)
EFST_ITEM = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*(.+))?$')
NAME_PRIORITY = {'user': 100, 'official_kro': 80, 'client_kro': 70,
                 'kro_reference': 65, 'client_translation': 50}


def parse_efst_enum(text):
    """rAthena status.hpp의 efst_type enum → {id: EFST_NAME}. C enum 규칙(암시적 증가)을 그대로 따른다."""
    m = EFST_ENUM.search(text)
    if not m:
        return {}
    body = re.sub(r'/\*.*?\*/|//[^\n]*', '', m.group(1), flags=re.S)
    if re.search(r'^\s*#', body, re.M):
        raise ValueError('조건부 enum은 활성 분기를 확인한 뒤 가져와야 합니다')
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.BitOr: operator.or_,
           ast.BitAnd: operator.and_, ast.LShift: operator.lshift, ast.RShift: operator.rshift}
    value, out = 0, {}

    def constant(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in out:
            return out[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return (-1 if isinstance(node.op, ast.USub) else 1) * constant(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            right = constant(node.right)
            if isinstance(node.op, (ast.LShift, ast.RShift)) and not 0 <= right <= 31:
                raise ValueError('enum shift 범위 오류')
            return ops[type(node.op)](constant(node.left), right)
        raise ValueError('확인할 수 없는 EFST 상수')

    for line in body.split(','):
        line = line.strip()
        if not line:
            continue
        item = EFST_ITEM.match(line)
        if not item:
            raise ValueError('EFST enum 구문을 확인할 수 없습니다: %s' % line)
        name, expr = item.group(1), item.group(2)
        if expr:
            expr = expr.strip()
            value = constant(ast.parse(expr, mode='eval').body)
        out[name] = value
        value += 1
    return {v: k for k, v in out.items()}


class _NameFile:
    """{id: name} JSON. 값이 {"name":.., "src":..}이면 출처(사용자 확인/외부 목록)를 함께 둔다."""

    def __init__(self, path, label, fallback_path=None):
        self.path, self.label, self.names, self.srcs = path, label, {}, {}
        self.details = {}
        self.fallback_path = fallback_path
        if fallback_path:
            for source_path in ([fallback_path] if isinstance(fallback_path, (str, os.PathLike)) else fallback_path):
                self._load(source_path)
        self._defaults = (dict(self.names), dict(self.srcs), dict(self.details))
        self._load(path)

    def _load(self, path):
        try:
            if os.path.exists(path):
                with open(path, encoding='utf-8-sig') as f:
                    for k, v in json.load(f).items():
                        key = numeric_id(k)
                        if key is None:
                            continue
                        if isinstance(v, dict):
                            name, src = v.get('name'), v.get('src') or 'import'
                            detail = {x: v[x] for x in ('url', 'checked_at', 'symbol') if isinstance(v.get(x), str)}
                        elif isinstance(v, str):
                            name, src, detail = v, 'user', {}
                        else:
                            continue
                        if NAME_PRIORITY.get(self.srcs.get(key), 10) > NAME_PRIORITY.get(src, 10):
                            continue
                        if isinstance(name, str) and name.strip():
                            if self.names.get(key) == name.strip() and 'symbol' not in detail:
                                symbol = self.details.get(key, {}).get('symbol')
                                if symbol:
                                    detail['symbol'] = symbol
                            self.names[key], self.srcs[key] = name.strip(), src
                            self.details[key] = detail
        except Exception:
            log.exception('%s 이름 파일 읽기 실패: %s', self.label, path)

    def name(self, i):
        return self.names.get(numeric_id(i)) or '%s #%s' % (self.label, i)

    def origin(self, i):
        """'user'(직접 확인) | 'rathena' 등 외부 목록 | '' (이름 없음)"""
        i = numeric_id(i)
        return self.srcs.get(i, '') if i in self.names else ''

    def metadata(self, i):
        return dict(self.details.get(numeric_id(i), {}))

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        out = {}
        for k in sorted(self.names):
            src = self.srcs.get(k, 'import')
            if (self.names[k] == self._defaults[0].get(k) and src == self._defaults[1].get(k)
                    and self.details.get(k, {}) == self._defaults[2].get(k, {})):
                continue
            out[str(k)] = self.names[k] if src == 'user' else dict(name=self.names[k], src=src, **self.details.get(k, {}))
        with open(self.path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(self.path + '.tmp', self.path)

    def import_efst(self, text, src='rathena'):
        """외부 EFST 목록 가져오기. 사용자가 직접 지정한 이름은 덮어쓰지 않고, 출처를 남겨 '확인 필요'로 표시한다."""
        added = 0
        for i, name in parse_efst_enum(text).items():
            if i < 0 or name == 'EFST_MAX':
                continue
            if NAME_PRIORITY.get(self.srcs.get(i), 10) >= 50:
                continue
            short = name[5:] if name.startswith('EFST_') else name
            if self.names.get(i) != short:
                self.names[i], self.srcs[i] = short, src
                self.details[i] = {}
                added += 1
        self.save()
        return added

    def set(self, i, text):
        """사용자가 직접 확인한 이름을 저장(빈 값이면 지운다)."""
        text = (text or '').strip()
        i = numeric_id(i)
        if i is None:
            raise ValueError('유효한 숫자 ID가 필요합니다')
        if text:
            self.names[i], self.srcs[i] = text, 'user'
            self.details[i] = {}
        else:
            for current, default in zip((self.names, self.srcs, self.details), self._defaults):
                current.pop(i, None)
                if i in default:
                    current[i] = default[i]
        self.save()
        return text

    def import_file(self, path):
        """이름 파일 가져오기. .json은 {"id": "이름"}, 그 밖에는 rAthena yml 형식으로 읽는다."""
        if path.lower().endswith('.json'):
            with open(path, encoding='utf-8-sig') as f:
                data = json.load(f)
            added = 0
            for k, v in (data.get('names', data) if isinstance(data, dict) else {}).items():
                key = numeric_id(k)
                if key is None:
                    continue
                name = v.get('name') if isinstance(v, dict) else v
                if isinstance(name, str) and name.strip() and key not in self.names:
                    self.names[key], self.srcs[key] = name.strip(), 'import'
                    self.details[key] = {x: v[x] for x in ('url', 'checked_at') if isinstance(v, dict) and isinstance(v.get(x), str)}
                    added += 1
            self.save()
            return added
        return self.import_rathena_yml(path)

    def import_rathena_yml(self, path):
        """rAthena skill_db.yml / status 등의 'Id: n' + 'Description: 이름' 블록을 가져온다."""
        added, cur = 0, None
        if self.label != '스킬':
            raise ValueError('스킬 Id와 버프 EFST는 다른 번호입니다. 버프는 EFST 목록을 가져오세요.')
        with open(path, encoding='utf-8-sig') as f:
            for line in f:
                m = re.match(r'\s*-\s*Id:\s*(\d+)', line)
                if m:
                    cur = int(m.group(1))
                    continue
                m = re.match(r'\s*Description:\s*(.+)', line)
                if m and cur is not None:
                    if cur not in self.names:
                        self.names[cur] = m.group(1).strip().strip('"\'')
                        self.srcs[cur] = 'rathena'
                        added += 1
                    cur = None
        self.save()
        return added


class MapDB:
    def __init__(self, ro_dir):
        self.names = {}
        path = os.path.join(ro_dir, 'System', 'mapInfo_true.lub')
        try:
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    tbl = iteminfo._run(*iteminfo._function(iteminfo._Reader(f.read())))['mapTbl']
                self.names = {k[:-4] if k.endswith('.rsw') else k: _COLOR.sub('', v.get('displayName') or '')
                              for k, v in tbl.items() if isinstance(v, dict)}
        except Exception:
            log.exception('mapInfo 읽기 실패')

    def name(self, map_id):
        if not map_id:
            return '(알 수 없음)'
        disp = self.names.get(map_id)
        return '%s (%s)' % (disp, map_id) if disp else map_id


class MonsterDB:
    """몬스터 이름: Replay에서 관측된 (class_id → name)을 DB에 누적."""

    def __init__(self, db=None):
        self.db, self.names = db, {}
        if db:
            try:
                self.names = dict(db._query('SELECT class_id, name FROM monster_names'))
            except Exception:
                log.exception('monster_names 읽기 실패')

    def observe(self, mapping):
        new = {k: v for k, v in mapping.items() if v and self.names.get(k) != v}
        if new:
            self.names.update(new)
            if self.db:
                try:
                    self.db._exec('INSERT OR REPLACE INTO monster_names VALUES (?,?)', list(new.items()), many=True)
                except Exception:
                    log.exception('monster_names 저장 실패')

    def name(self, class_id):
        return self.names.get(class_id) or '몬스터 #%d' % class_id


class GameDataProvider:
    def quest_title(self, quest_id):
        """퀘스트 번호 → 게임 클라이언트 퀘스트 목록(OngoingQuestInfoList)의 제목. 없으면 None."""
        if self._quests is None:
            try:
                from navigator.quest_info import QuestInfoDB
                self._quests = QuestInfoDB(self.ro_dir).load().quests or {}
            except Exception:
                log.exception('퀘스트 목록 읽기 실패')
                self._quests = {}
        info = self._quests.get(quest_id)
        return info.title if info else None

    def __init__(self, item_db, ro_dir, data_dir, db=None):
        self.ro_dir, self._quests = ro_dir, None
        self.items = item_db
        self.maps = MapDB(ro_dir)
        self.monsters = MonsterDB(db)
        self.skills = _NameFile(os.path.join(data_dir, 'skill_names.json'), '스킬',
                               [os.path.join(data_dir, 'skill_catalog.json'),
                                os.path.join(data_dir, 'skill_names_official.json')])
        self.buffs = _NameFile(os.path.join(data_dir, 'buff_names.json'), '버프',
                              os.path.join(data_dir, 'buff_catalog.json'))
        self.buff_links = {}
        try:
            with open(os.path.join(data_dir, 'buff_skill_reference.json'), encoding='utf-8') as f:
                self.buff_links = json.load(f)
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            log.exception('버프-스킬 참고 연결 읽기 실패')

    @staticmethod
    def job_name(job_id):
        if job_id is None:
            return '-'
        return JOB_NAMES.get(job_id, '직업 #%d' % job_id)
