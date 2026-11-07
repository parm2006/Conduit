import unittest
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from app.startup import configure_startup_task, task_is_owned, run_startup_helper, TASK_PREFIX


class StartupTests(unittest.TestCase):
    def helper_context(self):
        import win32com.client
        import win32com.shell.shell
        import win32security

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('app.startup.sys.frozen', True, create=True))
        stack.enter_context(patch('app.startup.sys.executable', r'C:\Program Files\Conduit\Conduit.exe'))
        stack.enter_context(patch('win32com.shell.shell.SHGetFolderPath', return_value=r'C:\Program Files'))
        stack.enter_context(patch('ctypes.windll.shell32.IsUserAnAdmin', return_value=1))
        stack.enter_context(patch('win32security.OpenProcessToken', return_value=MagicMock()))
        stack.enter_context(patch('win32security.GetTokenInformation', return_value=('sid',)))
        stack.enter_context(patch('win32security.ConvertSidToStringSid', return_value='S-1-5-21-123'))
        scheduler = MagicMock()
        stack.enter_context(patch('win32com.client.Dispatch', return_value=scheduler))
        registry = stack.enter_context(patch('winreg.CreateKeyEx'))
        stack.enter_context(patch('winreg.SetValueEx'))
        return scheduler, scheduler.GetFolder.return_value, registry

    def test_enable_and_manual_register_restricted_interactive_task(self):
        import pywintypes
        scheduler, folder, _registry = self.helper_context()
        # Schedule.Service wraps ERROR_FILE_NOT_FOUND in DISP_E_EXCEPTION.
        folder.GetTask.side_effect = pywintypes.com_error(
            -2147352567, 'Exception occurred', (0, None, None, None, 0, -2147024894), None
        )
        for operation, expected_triggers in [('enable', 1), ('manual', 0)]:
            scheduler.NewTask.return_value.reset_mock()
            self.assertEqual(run_startup_helper([operation]), 0)
            task = scheduler.NewTask.return_value
            self.assertEqual(task.Triggers.Create.call_count, expected_triggers)
            call = folder.RegisterTaskDefinition.call_args.args
            self.assertEqual(call[0], TASK_PREFIX + 'S-1-5-21-123')
            self.assertEqual(call[2:6], (6, 'S-1-5-21-123', None, 3))
            self.assertIn('(A;;GRGX;;;S-1-5-21-123)', call[6])
            self.assertNotIn('(A;;GA;;;S-1-5-21-123)', call[6])

    def test_remove_includes_hidden_tasks_and_preserves_unrelated_tasks(self):
        _scheduler, folder, _registry = self.helper_context()
        owned = MagicMock(Name=TASK_PREFIX + 'S-1-5-21-123')
        unrelated = MagicMock(Name='OtherTask')
        folder.GetTasks.return_value = [owned, unrelated]
        with patch('app.startup.task_is_owned', return_value=True):
            self.assertEqual(run_startup_helper(['remove']), 0)
        folder.GetTasks.assert_called_once_with(1)
        folder.DeleteTask.assert_called_once_with(owned.Name, 0)

    def test_refuses_foreign_task_and_reports_scheduler_failures(self):
        _scheduler, folder, _registry = self.helper_context()
        with patch('app.startup.task_is_owned', return_value=False):
            self.assertEqual(run_startup_helper(['manual']), 3)
        folder.RegisterTaskDefinition.assert_not_called()
        folder.GetTasks.side_effect = RuntimeError('scheduler unavailable')
        self.assertEqual(run_startup_helper(['remove']), 3)

    def test_refuses_source_portable_and_non_admin_registration(self):
        scheduler, _folder, _registry = self.helper_context()
        with patch('app.startup.sys.frozen', False):
            self.assertEqual(run_startup_helper(['enable']), 2)
        with patch('app.startup.sys.executable', r'C:\Downloads\Conduit.exe'):
            self.assertEqual(run_startup_helper(['enable']), 2)
        with patch('ctypes.windll.shell32.IsUserAnAdmin', return_value=0):
            self.assertEqual(run_startup_helper(['enable']), 2)
        self.assertEqual(run_startup_helper(['enable', '--program', 'other.exe']), 2)
        scheduler.Connect.assert_not_called()

    def test_manual_task_has_no_automatic_trigger(self):
        def unexpected_trigger(kind):
            self.fail('Manual launch must not register an automatic trigger')
        task = SimpleNamespace(
            RegistrationInfo=SimpleNamespace(), Principal=SimpleNamespace(),
            Settings=SimpleNamespace(), Triggers=SimpleNamespace(Create=unexpected_trigger),
            Actions=SimpleNamespace(Create=lambda kind: SimpleNamespace()),
        )
        configure_startup_task(task, r'C:\Program Files\Conduit\Conduit.exe', 'S-1-5-21-123', auto_start=False)

    def test_task_runs_only_at_this_users_logon_without_restart_or_time_limit(self):
        trigger = SimpleNamespace()
        action = SimpleNamespace()
        task = SimpleNamespace(
            RegistrationInfo=SimpleNamespace(), Principal=SimpleNamespace(),
            Settings=SimpleNamespace(),
            Triggers=SimpleNamespace(Create=lambda kind: trigger),
            Actions=SimpleNamespace(Create=lambda kind: action),
        )
        configure_startup_task(task, r'C:\Program Files\Conduit\Conduit.exe', 'S-1-5-21-123')
        self.assertEqual(task.Principal.UserId, 'S-1-5-21-123')
        self.assertEqual(task.Principal.LogonType, 3)  # Interactive token, no saved password
        self.assertEqual(task.Principal.RunLevel, 1)
        self.assertEqual(trigger.UserId, task.Principal.UserId)
        self.assertEqual(action.Path, r'C:\Program Files\Conduit\Conduit.exe')
        self.assertEqual(action.Arguments, '')
        self.assertEqual(task.Settings.ExecutionTimeLimit, 'PT0S')
        self.assertFalse(task.Settings.DisallowStartIfOnBatteries)
        self.assertFalse(task.Settings.StopIfGoingOnBatteries)
        self.assertEqual(task.Settings.MultipleInstances, 2)

    def test_cleanup_does_not_claim_tasks_for_other_executables_or_arguments(self):
        def task(path, arguments='', count=1):
            action = SimpleNamespace(Type=0, Path=path, Arguments=arguments)
            actions = SimpleNamespace(Count=count, Item=lambda index: action)
            return SimpleNamespace(Definition=SimpleNamespace(Actions=actions))
        exe = r'C:\Program Files\Conduit\Conduit.exe'
        self.assertTrue(task_is_owned(task(exe), exe))
        self.assertFalse(task_is_owned(task(r'C:\Other\Conduit.exe'), exe))
        self.assertFalse(task_is_owned(task(exe, '--unexpected'), exe))
        self.assertFalse(task_is_owned(task(exe, count=2), exe))


if __name__ == '__main__':
    unittest.main()
