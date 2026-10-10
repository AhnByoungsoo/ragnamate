"""노점 개설 창 읽기: 사용자 실제 게임 스크린샷을 Windows OCR(2배)로 읽은 결과(tests/data/vend_ocr_lines.json)로 확인."""
import json
import os
import unittest
from types import SimpleNamespace

from helpers import ROOT  # noqa: F401  (sys.path 설정)

from core import vend_ocr as V
from services import gnjoy_market as G

HERE = os.path.dirname(os.path.abspath(__file__))


def lines():
    with open(os.path.join(HERE, 'data', 'vend_ocr_lines.json'), encoding='utf-8') as f:
        return [SimpleNamespace(text=d['text'], box=tuple(d['box'])) for d in json.load(f)]


NAMES = ['이블커스 이어링 쉐도우', '오버 크로스 이어링 쉐도우', '강인한 마력의 갑옷', '일루시온 레그 A타입', '일루시온 레그 B타입',
         '의상 클락 로드', '의상 클락 카스켓', '제네릭 스톤(하단)', '제네릭 스톤(상단)', '제네릭 스톤II(하단)', '성제 스톤(상단)',
         '성제 스톤(하단)', '제네릭 스톤(걸칠것)', '제네릭 스톤II(걸칠것)', '변동캐스팅 스톤(걸칠것)', '캐스팅스톤(걸칠것)',
         '진노의 하프', '진노의 라이플']


