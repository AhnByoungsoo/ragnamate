"""ExperienceAnalyzer / ZenyAnalyzer."""
from analyzer.analyzers.context import per_hour
from analyzer.events import CALC, MEASURED, EventType as E


def analyze_exp(ctx):
    base = [e for e in ctx.of(E.BASE_EXP_GAIN)]
    job = [e for e in ctx.of(E.JOB_EXP_GAIN)]
    if not base and not job:
        return dict(available=False, note='EXP 획득 패킷이 샘플에서 검증되지 않아 미지원 '
                                          '(설정 > 실험적 패킷 해석을 켜면 후보 정의로 시도)',
                    base_total=None, job_total=None, base_per_hour=None, job_per_hour=None, by_map={},
                    series=[])
    bt = sum(e.data['amount'] for e in base)
    jt = sum(e.data['amount'] for e in job)
    by_map = {}
    for e in base + job:
        m = by_map.setdefault(e.map_name or '?', dict(base=0, job=0))
        m['base' if e.event_type == E.BASE_EXP_GAIN else 'job'] += e.data['amount']
    series, cb, cj = [], 0, 0
    for e in sorted(base + job, key=lambda e: e.timestamp_ms):
        if e.event_type == E.BASE_EXP_GAIN:
            cb += e.data['amount']
        else:
            cj += e.data['amount']
        series.append((e.timestamp_ms, cb, cj))
    conf = '△' if any(e.confidence != MEASURED for e in base + job) else MEASURED
    return dict(available=True, note='미검증 후보 패킷 기반(△)' if conf == '△' else '',
                base_total=bt, job_total=jt, base_per_hour=per_hour(bt, ctx.duration_ms),
                job_per_hour=per_hour(jt, ctx.duration_ms), by_map=by_map, series=series, conf=conf)


def analyze_zeny(ctx):
    vals = [(e.timestamp_ms, e.data['value']) for e in ctx.pr.events if e.event_type == E.ZENY_CHANGE]
    before = [v for t, v in vals if t < ctx.start_ms]
    inside = [(t, v) for t, v in vals if ctx.start_ms <= t <= ctx.end_ms]
    start = before[-1] if before else ctx.pr.start_zeny   # 메타데이터(Session)의 녹화 시작 Zeny
    if start is None:
        return dict(available=False, note='Zeny 정보 없음')
    gained = spent = 0
    last = start
    series = [(ctx.start_ms, start)]
    for t, v in inside:
        d = v - last
        if d > 0:
            gained += d
        else:
            spent += -d
        last = v
        series.append((t, v))
    return dict(available=True, note='', start=start, end=last, net=last - start, gained=gained, spent=spent,
                net_per_hour=per_hour(last - start, ctx.duration_ms), series=series,
                conf=dict(start=MEASURED, end=MEASURED, net=CALC, gained=CALC, spent=CALC),
                split_note='증가/감소는 Zeny 변경 패킷 간 차이로 계산(판매·구매·드랍 구분 불가)')
