"""커뮤니티 Guide Pack (예: ro_guides_kr.json).

앱 업데이트 없이 Guide 데이터만 교체할 수 있도록 data/guides/packs/ 에 넣어 사용한다.
원격 다운로드는 아직 구현하지 않는다(출처/무결성 검증 방법이 정해진 뒤 추가). 가져온 Pack의 Guide 중
출처가 community/user 인 것은 UI에서 △/! 신뢰도로 표시된다.
"""
from navigator.importers.json_importer import import_json


def install_pack(repo, path):
    return import_json(repo, path)
