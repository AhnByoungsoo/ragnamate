"""HUD에 무엇을 어떤 색으로 보여줄지 결정하는 부분 (Qt 없이 테스트 가능).

원칙은 그대로다: 값을 모르면 줄을 만들지 않는다. ○ = 화면, ● = Replay.
"""
from core.fmt import hms_sec, mmss, unit

MODE_LABEL = {'WAITING': 'WAITING', 'SCREEN': 'LIVE ● SCREEN', 'HYBRID': 'LIVE ● HYBRID', 'REPLAY': 'LIVE ● REPLAY',
              'PAUSED': 'PAUSED'}
PAGE_MODE_LABEL = dict(MODE_LABEL, WAITING='WAITING FOR RAGNAROK')      # Live 탭의 큰 표시
ITEMS = [('time', 'Time'), ('exp', 'EXP/h'), ('profit_h', 'Profit/h'), ('kills', 'Kill/h'), ('dps', 'DPS'),
         ('session', 'Session'), ('drops', 'Top Drops'), ('hp', 'HP'), ('sp', 'SP'), ('ap', 'AP'), ('wt', 'WT'),
         ('buffs', 'Buff')]
ITEM_LABEL = dict(ITEMS)
PRESETS = {
    'compact': {'exp', 'profit_h', 'kills', 'dps'},
    'hunt': {'time', 'exp', 'profit_h', 'kills', 'dps', 'session', 'drops', 'buffs'},
    'status': {'hp', 'sp', 'ap', 'wt', 'buffs'},
    'detailed': {k for k, _ in ITEMS},
}
DANGER, WARN, OK = '#ff6b6b', '#ffd166', None
BUFF_COLOR = {'WARN': WARN, 'DANGER': DANGER, 'EXPIRED': '#7a7a7a'}


def short(v, digits=1):
    """HUD용 짧은 숫자. 값이 없으면 '-'가 아니라 None을 돌려줘 줄 자체를 만들지 않는다."""
    return None if v is None else unit(v, digits)


def visible_items(settings):
    """프리셋 기본값 + 사용자가 HUD 메뉴에서 켜고 끈 항목(hud_items)."""
    keys = set(PRESETS.get(settings.get('hud_preset', 'hunt'), PRESETS['hunt']))
    for key, on in (settings.get('hud_items') or {}).items():
        keys.add(key) if on else keys.discard(key)
    return keys


def gauge_color(pct, warn, danger, high_is_bad=False):
    if pct is None:
        return OK
    if high_is_bad:
        return DANGER if pct >= danger else (WARN if pct >= warn else OK)
    return DANGER if pct <= danger else (WARN if pct <= warn else OK)


def build_rows(snap, settings, buff_rows=()):
    """(title, rows) — rows는 {label, value, src, color}. 값이 없으면 줄을 넣지 않는다."""
    s = snap.get('session') or {}
    vals = dict(snap.get('values', {}))
    rp = s.get('replay') or {}
    keys = visible_items(settings)
    warn, danger = settings.get('hud_warn_pct', 30), settings.get('hud_danger_pct', 10)
    weight_warn = settings.get('alert_weight', 85)
    rows = []

    def row(key, value, src, color=OK, label=None):
        if value is not None and key in keys:
            rows.append(dict(label=label or ITEM_LABEL.get(key, key), value=str(value), src=src, color=color))

    replay = snap.get('replay_live')
    if replay and replay.get('metrics'):
        title = '%s · %s · %s' % (replay['stage'], replay.get('map') or '?', replay['quality'])
        row('dps', short((replay.get('rolling_dps') or {}).get('1')), '●', label='DPS 1s')
        row('dps', short(replay.get('combat_dps')), '●', label='Combat DPS')
        if replay.get('combat_available'):
            row('kills', short((replay.get('metrics') or {}).get('kills')), '●', label='Kills')
        for key, value in (replay.get('resources') or {}).items():
            if value.get('value') is not None:
                pct = value['value'] * 100 / value['max_value'] if value.get('max_value') else None
                row(key.lower(), '%s / %s' % (short(value['value']), short(value.get('max_value')) or '?'), '●',
                    gauge_color(pct, warn, danger), label=key)
        row('exp', short(replay.get('exp_per_hour')), '●', label='EXP/h')
        row('profit_h', short(replay.get('profit_per_hour')), '○', label='Profit/h')
    else:
        mode = 'PAUSED' if s.get('state') == 'PAUSED' else snap.get('state')
        mp = s.get('map') or snap.get('map')
        title = '%s  ·  %s' % (MODE_LABEL.get(mode, mode),
                               ('%s %s' % (snap.get('map_source', '?'), mp)) if mp else '? Unknown Map')
        row('time', hms_sec(s.get('active')), '○')
        e = s.get('exp_pct_per_hour')
        row('exp', ('%+.2f%%' % e) if e is not None else None, '○')
        row('profit_h', short(s.get('profit_per_hour')), '○')
        if rp.get('kills') is not None and s.get('active'):
            row('kills', '{:,}'.format(int(rp['kills'] * 3600 / max(s['active'], 1))), '●')
        row('dps', short(rp.get('dps')), '●')
        row('session', short(s.get('profit')), '○')
        for t in (s.get('top') or [])[:3]:
            if t.get('value'):
                row('drops', short(t['value']), '○', label=t['name'][:10])
        for key, region in (('hp', 'HP_REGION'), ('sp', 'SP_REGION'), ('ap', 'AP_REGION')):
            row(key, ('%.0f%%' % vals[region]) if vals.get(region) is not None else None, '○',
                gauge_color(vals.get(region), warn, danger))
        wt = vals.get('WEIGHT_REGION')
        row('wt', ('%.0f%%' % wt) if wt is not None else None, '○',
            gauge_color(wt, max(0, weight_warn - 10), weight_warn, high_is_bad=True))

    if 'buffs' in keys:
        for b in buff_rows:
            rows.append(dict(label=b['name'][:10], value=mmss(b.get('remaining')),
                             src=b.get('icon', '○'), color=BUFF_COLOR.get(b.get('state'), OK)))
    return title, rows

