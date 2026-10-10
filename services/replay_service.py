"""Replay 폴더 관리 + 최신 .rrf 찾기 + 파싱 결과를 ItemEntry로 표준화.

파싱은 기존 rrf.Replay를 그대로 사용한다. 원본 파일은 건드리지 않고 임시 복사본을 읽는다
(녹화 중이거나 게임이 파일을 잡고 있어도 읽을 수 있게).
"""
import datetime
import glob
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from core.models import ITEM_TYPES, ItemEntry
from rrf import Replay

log = logging.getLogger(__name__)
FLAG_IDENTIFIED = 0x01
SOURCE_NAMES = {0: '인벤토리', 1: '카트'}


@dataclass
class ReplayResult:
    path: str
    modified: float
    recorded_at: datetime.datetime = None
    server: str = None
    entries: list = field(default_factory=list)
    storage_count: int = 0
    warnings: list = field(default_factory=list)


def find_latest(folder):
    """(경로, 수정시각) 또는 None"""
    files = glob.glob(os.path.join(folder, '*.rrf'))
    if not files:
        return None
    path = max(files, key=os.path.getmtime)
    return path, os.path.getmtime(path)


def _copy_for_read(path):
    """잠긴/녹화 중 파일도 읽을 수 있게 임시 복사본 생성. 복사 실패 시 원본 경로 반환."""
    fd, tmp = tempfile.mkstemp(prefix='ro_storage_pricer_', suffix='.rrf')
    os.close(fd)
    for _ in range(3):
        try:
            shutil.copyfile(path, tmp)
            return tmp
        except PermissionError:
            time.sleep(0.5)
    os.remove(tmp)
    return path


def _entries(source, items, item_db):
    out = []
    for it in items:
        cards = [c for c in it['cards'] if c and item_db.known(c)]
        raw = {k: v for k, v in it.items() if k not in ('cards', 'options')}
        raw['card_slots'] = list(it['cards'])
        out.append(ItemEntry(
            source=source, item_id=it['id'], name=item_db.name(it['id']), quantity=it['amount'],
            refine=it['refine'] if it['equip'] else None, cards=cards, options=list(it.get('options', [])),
            identified=bool(it['flag'] & FLAG_IDENTIFIED) if 'flag' in it else None,
            raw_data=raw, category=ITEM_TYPES.get(it.get('item_type')), is_equip=it['equip']))
    return out


def open_replay(path):
    """(Replay, 읽은 사본 경로) — 잠긴/녹화 중 파일은 임시 복사본으로 연다. 호출자가 close_copy 해야 함."""
    src = _copy_for_read(path)
    try:
        return Replay(src), src
    except Exception as e:
        close_copy(path, src)
        log.exception('Replay 헤더 파싱 실패: %s', path)
        raise ValueError('Replay 파일을 읽을 수 없습니다(손상되었거나 지원하지 않는 형식): %s'
                         % os.path.basename(path)) from e


def close_copy(path, src):
    if src != path:
        try:
            os.remove(src)
        except OSError:
            pass


def load_replay(path, item_db):
    rp, src = open_replay(path)
    try:
        try:
            lists = rp.inventory_lists()
            warn = None
        except Exception:
            log.exception('아이템 목록 파싱 중 오류: %s', path)
            lists, warn = [], 'Replay 일부를 읽지 못했습니다(logs 참고).'
        res = build_storage_result(path, rp, lists, item_db)
        if warn:
            res.warnings.insert(0, warn)
        return res
    finally:
        close_copy(path, src)


def build_storage_result(path, rp, lists, item_db):
    """inventory_lists 결과 → 창고/인벤토리/카트 ItemEntry (Replay 분석과 파싱 결과 공유)."""
    res = ReplayResult(path=path, modified=os.path.getmtime(path))
    y, mo, d, h, mi, s = rp.date
    res.recorded_at = datetime.datetime(y, mo, d, h, mi, s)
    try:
        res.server = rp.server()
    except Exception:
        log.exception('서버 판별 실패')
    lists = [(l[0], l[1]) for l in lists]

    # 창고: 연 순서대로 창고1..N (빈 목록/완전히 같은 목록은 중복으로 보고 제외)
    # 인벤토리/카트: 가장 마지막 목록 사용
    seen, storages, latest = [], [], {}
    for inv_type, items in lists:
        if inv_type == 2:
            sig = sorted((i['id'], i['amount'], i['refine'], i['cards']) for i in items)
            if items and sig not in seen:
                seen.append(sig)
                storages.append(items)
        elif inv_type in SOURCE_NAMES:
            latest[inv_type] = items
    for n, items in enumerate(storages, 1):
        res.entries += _entries('창고%d' % n, items, item_db)
    for inv_type, name in SOURCE_NAMES.items():
        if inv_type in latest:
            res.entries += _entries(name, latest[inv_type], item_db)
    res.storage_count = len(storages)

    if not storages:
        res.warnings.append('이 Replay에는 창고 목록이 없습니다. 녹화 중에 창고를 열어야 기록됩니다.')
    if 0 not in latest:
        res.warnings.append('인벤토리 목록이 없습니다(맵 이동/재접속 시 기록됨).')
    item_db.remember_categories({e.item_id: e.category for e in res.entries})
    return res
