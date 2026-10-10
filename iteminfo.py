"""클라이언트 System/itemInfo_true.lub(Lua 5.1 32bit 바이트코드)에서 아이템 이름 테이블 추출.

lub 파일은 거대한 테이블 생성 코드뿐이라, 테이블 관련 명령만 처리하는 최소 해석기로 충분하다.
"""
import struct


class _Reader:
    def __init__(self, d):
        self.d, self.p = d, 12  # 12바이트 헤더 건너뜀

    def u8(self):
        self.p += 1
        return self.d[self.p - 1]

    def i32(self):
        self.p += 4
        return struct.unpack_from('<i', self.d, self.p - 4)[0]

    def dbl(self):
        self.p += 8
        return struct.unpack_from('<d', self.d, self.p - 8)[0]

    def string(self):
        n = self.i32()
        if n == 0:
            return None
        self.p += n
        return self.d[self.p - n:self.p - 1].decode('cp949', 'replace')


def _function(r):
    r.string(); r.i32(); r.i32(); r.u8(); r.u8(); r.u8(); r.u8()
    code = [r.i32() & 0xFFFFFFFF for _ in range(r.i32())]
    consts = []
    for _ in range(r.i32()):
        t = r.u8()
        consts.append(None if t == 0 else bool(r.u8()) if t == 1 else r.dbl() if t == 3 else r.string())
    for _ in range(r.i32()):
        _function(r)
    for _ in range(r.i32()): r.i32()
    for _ in range(r.i32()): r.string(); r.i32(); r.i32()
    for _ in range(r.i32()): r.string()
    return code, consts


def _run(code, k):
    reg, G = [None] * 256, {}
    rk = lambda x: k[x & 0xFF] if x & 0x100 else reg[x]
    for i in code:
        op, A, C, B, Bx = i & 0x3F, (i >> 6) & 0xFF, (i >> 14) & 0x1FF, (i >> 23) & 0x1FF, i >> 14
        if op == 0: reg[A] = reg[B]                                   # MOVE
        elif op == 1: reg[A] = k[Bx]                                  # LOADK
        elif op == 2: reg[A] = bool(B)                                # LOADBOOL
        elif op == 3: reg[A:B + 1] = [None] * (B + 1 - A)             # LOADNIL
        elif op == 5: reg[A] = G.get(k[Bx])                           # GETGLOBAL
        elif op == 6: reg[A] = reg[B].get(rk(C))                      # GETTABLE
        elif op == 7: G[k[Bx]] = reg[A]                               # SETGLOBAL
        elif op == 9: reg[A][rk(B)] = rk(C)                           # SETTABLE
        elif op == 10: reg[A] = {}                                    # NEWTABLE
        elif op == 34:                                                # SETLIST
            for j in range(1, B + 1): reg[A][(C - 1) * 50 + j] = reg[A + j]
        elif op == 36: reg[A] = None                                  # CLOSURE (무시)
        elif op == 30: break                                          # RETURN
        else: raise ValueError('지원하지 않는 opcode %d' % op)
    return G


def _table(path):
    return _run(*_function(_Reader(open(path, 'rb').read())))['tbl']


def load_names(path):
    """{아이템ID: 이름}"""
    return {int(i): v.get('identifiedDisplayName') or '' for i, v in _table(path).items()}


def load_items(path):
    """{아이템ID: {name, slot, description}}"""
    out = {}
    for i, v in _table(path).items():
        desc = v.get('identifiedDescriptionName') or {}
        lines = [desc[k] for k in sorted(desc)] if isinstance(desc, dict) else []
        out[int(i)] = dict(name=v.get('identifiedDisplayName') or '',
                           slot=int(v.get('slotCount') or 0),
                           description='\n'.join(l for l in lines if isinstance(l, str)))
    return out
