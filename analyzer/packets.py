"""Packet 정의 레지스트리.

verified=True  : 샘플 Replay(kRO 2026-09 클라이언트)에서 길이·필드 의미를 교차검증한 정의.
                 2026-10-10 에 추가한 정의는 칸 배치를 해외 공개 자료(rAthena·Hercules 의 packets_struct,
                 OpenKore 의 Receive/ServerType0.pm, kRO recvpackets 길이표)에서 배우고, 내 리플레이의 실제 값과
                 맞는지 따로 확인한 것이다(REPLAY_PACKET_RESEARCH.md). 읽기만 한다 — 보내는 패킷은 다루지 않는다.
verified=False : 샘플에 등장하지 않아 검증하지 못한 후보 정의(rAthena 문서 기준).
                 설정 'experimental_packets'가 켜졌을 때만 사용하며, 이벤트 신뢰도는 △로 표시한다.

클라이언트 버전별로 구조가 다르면 PACKET_SETS에 새 버전 키로 정의를 추가한다.
길이가 정의와 다르면 해석하지 않고 unsupported로 기록한다.
"""
import struct
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_PACKET_VER = 'kro_2026'


@dataclass(frozen=True)
class PacketDef:
    id: int
    name: str
    length: Optional[int]        # None = 가변 길이(헤더 뒤 2바이트)
    decode: Callable
    verified: bool
    note: str = ''


def _cstr(b):
    return b.split(b'\0', 1)[0].decode('cp949', 'replace')


def _pos3(b, o):
    return (b[o] << 2) | (b[o + 1] >> 6), ((b[o + 1] & 0x3F) << 4) | (b[o + 2] >> 4)


def _move(b, o):
    sx, sy = _pos3(b, o)
    return sx, sy, ((b[o + 2] & 0x0F) << 6) | (b[o + 3] >> 2), ((b[o + 3] & 0x03) << 8) | b[o + 4]


# ---- 검증된 디코더 ------------------------------------------------------------
def d_vanish(p):          # 0x0080: type 0=시야이탈 1=사망 2=로그아웃 3=순간이동 (샘플: 0,2,3 관측)
    aid, typ = struct.unpack_from('<IB', p, 2)
    return dict(aid=aid, vanish_type=typ)


def d_playermove(p):      # 0x0087: 시작좌표 = 맵 도착좌표와 일치 확인
    sx, sy, dx, dy = _move(p, 6)
    return dict(tick=struct.unpack_from('<I', p, 2)[0], sx=sx, sy=sy, dx=dx, dy=dy)


def d_stopmove(p):        # 0x0088
    aid, x, y = struct.unpack_from('<IHH', p, 2)
    return dict(aid=aid, x=x, y=y)


def d_mapmove(p):         # 0x0091 (22) / 0x0ac7 (156): 맵명 16 + x,y
    name = _cstr(p[2:18])
    if not name.endswith('.gat'):
        raise ValueError('map name')
    x, y = struct.unpack_from('<HH', p, 18)
    return dict(map=name[:-4], x=x, y=y)


def d_par(p):             # 0x00b0 / 0x00b1: varID.W value.L
    var, val = struct.unpack_from('<Hi', p, 2)
    return dict(var=var, value=val)


def d_state3(p):          # 0x0983: EFST, AID, state, maxMS, remainMS, val1..3
    efst, aid, state, max_ms, remain_ms, v1, v2, v3 = struct.unpack_from('<HIBiiiii', p, 2)
    return dict(efst=efst, aid=aid, state=state, max_ms=max_ms, remain_ms=remain_ms, vals=(v1, v2, v3))


def d_state2(p):          # 0x043f: EFST, AID, state, remainMS, val1..3
    efst, aid, state, remain_ms, v1, v2, v3 = struct.unpack_from('<HIBiiii', p, 2)
    return dict(efst=efst, aid=aid, state=state, max_ms=None, remain_ms=remain_ms, vals=(v1, v2, v3))


def d_state1(p):          # 0x0196: EFST, AID, state
    efst, aid, state = struct.unpack_from('<HIB', p, 2)
    return dict(efst=efst, aid=aid, state=state, max_ms=None, remain_ms=None, vals=())


