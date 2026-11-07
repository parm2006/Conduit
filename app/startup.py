"""Installer-only, per-user elevated launch task. No restart/watchdog behavior."""

import os
from pathlib import Path
import sys


TASK_PREFIX = 'Conduit-Logon-'


def configure_startup_task(task, executable, user_sid, *, auto_start=True):
    task.RegistrationInfo.Description = 'Launch Conduit with approved administrator privileges.'
    task.Principal.UserId = user_sid
    task.Principal.LogonType = 3  # TASK_LOGON_INTERACTIVE_TOKEN
    task.Principal.RunLevel = 1  # TASK_RUNLEVEL_HIGHEST
    task.Settings.ExecutionTimeLimit = 'PT0S'
    task.Settings.DisallowStartIfOnBatteries = False
    task.Settings.StopIfGoingOnBatteries = False
    task.Settings.MultipleInstances = 2  # TASK_INSTANCES_IGNORE_NEW
    if auto_start:
        trigger = task.Triggers.Create(9)  # TASK_TRIGGER_LOGON
        trigger.UserId = user_sid
    action = task.Actions.Create(0)  # TASK_ACTION_EXEC
    action.Path = executable
    action.Arguments = ''
    action.WorkingDirectory = str(Path(executable).parent)


def task_is_owned(task, executable):
    actions = task.Definition.Actions
    if actions.Count != 1:
        return False
    action = actions.Item(1)
    return (
        action.Type == 0
        and os.path.normcase(action.Path) == os.path.normcase(executable)
        and not action.Arguments
    )


def run_startup_helper(arguments):
    if arguments not in (['enable'], ['manual'], ['remove']):
        return 2
    # An elevated task may only execute the protected installed binary, never
    # a downloaded portable copy or a Python script in a user-writable checkout.
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return 2
    import ctypes
    import win32api
    import win32com.client
    import win32security
    import pywintypes
    from win32com.shell import shell, shellcon

    executable = str(Path(sys.executable).resolve())
    expected = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAM_FILES, 0, 0)) / 'Conduit' / 'Conduit.exe'
    if Path(executable) != expected.resolve() or not ctypes.windll.shell32.IsUserAnAdmin():
        return 2
    try:
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
        finally:
            token.Close()
        scheduler = win32com.client.Dispatch('Schedule.Service')
        scheduler.Connect()
        folder = scheduler.GetFolder('\\')
        name = TASK_PREFIX + sid
        if arguments == ['remove']:
            # Uninstall removes only Conduit's exact tasks, including those
            # registered by another admin on this installation.
            for task in list(folder.GetTasks(1)):  # TASK_ENUM_HIDDEN
                if task.Name.startswith(TASK_PREFIX) and task_is_owned(task, executable):
                    folder.DeleteTask(task.Name, 0)
            return 0
        try:
            existing = folder.GetTask(name)
        except pywintypes.com_error as error:
            if error.hresult != -2147024894:  # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
                raise
            existing = None
        if existing is not None and not task_is_owned(existing, executable):
            return 3  # Never overwrite an unrelated task with a matching name.
        task = scheduler.NewTask(0)
        configure_startup_task(task, executable, sid, auto_start=arguments == ['enable'])
        # Only administrators/SYSTEM may change the elevated command. The
        # signed-in user may read/run it, without gaining task-edit permission.
        security = f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;{sid})'
        folder.RegisterTaskDefinition(name, task, 6, sid, None, 3, security)
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE, r'Software\Conduit',
            access=winreg.KEY_SET_VALUE | winreg.KEY_WOW64_32KEY,
        ) as key:
            winreg.SetValueEx(key, 'LaunchTaskName', 0, winreg.REG_SZ, name)
        return 0
    except Exception:
        return 3
