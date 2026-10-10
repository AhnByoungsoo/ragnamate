"""LIVE 상태 모니터 수명 주기 테스트 (Qt·게임 없이)."""
import unittest

from helpers import ROOT  # noqa: F401  (sys.path 설정)

from live.status import (ACTIVE, CRITICAL, EXPIRED_ALERT, EXPIRING, HIDDEN, ITEM_BUFF, SKILL_BUFF, StatusMonitor)


class Settings(dict):
    def save(self):
        pass


def monitor(**over):
    s = Settings(status_warn_sec=60, status_critical_sec=10, status_reuse_sec=60, status_max_items=12)
    s.update(over)
    fired = []
    m = StatusMonitor(s, alert=lambda key, stage, text: fired.append((key, stage, text)))
    m.fired = fired
    return m


def row(key='b1', name='올마이티(전 능력치)', remaining=600, total=600, source='REPLAY', **kw):
    return dict(key=key, name=name, remaining=remaining, total=total, source=source,
                monitor_source=kw.pop('monitor_source', ITEM_BUFF), **kw)


class LifecycleTest(unittest.TestCase):
    def test_active_to_reuse_to_hidden(self):
        m = monitor()
        self.assertEqual(m.sync([row(remaining=600)], now=0)[0].state, ACTIVE)
        self.assertEqual(m.sync([row(remaining=45)], now=10)[0].state, EXPIRING)
        self.assertEqual(m.sync([row(remaining=8)], now=20)[0].state, CRITICAL)
        gone = m.sync([], now=30)                       # 목록에서 사라짐 = 효과 종료
        self.assertEqual(gone[0].state, EXPIRED_ALERT)
        self.assertEqual(gone[0].label, '재사용!!')
        self.assertEqual(round(gone[0].remaining), 60)  # 재사용 알림 남은 시간
        self.assertEqual(round(m.tick(now=75)[0].remaining), 15)
        self.assertEqual(m.tick(now=95), [])            # 60초 지나면 사라진다
        self.assertEqual(m.items, {})

    def test_refresh_clears_reuse_alert(self):
        m = monitor()
        m.sync([row(remaining=5)], now=0)
        m.sync([], now=10)
        self.assertEqual(m.items['b1'].state, EXPIRED_ALERT)
        again = m.sync([row(remaining=600)], now=20)    # 다시 걸었다
        self.assertEqual(again[0].state, ACTIVE)
        self.assertEqual(again[0].remaining, 600)

    def test_alerts_once_per_stage(self):
        m = monitor()
        m.sync([row(remaining=600)], now=0)
        m.sync([row(remaining=40)], now=10)
        m.sync([row(remaining=38)], now=12)
        m.sync([row(remaining=5)], now=20)
        m.sync([], now=30)
        stages = [f[1] for f in m.fired]
        self.assertEqual(stages, [EXPIRING, CRITICAL, EXPIRED_ALERT])
        self.assertIn('재사용', m.fired[-1][2])

    def test_alert_can_be_disabled_per_buff(self):
        m = monitor(status_alerts={'b1': dict(warn=False, critical=False, reuse=True)})
        m.sync([row(remaining=30)], now=0)
        m.sync([], now=10)
        self.assertEqual([f[1] for f in m.fired], [EXPIRED_ALERT])

    def test_unknown_duration_stays_active(self):
        """남은 시간을 모르는 효과는 숫자를 만들어내지 않고 그대로 둔다."""
        m = monitor()
        rows = m.sync([row(remaining=None, total=None)], now=0)
        self.assertEqual(rows[0].state, ACTIVE)
        self.assertIsNone(rows[0].remaining)
        self.assertIsNone(rows[0].ratio)


class OrderingTest(unittest.TestCase):
    def test_favorites_and_reuse_first(self):
        m = monitor(status_favorites=['fav'])
        m.sync([row('fav', '즐겨찾기', remaining=900), row('b1', '곧 끝남', remaining=20),
                row('b2', '보통', remaining=300)], now=0)
        rows = m.tick(now=0)
        self.assertEqual([r.key for r in rows], ['fav', 'b1', 'b2'])
        m.sync([row('fav', '즐겨찾기', remaining=900), row('b2', '보통', remaining=300)], now=1)
        rows = m.tick(now=1)
        self.assertEqual(rows[1].key, 'b1')             # 재사용 알림이 일반 항목보다 앞

    def test_internal_hidden_by_default(self):
        m = monitor()
        rows = m.sync([row('b1', '버프'), row('mail', '읽지 않은 우편', internal=True)], now=0)
        self.assertEqual([r.key for r in m.visible(rows)], ['b1'])
        self.assertEqual(len(m.visible(rows, show_internal=True)), 2)

    def test_favorite_toggle_saved(self):
        m = monitor()
        m.sync([row('b1')], now=0)
        self.assertTrue(m.toggle_favorite('b1'))
        self.assertEqual(m.s['status_favorites'], ['b1'])
        self.assertFalse(m.toggle_favorite('b1'))


class SkillBuffTest(unittest.TestCase):
    def test_same_component_for_skill_and_item(self):
        m = monitor()
        rows = m.sync([row('item', '올마이티', monitor_source=ITEM_BUFF),
                       row('skill', '블레싱', monitor_source=SKILL_BUFF, source='MANUAL')], now=0)
        kinds = {r.key: (r.source, r.origin) for r in rows}
        self.assertEqual(kinds['item'], (ITEM_BUFF, 'REPLAY'))
        self.assertEqual(kinds['skill'], (SKILL_BUFF, 'MANUAL'))
        for r in rows:                                   # 같은 컴포넌트가 둘 다 처리
            self.assertEqual(r.state, ACTIVE)
            self.assertIn('color', r.to_row())


if __name__ == '__main__':
    unittest.main()