def d_efst_enter(p):      # 0x0984: 시야에 들어온 유닛의 상태(AID 먼저)
    aid, efst, max_ms, remain_ms, v1, v2, v3 = struct.unpack_from('<IHiiiii', p, 2)
    return dict(efst=efst, aid=aid, state=1, max_ms=max_ms, remain_ms=remain_ms, vals=(v1, v2, v3))


def d_cast(p):            # 0x07fb: 캐스팅 시작
    src, tgt, x, y, skill, element, cast_ms, disposable = struct.unpack_from('<IIHHHIIB', p, 2)
    return dict(src=src, tgt=tgt, x=x, y=y, skill=skill, element=element, cast_ms=cast_ms)


def d_useskill(p):        # 0x09cb: 비피해 스킬 사용 (level 필드는 힐량 등으로도 쓰임)
    skill, level, tgt, src, result = struct.unpack_from('<HiIIB', p, 2)
    return dict(skill=skill, level=level, tgt=tgt, src=src, result=result)


def d_act3(p):            # 0x08c8: 일반 공격/동작
    src, tgt, tick, sdelay, ddelay, dmg, is_sp, div, typ, left = struct.unpack_from('<IIIiiiBHBi', p, 2)
    return dict(src=src, tgt=tgt, tick=tick, src_delay=sdelay, tgt_delay=ddelay, damage=dmg, is_sp=is_sp,
                div=div, action=typ, left_damage=left)


def _unit(p, name_off, pos_off, lvl_off, hp_off, moving):
    d = dict(obj_type=p[4], aid=struct.unpack_from('<I', p, 5)[0], class_id=struct.unpack_from('<H', p, 23)[0],
             name=_cstr(p[name_off:]))
    if pos_off is not None:
        if moving:
            d['x'], d['y'], d['dx'], d['dy'] = _move(p, pos_off)
        else:
            d['x'], d['y'] = _pos3(p, pos_off)
    if lvl_off is not None:
        d['level'] = struct.unpack_from('<H', p, lvl_off)[0]
    if hp_off is not None:
        d['max_hp'], d['hp'] = struct.unpack_from('<ii', p, hp_off)
    return d


def d_standentry(p):      # 0x09ff: 좌표·레벨·이름 교차검증 완료
    return _unit(p, 84, 63, 69, 73, False)


def d_moveentry(p):       # 0x09fd: 이동 중 유닛
    return _unit(p, 90, 67, 75, 79, True)


def d_newentry(p):        # 0x09fe: 새로 나타난 유닛. 0x09ff 보다 바이트 하나가 적다(OpenKore 정의: a3 C2 … / a3 C3 …)
    # 예전에는 이름 자리를 88로 읽어 앞 5바이트가 잘렸다('블루 문 로리루리' → '문 로리루리'). 83으로 읽으면 같은 대상의
    # 0x09ff/0x09fd 이름과 1,092건 일치(다른 21건은 번호가 다른 캐릭터에게 다시 쓰인 것), 좌표·레벨 1,241건 모두 범위 안.
    return _unit(p, 83, 63, 68, 72, False)


def d_pickup(p):          # 0x0b41 (70): 칸 수량 아이템 감정 파손 카드×4 부위 종류 결과 만료 ?.W 옵션×5 즐겨찾기 모양.W 제련 등급
    index, count, item_id = struct.unpack_from('<HHI', p, 2)
    return dict(index=index, count=count, item_id=item_id, result=p[33],
                cards=[c for c in struct.unpack_from('<4I', p, 12) if c], refine=p[68], grade=p[69])


def d_act2(p):            # 0x02e1 (33): 몬스터가 때린 평타. 공격자 대상 tick 공속 피격모션 피해 타수.W 종류.B 왼손피해
    src, tgt, tick, sdelay, ddelay, dmg, div, typ, left = struct.unpack_from('<IIIiiiHBi', p, 2)
    return dict(src=src, tgt=tgt, tick=tick, damage=dmg, div=div, action=typ, left_damage=left)


