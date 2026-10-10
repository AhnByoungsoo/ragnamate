"""Ragnarok 리플레이(.rrf v0.05) 복호화 + 창고 아이템 추출.

포맷 참고: https://github.com/Tokeiburu/Rrf-Parser (ReplayService.cs)
"""
import struct

HEADER_START, HEADER_NORMAL, HEADER_EQUIP, HEADER_END = 0x0B08, 0x0B09, 0x0B39, 0x0B0B
INV_STORAGE = 2
SERVERS = {'바포메트': 129, '이그드라실': 229, '다크로드': 529, '이프리트': 729}


def _s32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x >> 31 else x


class Replay:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self._load(f.read())

    @classmethod
    def from_bytes(cls, data):
        obj = cls.__new__(cls)
        obj._load(data)
        return obj

    def _load(self, d):
        if len(d) < 352:
            raise ValueError('Incomplete Replay header')
        if d[100] != 5:
            raise ValueError('지원하지 않는 리플레이 버전: %d' % d[100])
        y, mo, da, _, h, mi, se = struct.unpack_from('<HBBBBBB', d, 104)
        self.date = (y, mo, da, h, mi, se)
        self._k1 = _s32(struct.unpack('<i', struct.pack('<HBB', y, mo, da))[0]) >> 5
        self._k2 = _s32(struct.unpack('<i', bytes([0, h, mi, se]))[0]) >> 3
        self.data = d
        self.containers = []
        for i in range(24):
            t, ln, off = struct.unpack_from('<Hii', d, 112 + i * 10)
            self.containers.append((t, ln if ln else len(d) - off, off))

    def _crypt(self, buf):
        out = bytearray(buf)
        for c in range(len(out) // 4):
            v = struct.unpack_from('<i', out, c * 4)[0]
            struct.pack_into('<I', out, c * 4, (v ^ _s32((self._k1 + c + 1) * self._k2)) & 0xFFFFFFFF)
        return bytes(out)

    def packets(self):
        from analyzer.framing import PacketFramer
        # 녹화가 끝나지 않은 파일은 헤더만 있고 패킷 컨테이너(type 1)가 아직 없다.
        # 제너레이터 안에서 StopIteration을 내면 RuntimeError로 바뀌어 원인을 알 수 없으므로 여기서 걸러낸다.
        found = [c for c in self.containers if c[0] == 1 and c[2]]
        if not found:
            raise ValueError('패킷 기록이 아직 없습니다 — 녹화 중이거나 중단된 Replay일 수 있습니다.')
        t, real, off = found[0]
        end = min(len(self.data), off + real)
        if off < 352 or real < 0:
            raise ValueError('Invalid packet container')
        framer = PacketFramer(self._crypt, off)
        for pos in range(off, end, 256 * 1024):
            for frame in framer.feed(self.data[pos:min(end, pos + 256 * 1024)], pos):
                yield frame.timestamp, frame.packet
        self.framing = dict(pending=len(framer.pending), rejected=framer.rejected, resync=framer.resync,
                            declared_missing=max(0, off + real - len(self.data)))

    def meta_chunks(self, container_type):
        """비패킷 컨테이너(2=ReplayData, 3=Session 등)의 {chunk_id: bytes}."""
        for t, real, off in self.containers:
            if t == container_type and off:
                c, q, out = self._crypt(self.data[off:off + real]), 0, {}
                while q + 6 <= len(c):
                    cid, cl = struct.unpack_from('<hi', c, q)
                    if cl < 0 or q + 6 + cl > len(c):
                        break
                    out[cid] = c[q + 6:q + 6 + cl]
                    q += 6 + max(cl, 0)
                return out
        return {}

    def server(self):
        blob = b''.join(self._crypt(self.data[off:off + real])
                        for t, real, off in self.containers if off and t != 1)
        for name in SERVERS:
            if name.encode('cp949') in blob:
                return name
        return None

    def storages(self):
        """창고를 열 때마다 받은 목록(0x0b08~0x0b0b)을 창고 단위 리스트로 반환."""
        return [items for inv_type, items in self.inventory_lists() if inv_type == INV_STORAGE]

    def inventory_lists(self, packets=None, with_time=False):
        """완결된(0x0b08~0x0b0b) 목록을 [(type, items)]로 반환. type 0=인벤토리 1=카트 2=창고.

        녹화 중이라 잘린 패킷/미완결 목록은 건너뛴다.
        packets: 이미 복호화한 [(time, packet)]을 재사용할 때 전달.
        with_time: True면 [(type, items, 목록 시작 time)] 반환.
        """
        result, cur, cur_type, cur_tm = [], None, None, None
        for tm, p in (packets if packets is not None else self.packets()):
            if len(p) < 4:
                continue
            h = struct.unpack_from('<H', p)[0]
            if h == HEADER_START:
                cur, cur_type, cur_tm = ([], p[4], tm) if len(p) >= 5 else (None, None, None)
            elif cur is None:
                continue
            elif h == HEADER_NORMAL:
                for o in range(5, len(p) - 33, 34):
                    idx, iid, typ, amt, wear = struct.unpack_from('<HIBHI', p, o)
                    cur.append(dict(id=iid, amount=amt, cards=struct.unpack_from('<4I', p, o + 13),
                                    refine=0, grade=0, equip=False, item_type=typ, index=idx,
                                    wear_state=wear, flag=p[o + 33], options=[]))
            elif h == HEADER_EQUIP:
                for o in range(5, len(p) - 67, 68):
                    idx, iid, typ, loc, wear = struct.unpack_from('<HIBII', p, o)
                    n_opt = min(p[o + 39], 5)
                    opts = [dict(zip(('id', 'value', 'param'), struct.unpack_from('<HhB', p, o + 40 + 5 * j)))
                            for j in range(n_opt)]
                    cur.append(dict(id=iid, amount=1, cards=struct.unpack_from('<4I', p, o + 15),
                                    refine=p[o + 65], grade=p[o + 66], equip=True, item_type=typ, index=idx,
                                    location=loc, wear_state=wear, flag=p[o + 67],
                                    options=[x for x in opts if x['id']]))
            elif h == HEADER_END:
                result.append((cur_type, cur, cur_tm) if with_time else (cur_type, cur))
                cur = None
        return result
