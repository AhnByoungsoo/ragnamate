"""시간축 데이터: Rolling DPS, 이동, 타임라인 표."""
import math

from analyzer.events import EventType as E

TIMELINE_TYPES = {
    E.SESSION_START: 'Replay 시작', E.SESSION_END: 'Replay 종료', E.MAP_CHANGE: '맵 이동',
    E.BUFF_START: '버프 시작', E.BUFF_END: '버프 종료', E.SKILL_CAST_START: '캐스팅 시작', E.SKILL_USE: '스킬 사용',
    E.DAMAGE: '피해', E.MONSTER_DEATH: '몬스터 처치', E.ZENY_CHANGE: 'Zeny 변경', E.ITEM_GAIN: '아이템 획득',
    E.ITEM_REMOVE: '아이템 소모', E.BASE_EXP_GAIN: 'Base EXP', E.JOB_EXP_GAIN: 'Job EXP',
    E.INVENTORY_UPDATE: '인벤토리 목록', E.CART_UPDATE: '카트 목록', E.STORAGE_UPDATE: '창고 목록',
    E.MONSTER_SPAWN: '몬스터 등장', E.ACTION: '동작', E.PLAYER_MOVE: '이동',
}


def rolling_dps(points, start_ms, end_ms, bucket_s):
    """[(t, amount)] → [(bucket_start_ms, dps)] (bucket_s 초 단위)."""
    if end_ms <= start_ms:
        return []
    n = max(1, math.ceil((end_ms - start_ms) / (bucket_s * 1000)))
    buckets = [0] * n
    for t, a in points:
        i = int((t - start_ms) // (bucket_s * 1000))
        if 0 <= i < n:
            buckets[i] += a
    return [(start_ms + i * bucket_s * 1000, v / bucket_s) for i, v in enumerate(buckets)]


def movement(ctx):
    pts = [(e.timestamp_ms, e.x, e.y) for e in ctx.of(E.PLAYER_MOVE) if e.x is not None]
    dist = 0.0
    for (t1, x1, y1), (t2, x2, y2) in zip(pts, pts[1:]):
        dist += math.hypot(x2 - x1, y2 - y1)
    idle = sum(max(0, b[0] - a[0] - 10_000) for a, b in zip(pts, pts[1:]))
    spawns = [dict(t=e.timestamp_ms, class_id=e.data.get('class_id'), x=e.x, y=e.y)
              for e in ctx.of(E.MONSTER_SPAWN) if e.x is not None]
    return dict(points=pts, distance_cells=dist, idle_ms=idle, monster_spawns=spawns,
                note='이동거리는 이동 목적지 사이 직선거리 합(○)')


def timeline_rows(ctx, describe):
    """[(t_ms, 분류, 설명)] — describe(event)→str"""
    out = []
    for e in ctx.events:
        label = TIMELINE_TYPES.get(e.event_type)
        if label:
            out.append((e.timestamp_ms, label, describe(e), e.confidence, e.raw_packet))
    return out