def d_cast_old(p):        # 0x013e (24): 몬스터의 시전 시작(0x07fb 에서 끝의 1바이트만 없다)
    src, tgt, x, y, skill, element, cast_ms = struct.unpack_from('<IIHHHII', p, 2)
    return dict(src=src, tgt=tgt, x=x, y=y, skill=skill, element=element, cast_ms=cast_ms)


def d_skill_delay(p):     # 0x043d (8): 스킬 번호.W 다시 쓸 수 있을 때까지 ms.L
    skill, delay = struct.unpack_from('<HI', p, 2)
    return dict(skill=skill, delay_ms=delay)


def d_skill_delay_list(p):    # 0x0985: (스킬.W 전체ms.L 남은ms.L) × n — 접속·맵 이동 때 남아 있는 쿨타임
    return dict(skills=[dict(zip(('skill', 'total_ms', 'remain_ms'), struct.unpack_from('<HII', p, 4 + k * 10)))
                        for k in range((len(p) - 4) // 10)])


def d_skill_fail(p):      # 0x0110 (14): 스킬.W 구분.L 아이템.L 성공.B 사유.B
    skill, btype, item_id, flag, cause = struct.unpack_from('<HiIBB', p, 2)
    return dict(skill=skill, btype=btype, item_id=item_id, flag=flag, cause=cause)


def d_hp_tiny(p):         # 0x0a36 (7): 대상.L HP.B — 0~20 (5% 단위)
    aid, hp = struct.unpack_from('<IB', p, 2)
    return dict(aid=aid, hp=hp)


def d_item_fall(p):       # 0x0add (24): 바닥번호 아이템 종류.W 감정.B x.W y.W subx.B suby.B 수량.W 효과.B 효과종류.W
    gid, item_id, typ, identified, x, y, _sx, _sy, amount = struct.unpack_from('<IIHBHHBBH', p, 2)
    return dict(gid=gid, item_id=item_id, x=x, y=y, amount=amount)


def d_item_entry(p):      # 0x009d (19): 화면에 들어왔을 때 이미 바닥에 있던 물건. 바닥번호 아이템 감정.B x.W y.W 수량.W
    gid, item_id, identified, x, y, amount = struct.unpack_from('<IIBHHH', p, 2)
    return dict(gid=gid, item_id=item_id, x=x, y=y, amount=amount)


def d_item_vanish(p):     # 0x00a1 (6): 바닥번호
    return dict(gid=struct.unpack_from('<I', p, 2)[0])


def d_item_used(p):       # 0x01c8 (15): 칸.W 아이템.L 사용자.L 남은수.W 결과.B
    index, item_id, aid, remaining, ok = struct.unpack_from('<HIIHB', p, 2)
    return dict(index=index, item_id=item_id, aid=aid, remaining=remaining, ok=ok)


def d_skill_list_old(p):  # 0x010f: (번호.W 종류.L 레벨.W SP.W 사거리.W 이름.24 올릴수있음.B) × n
    return dict(skills={sid: dict(level=lv, sp=sp, range=rng) for sid, _inf, lv, sp, rng in (
        struct.unpack_from('<HIHHH', p, 4 + k * 37) for k in range((len(p) - 4) // 37))})


def d_skill_list(p):      # 0x0b32: (번호.W 종류.L 레벨.W SP.W 사거리.W 올릴수있음.B 레벨2.W) × n
    return dict(skills={sid: dict(level=lv, sp=sp, range=rng) for sid, _inf, lv, sp, rng in (
        struct.unpack_from('<HiHHH', p, 4 + k * 15) for k in range((len(p) - 4) // 15))})


def d_quest_add(p):       # 0x0b0c (155): 퀘스트.L 활성.B 시작.L 끝.L 목표수.W + 목표 × 3 (46바이트)
    quest, active, start, end, count = struct.unpack_from('<IBiih', p, 2)
    goals = []
    for k in range(min(count, (len(p) - 17) // 46)):
        o = 17 + k * 46
        ident, _ident2, _mob_type, mob_id, _lv0, _lv1, hunted = struct.unpack_from('<IIIIhhh', p, o)
        goals.append(dict(ident=ident, mob_id=mob_id, count=hunted, name=_cstr(p[o + 22:o + 46])))
    return dict(quest=quest, active=active, start=start, end=end, goals=goals)


def d_quest_hunt(p):      # 0x0afe: 목표수.W + (퀘스트.L 목표.L 목표2.L 채울수.W 잡은수.W) × n
    return dict(goals=[dict(zip(('quest', 'ident', 'ident2', 'need', 'count'), struct.unpack_from('<IIIhh', p, 6 + k * 16)))
                       for k in range((len(p) - 6) // 16)])


def d_quest_hunt_info(p):     # 0x08fe: (퀘스트.L 몬스터.L 채울수.W 잡은수.W) × n
    return dict(goals=[dict(zip(('quest', 'mob_id', 'need', 'count'), struct.unpack_from('<IIhh', p, 4 + k * 12)))
                       for k in range((len(p) - 4) // 12)])


def d_quest_delete(p):    # 0x02b4 (6): 퀘스트.L
    return dict(quest=struct.unpack_from('<I', p, 2)[0])


def d_store_sign(p):      # 0x0131 (86) 판매 노점 / 0x0814 (86) 구매 노점: 주인.L 상점명.80
    return dict(aid=struct.unpack_from('<I', p, 2)[0], title=_cstr(p[6:86]))


def d_store_search(p):    # 0x0b64: 게임 안 노점 검색 결과. 첫쪽.B 다음쪽.B 남은횟수.B + 매물 × n (142바이트)
    rows = []
    for k in range((len(p) - 7) // 142):
        o = 7 + k * 142
        store, aid = struct.unpack_from('<II', p, o)
        item_id, _typ, price, amount = struct.unpack_from('<IBIH', p, o + 88)
        rows.append(dict(store=store, aid=aid, shop=_cstr(p[o + 8:o + 88]), item_id=item_id, price=price, amount=amount,
                         cards=[c for c in struct.unpack_from('<4I', p, o + 99) if c], refine=p[o + 140], grade=p[o + 141]))
    return dict(first=p[4], more=p[5], uses_left=p[6], rows=rows)


def d_store_items(p):     # 0x0b3d: 열어 본 판매 노점의 물건. 주인.L 노점.L + 물건 × n (64바이트)
    aid, vender = struct.unpack_from('<II', p, 4)
    rows = []
    for k in range((len(p) - 12) // 64):
        o = 12 + k * 64
        price, amount, _index, _typ, item_id = struct.unpack_from('<IHhBI', p, o)
        rows.append(dict(item_id=item_id, price=price, amount=amount, cards=[c for c in struct.unpack_from('<4I', p, o + 15) if c],
                         refine=p[o + 62], grade=p[o + 63]))
    return dict(aid=aid, vender=vender, rows=rows)


# ---- 미검증 후보 --------------------------------------------------------------
def c_skill_damage(p):    # 0x01de ZC_NOTIFY_SKILL2
    skill, src, tgt, tick, sdelay, ddelay, dmg, level, count, action = struct.unpack_from('<HIIIiiiHhB', p, 2)
    return dict(skill=skill, src=src, tgt=tgt, tick=tick, damage=dmg, level=level, div=count, action=action)


def c_notify_exp2(p):     # 0x0acc ZC_NOTIFY_EXP2: AID, amount(int64), varID, expType
    aid, amount, var, exp_type = struct.unpack_from('<IqHh', p, 2)
    return dict(aid=aid, amount=amount, var=var, exp_type=exp_type)


def c_notify_exp(p):      # 0x07f6 ZC_NOTIFY_EXP
    aid, amount, var, exp_type = struct.unpack_from('<IiHh', p, 2)
    return dict(aid=aid, amount=amount, var=var, exp_type=exp_type)


def c_delete_item(p):     # 0x07fa ZC_DELETE_ITEM_FROM_BODY: reason, index, count
    reason, index, count = struct.unpack_from('<HHH', p, 2)
    return dict(reason=reason, index=index, count=count)


_DEFS = [
    PacketDef(0x0080, 'ZC_NOTIFY_VANISH', 7, d_vanish, True),
    PacketDef(0x0087, 'ZC_NOTIFY_PLAYERMOVE', 12, d_playermove, True),
    PacketDef(0x0088, 'ZC_STOPMOVE', 10, d_stopmove, True),
    PacketDef(0x0091, 'ZC_NPCACK_MAPMOVE', 22, d_mapmove, True),
    PacketDef(0x0ac7, 'ZC_NPCACK_SERVERMOVE', 156, d_mapmove, True),
    PacketDef(0x00b0, 'ZC_PAR_CHANGE', 8, d_par, True),
    PacketDef(0x00b1, 'ZC_LONGPAR_CHANGE', 8, d_par, True),
    PacketDef(0x0983, 'ZC_MSG_STATE_CHANGE3', 29, d_state3, True),
    PacketDef(0x043f, 'ZC_MSG_STATE_CHANGE2', 25, d_state2, True),
    PacketDef(0x0196, 'ZC_MSG_STATE_CHANGE', 9, d_state1, True),
    PacketDef(0x0984, 'ZC_EFST_SET_ENTER2', 28, d_efst_enter, True),
    PacketDef(0x07fb, 'ZC_USESKILL_ACK2', 25, d_cast, True),
    PacketDef(0x09cb, 'ZC_USE_SKILL2', 17, d_useskill, True),
    PacketDef(0x08c8, 'ZC_NOTIFY_ACT3', 34, d_act3, True),
    PacketDef(0x09ff, 'ZC_NOTIFY_STANDENTRY', None, d_standentry, True),
    PacketDef(0x09fd, 'ZC_NOTIFY_MOVEENTRY', None, d_moveentry, True),
    PacketDef(0x09fe, 'ZC_NOTIFY_NEWENTRY', None, d_newentry, True),
    # 실측 2026-10-10(리플레이 21개 345건): 아이템 번호가 있는 337건은 모두 결과 0이고 이름이 item_db와 맞음,
    # 나머지 8건은 번호 0·결과 1 또는 6(줍기 실패). 창고에서 꺼낸 23건은 전부 직전에 0x00f6이 있었다.
    # 제련(끝에서 둘째)·등급(끝)·카드(12~27) 자리는 OpenKore 정의로 배웠고, 창고에서 꺼낸 장비 20건이 창고 목록의
    # 제련·카드·등급과 20/20 일치(그중 18건은 값이 0이 아님 — 예: +12 등급 4 베스퍼 헤드기어).
    PacketDef(0x0b41, 'ZC_ITEM_PICKUP_ACK', 70, d_pickup, True),
    # ---- 2026-10-10 해외 공개 정의로 배운 것. 뒤의 글은 내 리플레이(22개)에서 확인한 내용 ----
    PacketDef(0x02e1, 'ZC_NOTIFY_ACT2', 33, d_act2, True, '몬스터 평타 4,397건 · 맞은 피해 합이 HP 감소의 97%(321321.rrf)'),
    PacketDef(0x013e, 'ZC_USESKILL_ACK', 24, d_cast_old, True, '501건 전부 몬스터의 시전 · 스킬 이름 확인'),
    PacketDef(0x043d, 'ZC_SKILL_POSTDELAY', 8, d_skill_delay, True, '2,346건 중 2,345건이 내가 쓴 스킬 · 스킬마다 값 고정'),
    PacketDef(0x0985, 'ZC_SKILL_POSTDELAY_LIST', None, d_skill_delay_list, True, '9건 · (길이-4)가 10의 배수'),
    PacketDef(0x0110, 'ZC_ACK_TOUSESKILL', 14, d_skill_fail, True, '601건 · 사유 번호의 뜻은 rAthena 표'),
    PacketDef(0x0a36, 'ZC_HP_INFO_TINY', 7, d_hp_tiny, True, '3,207건 전부 몬스터 · 값 0~20'),
    PacketDef(0x0add, 'ZC_ITEM_FALL_ENTRY', 24, d_item_fall, True, '2,101건 아이템 번호 100% 확인 · 2,096건이 몬스터 사망 0.6초 안'),
    PacketDef(0x009d, 'ZC_ITEM_ENTRY', 19, d_item_entry, True, '1,940건'),
    PacketDef(0x00a1, 'ZC_ITEM_DISAPPEAR', 6, d_item_vanish, True, '1,684건'),
    PacketDef(0x01c8, 'ZC_USE_ITEM_ACK2', 15, d_item_used, True, '1,153건 아이템 번호 100% 확인 · 결과 칸의 뜻은 미확인'),
    PacketDef(0x010f, 'ZC_SKILLINFO_LIST', None, d_skill_list_old, True, '22건 · 스킬 이름 100% 확인'),
    PacketDef(0x0b32, 'ZC_SKILLINFO_LIST2', None, d_skill_list, True, '25건 · 스킬 이름 100% 확인'),
    PacketDef(0x0b0c, 'ZC_ADD_QUEST_EX', 155, d_quest_add, True, '24건 · 퀘스트 번호가 진행 패킷과 같음'),
    PacketDef(0x0afe, 'ZC_UPDATE_MISSION_HUNT_EX', None, d_quest_hunt, True, '2,582건 · 잡은 수가 1씩 오름'),
    PacketDef(0x08fe, 'ZC_HUNTING_QUEST_INFO', None, d_quest_hunt_info, True, '9건'),
    PacketDef(0x02b4, 'ZC_DEL_QUEST', 6, d_quest_delete, True, '12건'),
    PacketDef(0x0131, 'ZC_STORE_ENTRY', 86, d_store_sign, True, '291건 상점명이 읽힘'),
    PacketDef(0x0814, 'ZC_BUYING_STORE_ENTRY', 86, d_store_sign, True, '14건 상점명이 읽힘'),
    PacketDef(0x0b64, 'ZC_SEARCH_STORE_INFO_ACK', None, d_store_search, True,
              '2건 · 같은 매물(7,500 Z × 1,470)이 바로 뒤 연 노점 목록에도 있음'),
    PacketDef(0x0b3d, 'ZC_PC_PURCHASE_ITEMLIST_FROMMC', None, d_store_items, True, '3건 · 가격 × 10 = 직후 제니 감소(85,000 / 75,000)'),
    # 인벤토리 목록은 rrf.Replay.inventory_lists()가 처리 (기존 파서 재사용)
    PacketDef(0x0b08, 'ZC_INVENTORY_START', None, None, True),
    PacketDef(0x0b09, 'ZC_INVENTORY_ITEMLIST_NORMAL', None, None, True),
    PacketDef(0x0b39, 'ZC_INVENTORY_ITEMLIST_EQUIP', None, None, True),
    PacketDef(0x0b0b, 'ZC_INVENTORY_END', 4, None, True),
    # 후보(미검증)
    PacketDef(0x01de, 'ZC_NOTIFY_SKILL2', 33, c_skill_damage, False, '스킬 피해'),
    PacketDef(0x0acc, 'ZC_NOTIFY_EXP2', 18, c_notify_exp2, False, 'EXP 획득'),
    PacketDef(0x07f6, 'ZC_NOTIFY_EXP', 14, c_notify_exp, False, 'EXP 획득'),
    # 길이는 8이다(실측 84/84 — 예전 정의 10이라 한 번도 해석되지 않았다). 사유 4(창고로 옮김)는 바로 뒤
    # 창고 추가(0x0b44)의 수량과 78/78 일치. 사유 6(판매)은 표본이 없어 후보로 둔다.
    PacketDef(0x07fa, 'ZC_DELETE_ITEM_FROM_BODY', 8, c_delete_item, False, '아이템 빠짐(소모·창고·판매)'),
]

PACKET_SETS = {DEFAULT_PACKET_VER: {d.id: d for d in _DEFS}}


def registry(packet_ver=DEFAULT_PACKET_VER, experimental=False):
    if packet_ver not in PACKET_SETS:
        raise ValueError('Unsupported packet profile: %s' % packet_ver)
    defs = PACKET_SETS[packet_ver]
    return {k: v for k, v in defs.items() if v.verified or experimental}


def packet_name(pid, packet_ver=DEFAULT_PACKET_VER):
    d = PACKET_SETS.get(packet_ver, {}).get(pid)
    return d.name if d else ''
