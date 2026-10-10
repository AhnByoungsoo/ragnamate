"""화면 Vision: 바(게이지) 채움 비율, 변화 감지, 장면 판별."""
import numpy as np


def changed(prev, cur, threshold=6.0, frac=0.002):
    """OCR이 필요한 변화인지: 평균 절대차가 threshold 이상이거나, 뚜렷이 바뀐 픽셀 비율이 frac 이상.
    (채팅 한 줄 추가처럼 작은 영역만 바뀌는 경우는 비율 기준으로 잡는다)"""
    if prev is None or cur is None or prev.shape != cur.shape:
        return True
    diff = np.abs(prev.astype(np.int16) - cur.astype(np.int16)).max(axis=2)
    return float(diff.mean()) >= threshold or float((diff > 40).mean()) >= frac


def bar_fill_ratio(img, bar_color=None, tol=60):
    """가로 게이지 채움 비율(0~1).

    bar_color가 있으면 그 색과 가까운 픽셀 열의 오른쪽 끝을 채움 끝으로 본다.
    없으면 채도(saturation)가 높은 픽셀 열을 채움으로 본다(배경은 보통 회색/어두움).
    바 가운데 줄 여러 개를 모아 판단해 글자/테두리 영향을 줄인다.
    """
    if img is None or img.size == 0:
        return None
    h, w = img.shape[:2]
    band = img[max(0, h // 2 - max(1, h // 6)):h // 2 + max(1, h // 6) + 1].astype(np.int16)
    if bar_color:
        dist = np.abs(band - np.array(bar_color, dtype=np.int16)).sum(axis=2)
        mask = dist <= tol
    else:
        mx, mn = band.max(axis=2), band.min(axis=2)
        mask = (mx - mn) >= 60
    cols = mask.mean(axis=0) >= 0.5
    if not cols.any():
        return 0.0
    filled = np.where(cols)[0]
    return round((filled.max() + 1) / w, 4)


def dominant_color(img):
    """Calibration 때 바 채움 색 샘플(가장 채도가 높은 픽셀들의 평균, BGR)."""
    px = img.reshape(-1, 3).astype(np.int16)
    sat = px.max(axis=1) - px.min(axis=1)
    top = px[sat >= np.percentile(sat, 80)]
    return [int(v) for v in top.mean(axis=0)] if len(top) else []


BAR_ORDER = ('HP_REGION', 'SP_REGION', 'AP_REGION')
COLOR_KIND = {'red': 'HP_REGION', 'green': 'HP_REGION', 'blue': 'SP_REGION', 'yellow': 'AP_REGION'}


def _hue_kind(bgr):
    """막대 채움 색 대략 분류. 애매하면 None."""
    if len(bgr) != 3:
        return None
    b, g, r = bgr
    if r > g + 40 and r > b + 40:
        return 'red'
    if b > r + 40 and b > g + 20:
        return 'blue'
    if r > b + 60 and g > b + 60:
        return 'yellow'
    if g > r + 40 and g > b + 40:
        return 'green'
    return None


def suggest_bars(img, min_width=48, max_height=40):
    """게이지 막대처럼 보이는 가로 영역 후보를 찾아 HP/SP/AP로 배치해 본다.

    **추천일 뿐이다.** 사용자가 보정 화면에서 확인하고 저장해야 실제 Region이 된다.
    색으로 먼저 맞추고(빨강·초록=HP, 파랑=SP, 노랑=AP), 색을 모르는 후보는 위에서부터 남은 자리에 넣는다.
    반환: [(region_kind, (x, y, w, h)), ...]
    """
    import cv2
    if img is None or img.size == 0:
        return []
    px = img.astype(np.int16)
    sat = px.max(axis=2) - px.min(axis=2)
    mask = ((sat >= 60) & (px.max(axis=2) >= 80)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cands = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < min_width or not 3 <= h <= max_height or w < h * 3 or area < w * h * 0.5:
            continue
        cands.append(((x, y, w, h), _hue_kind(dominant_color(img[y:y + h, x:x + w]))))
    cands.sort(key=lambda c: (c[0][1], c[0][0]))
    out = {}
    for rect, color in cands:
        kind = COLOR_KIND.get(color)
        if kind and kind not in out:
            out[kind] = rect
    for rect, color in cands:
        if rect in out.values():
            continue
        free = next((k for k in BAR_ORDER if k not in out), None)
        if free is None:
            break
        out[free] = rect
    return [(k, out[k]) for k in BAR_ORDER if k in out]


def scene_from_text(lines):
    """전체 화면 OCR 결과로 대략적인 장면 판별 (게임 밖 화면에서 세션을 만들지 않기 위함)."""
    text = ' '.join(l.text for l in lines)
    if 'Character List' in text or '게임시작' in text:
        return 'CHARACTER_SELECT'
    return 'UNKNOWN'
