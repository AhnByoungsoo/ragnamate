"""업데이트: 진짜 git 저장소 두 개(서버 역할·내 기기 역할)로 확인·받기, 버튼이 묻고 받는 흐름."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (sys.path 설정)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from services import updater  # noqa: E402
from ui import update_controller as UC  # noqa: E402

APP = QApplication.instance() or QApplication([])


def git(cwd, *args):
    subprocess.run(('git', '-c', 'user.name=t', '-c', 'user.email=t@t') + args, cwd=cwd, check=True, capture_output=True)


def write(folder, name, text):
    with open(os.path.join(folder, name), 'w', encoding='utf-8') as f:
        f.write(text)


@unittest.skipUnless(shutil.which('git'), 'git 없음')
class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.server, self.mine = os.path.join(self.tmp.name, 'server'), os.path.join(self.tmp.name, 'mine')
        os.mkdir(self.server)
        git(self.server, 'init', '-q', '-b', 'main')
        write(self.server, 'app.py', 'v1')
        write(self.server, '.gitignore', 'settings.json\n')
        git(self.server, 'add', '.')
        git(self.server, 'commit', '-q', '-m', '처음')
        git(self.tmp.name, 'clone', '-q', self.server, self.mine)

    def publish(self, text, subject):
        write(self.server, 'app.py', text)
        git(self.server, 'commit', '-q', '-am', subject)

    def test_folder_without_git_has_no_update(self):
        self.assertFalse(updater.available(self.tmp.name))
        self.assertTrue(updater.available(self.mine))

    def test_check_lists_new_changes_and_apply_takes_them(self):
        self.assertEqual(updater.check(self.mine), [])
        self.publish('v2', '둘째')
        self.publish('v3', '셋째')
        self.assertEqual(updater.check(self.mine), ['셋째', '둘째'])
        write(self.mine, 'settings.json', '내 설정')                 # git에 없는 기기별 파일
        updater.apply(self.mine, pip=False)
        with open(os.path.join(self.mine, 'app.py')) as f:
            self.assertEqual(f.read(), 'v3')
        with open(os.path.join(self.mine, 'settings.json'), encoding='utf-8') as f:
            self.assertEqual(f.read(), '내 설정')
        self.assertEqual(updater.check(self.mine), [])

    def test_unreachable_server_raises(self):
        git(self.mine, 'remote', 'set-url', 'origin', os.path.join(self.tmp.name, '없음'))
        with self.assertRaises(updater.UpdateError):
            updater.check(self.mine)


class ControllerTest(unittest.TestCase):
    def controller(self, changes, answer=QMessageBox.Yes):
        self.asked, self.told = [], []
        for name, target in (('question', lambda *a, **k: (self.asked.append(a[2]), answer)[1]),
                             ('information', lambda *a, **k: self.told.append(a[2])),
                             ('warning', lambda *a, **k: self.told.append(a[2]))):
            p = mock.patch.object(QMessageBox, name, staticmethod(target))
            p.start()
            self.addCleanup(p.stop)
        fake = mock.Mock(available=lambda root: True, check=mock.Mock(side_effect=changes), apply=mock.Mock(return_value='abc1234'))
        p = mock.patch.object(UC, 'updater', fake)
        p.start()
        self.addCleanup(p.stop)
        self.window = QWidget()
        c = UC.UpdateController(self.window, 'root')
        c.button.setParent(self.window)
        self.window.show()
        c._background = lambda fn, signal, *extra: signal.emit(self.result(fn), *extra)   # 같은 스레드에서 바로
        c.restart = mock.Mock()
        return c, fake

    @staticmethod
    def result(fn):
        try:
            return fn('root')
        except Exception as e:
            return e

    def test_startup_check_asks_then_updates_and_restarts(self):
        c, fake = self.controller([['버그 수정', '새 기능']])
        c.check()
        self.assertIn('업데이트 하겠습니까?', self.asked[0])
        self.assertIn('· 버그 수정', self.asked[0])
        fake.apply.assert_called_once_with('root')
        c.restart.assert_called_once()

    def test_no_keeps_the_button_marked(self):
        c, fake = self.controller([['버그 수정']], answer=QMessageBox.No)
        c.check()
        fake.apply.assert_not_called()
        self.assertEqual(c.button.text(), UC.FOUND)

    def test_latest_is_silent_at_startup_and_said_when_clicked(self):
        c, _ = self.controller([[], []])
        c.check()
        self.assertEqual((self.asked, self.told), ([], []))
        c.check(manual=True)
        self.assertEqual(self.told, ['지금 버전이 최신입니다.'])

    def test_failed_startup_check_is_silent(self):
        c, _ = self.controller(updater.UpdateError('오프라인'))
        c.check()
        self.assertEqual((self.told, c.button.text()), ([], UC.IDLE))

    def test_failed_update_keeps_running(self):
        c, fake = self.controller([['버그 수정']])
        fake.apply.side_effect = updater.UpdateError('충돌')
        c.check()
        c.restart.assert_not_called()
        self.assertIn('충돌', self.told[0])


if __name__ == '__main__':
    unittest.main()
