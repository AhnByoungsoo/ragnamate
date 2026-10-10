"""ID는 정수 또는 십진 정수 문자열만 허용한다. 이름 유사도로 ID를 추정하지 않는다."""
import re


def numeric_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value.strip()):
        return int(value.strip())
    return None