class VendOcrTest(unittest.TestCase):
    def test_title_and_region(self):
        self.assertEqual(V.find_title(lines())[:2], (39, 12))
        self.assertIsNone(V.find_title([SimpleNamespace(text='상점명', box=(0, 0, 5, 5))]))
        self.assertEqual(V.window_region((53, 38, 49, 11), 522, 579), (13, 26, 460, 553))

    def test_rows_prices_and_names(self):
        rows = V.parse_rows(lines())
        self.assertEqual([r.price for r in rows], [40000000, 90000000, 80000000, 40000000, 9999999, 20000000, 9000000,
                                                   6000000, 10000000])
        self.assertEqual(rows[1].real, 85500000)
        self.assertEqual([r.refine for r in rows[:4]], [10, 9, 9, None])
        self.assertIn('질것', rows[7].name_text)               # 두 줄로 나뉜 이름도 합친다
        m = V.NameMatcher(NAMES)
        got = [m.match(r.name_text)[0] for r in rows]
        self.assertEqual(got, ['이블커스 이어링 쉐도우', '강인한 마력의 갑옷', '일루시온 레그 B타입', '의상 클락 로드',
                               '제네릭 스톤(하단)', '성제 스톤(상단)', '제네릭 스톤(걸칠것)', '변동캐스팅 스톤(걸칠것)',
                               '진노의 하프'])
        for r, name in zip(rows, got):
            r.match = name
        self.assertEqual([r.comparable for r in rows], [False] * 3 + [True] * 6)   # 제련 장비는 비교 안 함

    def test_matcher_rejects_unclear(self):
        m = V.NameMatcher(['성제 스톤(상단)', '성제 스톤(하단)'])
        self.assertEqual(m.match('성제 스톤(')[0], '')                          # (상단)/(하단) 구분 불가 → 확정 안 함
        self.assertEqual(m.match('ㅋ')[0], '')
        self.assertEqual(V.NameMatcher(['진노의 하프'], preferred={'진노의 하프'}).match('[훼 진노의 하프')[0], '진노의 하프')

    def test_card_prefix_is_split_off(self):
        """카드가 박힌 장비는 이름 앞에 접두사가 붙는다 — 떼어 내고 본체를 찾되, 접두사는 따로 남긴다."""
        m = V.NameMatcher(['글레이시아 가드', '판화된 글레이시아 가드', '디비누스 슈즈 쉐도우'])
        name, _s, _c, prefix = m.match_prefixed('마술의 글레이시아 가드')
        self.assertEqual((name, prefix), ('글레이시아 가드', ''))      # 점수가 충분하면 통째로 맞춘다
        name, _s, _c, prefix = m.match_prefixed('보석세공된 지혜의 글레이시아 가드')
        self.assertEqual(name, '글레이시아 가드')
        self.assertTrue(prefix)                                       # 앞부분은 카드 접두사로 따로 둔다
        # 실제로 존재하는 접두사 이름은 그대로 맞춰야 한다(접두사로 오해하지 않음)
        self.assertEqual(m.match_prefixed('판화된 글레이시아 가드')[:1], ('판화된 글레이시아 가드',))

    def test_equipment_rows_are_compared_by_refine(self):
        row = V.VendRow('+12 글레이시아 가드', 1000)
        row.refine, row.match = 12, '글레이시아 가드'
        self.assertFalse(row.comparable)                              # 이름만으로 같은 아이템이라 보지 않는다
        self.assertTrue(row.equipment)                                # 같은 제련끼리는 견준다
        plain = V.VendRow('글레이시아 가드', 1000)
        plain.match = '글레이시아 가드'
        self.assertTrue(plain.comparable)
        self.assertFalse(plain.equipment)
        carded = V.VendRow('마술의 글레이시아 가드', 1000)
        carded.match, carded.prefix = '글레이시아 가드', '마술의'
        self.assertFalse(carded.comparable)
        self.assertTrue(carded.equipment)

    def test_shop_name(self):
        ns = SimpleNamespace
        self.assertEqual(V.find_shop([ns(text='。 6시', box=(0, 0, 1, 1)), ns(text='상점명 。 6시', box=(0, 0, 1, 1))]), '6시')
        self.assertEqual(V.find_shop([ns(text='노점 개설', box=(0, 0, 1, 1))]), '')
        # 1배 OCR: '상점명' 글자는 안 읽히고 제목 바로 아래 '。 6시'만 읽힘(실제 스크린샷)
        got = [ns(text='노점 개설', box=(53, 38, 49, 11)), ns(text='。 6시', box=(64, 59, 32, 12)),
               ns(text='+10 口l블커스', box=(76, 86, 107, 11))]
        self.assertEqual(V.find_shop(got, (53, 38, 49, 11)), '6시')

    def test_own_listing(self):
        own = dict(price=20000000, qty=1, shop='6시', kind='sale')
        self.assertTrue(V.is_own_listing(own, 20000000, '6사'))        # 상점명 OCR이 조금 틀려도
        self.assertTrue(V.is_own_listing(own, 20000000, ''))           # 상점명을 못 읽어도 같은 가격이면
        self.assertFalse(V.is_own_listing(own, 19000000, '6시'))       # 가격이 다르면 남의 노점
        self.assertFalse(V.is_own_listing(dict(own, shop='카드점'), 20000000, '6시'))
        self.assertFalse(V.is_own_listing(dict(own, kind='buy'), 20000000, '6시'))

    def test_change_detection(self):
        import numpy as np
        box = V.watch_box(lines())
        self.assertEqual(box[:2], (9, 8))                                  # 제목(39,12) 기준 창 안쪽
        self.assertGreater(box[3], 430)                                    # '※' 안내 줄까지
        a = np.full((553, 460, 3), 230, np.uint8)
        b = a.copy()
        b[343:355, 330:345] = 40                                          # 가격 숫자 두 글자만 바뀜(평균 변화 0.13)
        self.assertTrue(V.content_changed(a, b, box))
        c = a.copy()
        c[530:553, 0:460] = 0                                             # 창 아래 게임 배경만 바뀜 → 다시 읽지 않음
        self.assertFalse(V.content_changed(a, c, box))
        self.assertFalse(V.content_changed(a, a.copy(), box))
        self.assertTrue(V.content_changed(None, a, box))

    def test_twelve_rows_with_unpriced(self):
        """2026-09-27 스크린샷(12줄, 뒤 5줄은 아이템만 올리고 판매가 0) — 모든 줄을 읽는다."""
        with open(os.path.join(HERE, 'data', 'vend_ocr_lines_12.json'), encoding='utf-8') as f:
            d = json.load(f)
        ls = [SimpleNamespace(text=l['text'], box=tuple(l['box'])) for l in d['lines']]
        rows = V.parse_rows(ls)
        self.assertEqual(len(rows), 12)
        # 2번째 줄은 판매가·실거래가 숫자가 둘 다 깨짐('9.5叨.0叨') → None(감시기가 그 줄만 3배로 다시 읽음)
        self.assertEqual([r.price for r in rows], [100000, None, 3000000, 920000, 170000, 160000, 2000000, 0, 0, 0, 0, 0])
        names = ['에텔 스톤', '제네릭 스톤(하단)', '맘모스 이어링 쉐도우', '독약병 상자', '중화된 마력의 결정체',
                 '잠식된 마력의 결정체', '소울링커 스톤(중단)', '빙화 마석(스펠 버스터)', '앱솔브 쉐도우 마법서',
                 '트루 젬스톤 쉐도우 마법서', '올마이티 쉐도우 마법서', '스펠 캐스터 쉐도우 마법서']
        m = V.NameMatcher(names + ['빙화 마석(스펠 캐스터)', '올마이티 이어링 쉐도우', '앱솔브 이어링 쉐도우'])
        got = [m.best([r.name_text] + alts)[0] for r, alts in zip(rows, d['alts'])]
        self.assertEqual(got, names)
        self.assertEqual(V.name_box(rows[9], rows[10].y, 27), (47, 432, 240, 474))

    def test_amount_and_title_variants(self):
        self.assertEqual([V._amount(t) for t in ('9,500.OOO', '0', '', '9500.000', '3.側0.000', '기예0.000')],
                         [9500000, 0, 0, 9500000, None, None])
        self.assertEqual([V.price_from_real(r) for r in (2000000, 38000000, 180500000, None, 9975000)],
                         [2000000, 40000000, 190000000, None, None])      # 9,975,000 은 9,975,000/10,500,000 둘 다 가능
        self.assertEqual(V.find_title([SimpleNamespace(text='노점 개 촐', box=(28, 137, 49, 11))]), (28, 137, 49, 11))
        self.assertIsNone(V.find_title([SimpleNamespace(text='노점 개설 창 감지 - 바포메트', box=(0, 0, 1, 1))]))
        dlg = [SimpleNamespace(text='요르투스 집행관 카드 의 가격이 1억 9', box=(0, 0, 1, 1)),
               SimpleNamespace(text='입력금액이 맞습니까?', box=(0, 0, 1, 1))]
        self.assertTrue(V.find_dialog(dlg))
        self.assertFalse(V.find_dialog([SimpleNamespace(text='천만ZenyOl 상 안전 확인', box=(0, 0, 1, 1))]))

    def test_real_amount(self):
        self.assertEqual([V.real_amount(p) for p in (40000000, 90000000, 20000000, 10000000, 9999999, None)],
                         [38000000, 85500000, 19000000, 10000000, 9999999, None])      # 스크린샷 실거래가와 같음


