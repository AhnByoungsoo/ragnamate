"""메모리얼 던전 창 읽기: 실제 화면에서 읽힌 글자로 순위·입장 가능을 가르고, 두 번 같게 읽혀야 받아들인다."""
import unittest

import helpers  # noqa: F401  (sys.path 설정)

from live.queue_screen import QueueScreenWatcher, parse  # noqa: E402

# 2026-10-18 실제 게임 창(배럭, 맨 앞 아님)에서 Windows OCR 이 읽은 그대로
WAITING = ['메모리얼 던전', 'CH기 순위 : 13']
READY = ['메모리얼 던전', '[바곳의 연구실] 입장 가통', '10월 18일 09시 56분 내메 입장하', '던전 파괴']


class ParseTest(unittest.TestCase):
    def test_real_screen_text(self):
        self.assertEqual(parse(WAITING), dict(position=13, ready=False, name=''))
        self.assertEqual(parse(READY), dict(position=0, ready=True, name='바곳의 연구실'))
        self.assertIsNone(parse(['메모리얼 던전', '던전 파괴']))


class FakeOcr:
    def __init__(self):
        self.full, self.small, self.calls = [], [], []

    def recognize(self, img, scale=1.0):
        from live.ocr import OcrLine
        self.calls.append(scale)
        return [OcrLine(t, 1.0, (65, 450, 74, 12)) for t in (self.full if scale == 1.5 else self.small)]


class ObserveTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.ocr = FakeOcr()
        self.w = QueueScreenWatcher(lambda h, s: None, ocr=self.ocr)
        self.img = np.zeros((993, 1920, 3), np.uint8)

    def test_rank_needs_two_equal_reads_and_ready_is_immediate(self):
        self.ocr.full, self.ocr.small = ['메모리얼 던전'], WAITING
        self.assertIsNone(self.w.observe(1, self.img, now=0))          # 한 번 읽음
        self.assertEqual(self.w.observe(1, self.img, now=0.5)['position'], 13)
        self.ocr.small = ['메모리얼 던전', 'CH기 순위 : 81']             # 한 번 잘못 읽음 → 그대로 13
        self.assertEqual(self.w.observe(1, self.img, now=1)['position'], 13)
        self.ocr.small = READY
        self.assertTrue(self.w.observe(1, self.img, now=1.5)['ready'])
        self.assertEqual(self.ocr.calls.count(1.5), 1)                    # 창 전체 찾기는 처음 한 번

    def test_window_closed_clears_the_state(self):
        self.ocr.full, self.ocr.small = ['메모리얼 던전'], READY
        self.assertTrue(self.w.observe(1, self.img, now=0)['ready'])
        self.ocr.full, self.ocr.small = [], []
        self.assertTrue(self.w.observe(1, self.img, now=1)['ready'])      # 아직 다시 찾을 때가 아님
        self.assertIsNone(self.w.observe(1, self.img, now=9))             # 다시 찾아도 없음 → 닫힘


if __name__ == '__main__':
    unittest.main()
