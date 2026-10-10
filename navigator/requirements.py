"""Requirement Engine: PASS / FAIL / UNKNOWN. 확인할 수 없는 것은 실패로 치지 않는다."""
from dataclasses import dataclass

from navigator.models import FAIL, PASS, Q_COMPLETED, Q_NOT_COMPLETED, UNKNOWN


@dataclass
class ReqResult:
    requirement: object
    result: str
    message: str
    have: object = None
    need: object = None
    link_guide: str = ''          # 해결용 Guide (선행 퀘스트 등)


def evaluate(req, st, repo=None):
    k = req.kind
    if k == 'LEVEL':
        return _range(req, st.level, '베이스 레벨')
    if k == 'JOB_LEVEL':
        r = _range(req, st.job_level, '잡 레벨')
        if r.result != UNKNOWN:
            r.message += ' (잡 레벨은 Replay 메타데이터 값 — 게임에서 한 번 확인하세요)'
        return r
    if k == 'JOB':
        if st.job_id is None:
            return ReqResult(req, UNKNOWN, '현재 직업을 Replay에서 확인하지 못함')
        ok = st.job_id in req.jobs
        return ReqResult(req, PASS if ok else FAIL,
                         '현재 직업 %s — %s' % (st.job_name or st.job_id, '조건 충족' if ok else req.description),
                         st.job_id, req.jobs)
    if k == 'ITEM':
        if not (st.inventory_known or st.storage_known or st.cart_known):
            return ReqResult(req, UNKNOWN, '인벤토리/창고 정보가 없음 (Replay에서 창고를 열어야 기록됨)',
                             None, req.quantity)
        have = st.holding(req.item_id).total if req.item_id else 0
        ok = have >= req.quantity
        missing = [] if st.storage_known else ['창고']
        note = ' (%s 미확인)' % ', '.join(missing) if missing else ''
        return ReqResult(req, PASS if ok else FAIL,
                         '%s %s/%s%s' % (req.item_name, '{:,}'.format(have), '{:,}'.format(req.quantity), note),
                         have, req.quantity)
    if k == 'ZENY':
        if req.min is None:
            return ReqResult(req, UNKNOWN, req.description or '금액 미확인')
        if st.zeny is None:
            return ReqResult(req, UNKNOWN, 'Zeny 정보 없음', None, req.min)
        ok = st.zeny >= req.min
        return ReqResult(req, PASS if ok else FAIL, '보유 %s / 필요 %s Z' % ('{:,}'.format(st.zeny),
                                                                         '{:,}'.format(req.min)), st.zeny, req.min)
    if k in ('QUEST', 'EPISODE'):
        state = st.quest_states.get(req.guide_id) if req.guide_id else None
        if state is None and repo is not None and req.guide_id:
            state = repo.quest_state(st.character_name, req.guide_id)
        if state == Q_COMPLETED:
            return ReqResult(req, PASS, '완료 확인 — ' + req.description, link_guide=req.guide_id)
        if state == Q_NOT_COMPLETED:
            return ReqResult(req, FAIL, '미완료 확인 — ' + req.description, link_guide=req.guide_id)
        return ReqResult(req, UNKNOWN, '완료 여부를 Replay로 확인할 수 없음 — ' + req.description,
                         link_guide=req.guide_id)
    return ReqResult(req, UNKNOWN, (req.description or k) + ' (자동 확인 불가)')


def _range(req, value, label):
    if value is None:
        return ReqResult(req, UNKNOWN, '%s를 Replay에서 확인하지 못함' % label, None, req.min)
    if req.min is not None and value < req.min:
        return ReqResult(req, FAIL, '%s %d < 필요 %d' % (label, value, req.min), value, req.min)
    if req.max is not None and value > req.max:
        return ReqResult(req, FAIL, '%s %d > 최대 %d' % (label, value, req.max), value, req.max)
    return ReqResult(req, PASS, '%s %d (필요 %s)' % (label, value, req.min if req.min is not None else '-'),
                     value, req.min)


def readiness(results):
    """UNKNOWN은 분모에서 제외하고 '확인 필요'로 따로 센다."""
    required = [r for r in results if not r.requirement.optional]
    p = sum(1 for r in required if r.result == PASS)
    f = sum(1 for r in required if r.result == FAIL)
    u = sum(1 for r in required if r.result == UNKNOWN)
    return dict(percent=round(p * 100 / (p + f)) if p + f else None, passed=p, failed=f, unknown=u)


def diagnose(results, repo=None):
    """'왜 못 들어가지?' 결론 문장 목록."""
    fails = [r for r in results if r.result == FAIL and not r.requirement.optional]
    unknowns = [r for r in results if r.result == UNKNOWN and not r.requirement.optional]
    out = []
    for r in fails:
        out.append('❌ %s 조건이 충족되지 않은 것으로 확인됩니다: %s' % (_label(r), r.message))
    for r in unknowns:
        out.append('? %s: %s' % (_label(r), r.message))
    if not fails and not unknowns:
        out.append('✅ 확인 가능한 조건은 모두 충족합니다. 그래도 입장이 안 되면 Guide에 없는 조건(파티, 재입장 대기 등)이나 '
                   '업데이트로 바뀐 조건일 수 있습니다.')
    elif not fails:
        out.append('확인된 실패 조건은 없습니다. 위 "확인 필요" 항목을 게임에서 확인하세요.')
    return out


KIND_LABEL = {'LEVEL': '레벨', 'JOB_LEVEL': '잡 레벨', 'JOB': '직업', 'QUEST': '선행 퀘스트', 'ITEM': '필요 아이템',
              'ZENY': '제니', 'EPISODE': '에피소드', 'COOLDOWN': '재입장 대기', 'PARTY': '파티', 'EQUIPMENT': '장비',
              'STATUS': '상태', 'OTHER': '기타'}


def _label(r):
    return KIND_LABEL.get(r.requirement.kind, r.requirement.kind)
