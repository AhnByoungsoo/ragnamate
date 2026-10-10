import os
import subprocess
import unittest
from unittest.mock import patch
import helpers
from core import elevation as E


class ElevationTest(unittest.TestCase):
    def test_already_admin_stays_in_process(self):
        with patch.object(E,'is_admin',return_value=True),patch.object(E,'launch_admin') as launch:
            self.assertIsNone(E.ensure_gui_admin('main.py',attempted=True))
            launch.assert_not_called()

    def test_export_needs_no_admin(self):
        with patch.object(E,'is_admin') as check:
            self.assertIsNone(E.ensure_gui_admin('main.py',export=True))
            check.assert_not_called()

    def test_parent_exits_after_request(self):
        with patch.object(E,'is_admin',return_value=False),patch.object(E,'launch_admin',return_value=True) as launch:
            self.assertEqual(E.ensure_gui_admin('main.py','file.rrf'),0)
            launch.assert_called_once_with('main.py','file.rrf')

    def test_cancel_does_not_start_normal_gui(self):
        with patch.object(E,'is_admin',return_value=False),patch.object(E,'launch_admin',return_value=False),patch.object(E,'show_error') as error:
            self.assertEqual(E.ensure_gui_admin('main.py'),1)
            error.assert_called_once()

    def test_failed_elevation_does_not_loop(self):
        with patch.object(E,'is_admin',return_value=False),patch.object(E,'launch_admin') as launch,patch.object(E,'show_error'):
            self.assertEqual(E.ensure_gui_admin('main.py',attempted=True),1)
            launch.assert_not_called()

    def test_unicode_paths_and_replay_quoting_without_real_uac(self):
        with patch.object(E.sys,'executable',r'C:\프로그램 폴더\python.exe'),patch.object(E.os.path,'isfile',return_value=True), \
             patch.object(E.ctypes.windll.shell32,'ShellExecuteW',return_value=33) as execute:
            script=r'C:\프로젝트 폴더\main.py'
            replay=r'C:\리플레이 폴더\인퀴.rrf'
            self.assertTrue(E.launch_admin(script,replay))
            args=execute.call_args.args
            self.assertEqual(args[1],'runas')
            self.assertEqual(args[2],r'C:\프로그램 폴더\pythonw.exe')
            self.assertEqual(args[3],subprocess.list2cmdline([script,E.MARKER,replay]))
            self.assertEqual(args[4],r'C:\프로젝트 폴더')

    def test_main_exits_before_logging_and_data_access(self):
        import main
        with patch.object(main.sys,'argv',['main.py']),patch.object(E,'ensure_gui_admin',return_value=0), \
             patch.object(main,'setup_logging') as setup,patch.object(main,'Settings') as settings:
            self.assertEqual(main.main(),0)
            setup.assert_not_called()
            settings.assert_not_called()
