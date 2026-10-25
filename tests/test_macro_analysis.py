"""G HUB 매크로 분석: UserKeys.lua 키 연결, 스킬바 패킷, 매크로 간격, 측정한 것만 말하는 분석 글."""
import struct
import unittest

import helpers  # noqa: F401  (sys.path 설정)

from core import macro_analysis as A  # noqa: E402

# 2026-10-25 이 PC 게임 폴더의 savedata/UserKeys.lua 에서 일부(형식 그대로)
USERKEYS = '''USERKEY_1 = {
\t[9] = { EXE = "스킬바 2-1번", KEY1 = 81 },
\t[10] = { EXE = "스킬바 2-2번", KEY1 = 87 },
\t[13] = { EXE = "스킬바 2-5번", KEY1 = 84 },
\t[18] = { EXE = "스킬바 3-1번", KEY1 = 65 },
\t[35] = { EXE = "스킬바 4-9번", KEY1 = 32 },
}

USERKEY_2 = {
\t[24] = { EXE = "호문 정보 창 On/Off", KEY1 = 221, KEY2 = 17 },
}
'''


def bar_packet(cells):
    b = bytearray(struct.pack('<HBH', 0x0b20, 0, 0)) + bytes(38 * 7)
    for slot, (is_skill, ident, n) in cells.items():
        struct.pack_into('<BIH', b, 5 + slot * 7, is_skill, ident, n)
    return bytes(b)


class Ev:
    def __init__(self, t, typ, **data):
        self.timestamp_ms, self.event_type, self.actor_id, self.data = t, typ, 1, data


class MacroAnalysisTest(unittest.TestCase):
    def test_userkeys_map_slots_and_row_one_defaults_to_f_keys(self):
        keys = A.parse_userkeys(USERKEYS)
        self.assertEqual((keys[0], keys[8], keys[9], keys[10], keys[13], keys[18], keys[35]),
                         ('F1', 'F9', 'Q', 'W', 'T', 'A', 'Space'))
        self.assertNotIn(24, keys)                                  # USERKEY_2(창 열기)는 스킬바가 아님

    def test_hotbar_packet_layout(self):
        self.assertEqual(len(bar_packet({})), 271)
        bar = A.hotbar([(0, bar_packet({13: (1, 5252, 3), 35: (0, 12887, 0)}))])
        self.assertEqual(bar, {13: dict(skill=True, id=5252, level=3), 35: dict(skill=False, id=12887, level=0)})
        self.assertEqual(A.slot_label(13), '스킬바 2-5')

    def test_measured_findings_only(self):
        # 실제 G HUB 매크로 '멸화격'(A, 클릭, T, 클릭 · 한 바퀴 350ms) 과 111.rrf 의 값 모양
        macro = dict(mode='hold', steps=[dict(type='tap', key='A'), dict(type='wait', ms=100), dict(type='click'),
                                         dict(type='wait', ms=100), dict(type='tap', key='T'), dict(type='wait', ms=100),
                                         dict(type='click'), dict(type='wait', ms=50)])
        keys = A.parse_userkeys(USERKEYS)
        bar = A.hotbar([(0, bar_packet({13: (1, 5252, 3), 18: (1, 6623, 5)}))])
        events = [Ev(i * 1000, 'SKILL_USE', skill=5252) for i in range(6)] + [Ev(10, 'SKILL_DELAY', skill=5252, delay_ms=1000)]
        events += [Ev(20, 'SKILL_FAIL', skill=6623, cause=4)]
        rows = {r['key']: r for r in A.analyze(macro, keys, bar, A.skill_stats(events, 1), lambda i: '스킬%d' % i)}
        t = rows['T']
        self.assertEqual((t['interval_ms'], t['cooldown_ms'], t['uses'], t['gap_ms']), (350, 1000, 6, 1000))
        self.assertIn('누름 3번에 1번꼴', t['verdict'])
        self.assertIn('실패 1번 / 사용 0번', rows['A']['verdict'])
        self.assertIn('기록이 없음', A.verdict(350, {}))


if __name__ == '__main__':
    unittest.main()