class RecommendTest(unittest.TestCase):
    """2026-09-27 바포메트 실제 시세로 확인한 값."""
    def test_recommend_and_judge(self):
        cases = [(6000000, dict(now_min=15000000, avg7=11300428), 11300000, ('danger', '너무 쌈 -47%')),
                 (9999999, dict(now_min=8000000, avg7=13166666), 7920000, ('warn', '최저가보다 25% 비쌈')),
                 (20000000, dict(now_min=19000000, avg7=None), 18810000, ('warn', '최저가보다 5% 비쌈')),
                 (9000000, dict(now_min=9000000, avg7=10000000), 8910000, ('ok', '적정'))]
        for mine, cmp, rec, verdict in cases:
            self.assertEqual(G.recommend_price(cmp), rec)
            self.assertEqual(G.judge_price(mine, rec, cmp['now_min']), verdict)
        self.assertEqual(G.recommend_price(dict(now_min=None, avg7=None, avg30=5555555)), 5550000)   # 판매 노점 없음
        self.assertIsNone(G.recommend_price(dict(now_min=None, avg7=None)))
        self.assertEqual(G.recommend_price(dict(now_min=5000, avg7=None)), 4950)                   # 1만 미만은 그대로
        self.assertEqual(G.judge_price(None, 100), ('muted', ''))
        self.assertEqual(G.judge_price(120, 100), ('warn', '추천가보다 20% 비쌈'))


if __name__ == '__main__':
    unittest.main()
