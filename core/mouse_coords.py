"""Windows 물리 픽셀 또는 게임 클라이언트 원점 기준 좌표."""
import ctypes
import ctypes.wintypes as wt

def cursor_position():
    point = wt.POINT()
    fn = ctypes.windll.user32.GetCursorPos
    fn.argtypes = (ctypes.POINTER(wt.POINT),)
    if not fn(ctypes.byref(point)):
        raise OSError('마우스 위치를 읽지 못했습니다.')
    return point.x, point.y

def game_client():
    from live.window import find_game_window
    window = find_game_window()
    if not window or window.minimized:
        raise ValueError('표시된 라그나로크 창을 찾지 못했습니다.')
    return window.client

def validate_position(step):
    mode = step.get('position', 'current')
    if mode not in ('current', 'screen', 'game'):
        return '좌표 기준을 확인하세요.'
    if mode == 'current':
        return '마우스 이동에는 좌표가 필요합니다.' if step.get('type') == 'move' else None
    for key in ('x', 'y'):
        val = step.get(key)
        if type(val) is not int or not -100000 <= val <= 100000 or (mode == 'game' and val < 0):
            return '좌표는 정수 물리 픽셀로 지정하세요. 게임 기준은 0 이상입니다.'
    return None

def resolve_position(step, client=None):
    problem = validate_position(step)
    if problem: raise ValueError(problem)
    if step.get('position', 'current') == 'current': return None
    x, y = step['x'], step['y']
    if step['position'] == 'game':
        left, top, width, height = client if client is not None else game_client()
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError('지정 좌표가 현재 게임 창 밖입니다. 창 크기와 좌표를 확인하세요.')
        x, y = left + x, top + y
    return x, y

def capture_position(mode, cursor=None, client=None):
    x, y = cursor if cursor is not None else cursor_position()
    if mode == 'game':
        left, top, width, height = client if client is not None else game_client()
        x, y = x-left, y-top
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError('게임 화면 안으로 마우스를 옮긴 뒤 다시 가져오세요.')
    return x, y

def absolute_position(x, y, desktop):
    left, top, width, height = desktop
    if width <= 1 or height <= 1 or not left <= x < left+width or not top <= y < top+height:
        raise ValueError('지정 좌표가 연결된 화면 영역 밖입니다.')
    return round((x-left)*65535/(width-1)), round((y-top)*65535/(height-1))
