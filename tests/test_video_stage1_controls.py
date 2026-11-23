import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.native_streamer import NativeStreamerReceiver, NativeStreamerSender, _stage1_mode


class VideoStage1ControlTests(unittest.TestCase):
    def wrapper(self, cls, dll, handle=1):
        wrapper = cls.__new__(cls)
        wrapper._dll = dll
        wrapper._handle = handle
        self.addCleanup(setattr, wrapper, '_handle', None)
        return wrapper

    def test_decoder_mode_is_reported_without_changing_environment(self):
        import os
        with patch.dict(os.environ, {'CONDUIT_VIDEO_STAGE1': 'Decoder'}):
            self.assertEqual(_stage1_mode(), 'decoder')
            self.assertEqual(os.environ['CONDUIT_VIDEO_STAGE1'], 'Decoder')

    def test_stats_are_optional_for_old_dll(self):
        for cls in (NativeStreamerSender, NativeStreamerReceiver):
            with self.subTest(cls=cls):
                self.assertEqual(self.wrapper(cls, SimpleNamespace()).get_stats(), '')

    def test_stats_decode_text_from_native_buffer(self):
        def read(handle, buffer, size):
            self.assertEqual(handle, 123)
            data = b'presented=42\nqueue p95=1.25ms\n'
            ctypes.memmove(buffer, data + b'\0', len(data) + 1)
            return len(data)
        for cls, name in ((NativeStreamerSender, 'streamer_sender_get_stats'),
                          (NativeStreamerReceiver, 'streamer_receiver_get_stats')):
            with self.subTest(cls=cls):
                wrapper = self.wrapper(cls, SimpleNamespace(**{name: read}), 123)
                self.assertEqual(wrapper.get_stats(), 'presented=42\nqueue p95=1.25ms\n')

    def test_stats_failure_or_destroyed_handle_returns_empty(self):
        for cls, name in ((NativeStreamerSender, 'streamer_sender_get_stats'),
                          (NativeStreamerReceiver, 'streamer_receiver_get_stats')):
            with self.subTest(cls=cls):
                self.assertEqual(self.wrapper(cls, SimpleNamespace(**{name: lambda *args: 0})).get_stats(), '')
                self.assertEqual(self.wrapper(cls, SimpleNamespace(), None).get_stats(), '')

    def test_stop_logs_stats_without_changing_native_stop_call(self):
        dll = SimpleNamespace(streamer_receiver_stop=Mock())
        wrapper = self.wrapper(NativeStreamerReceiver, dll)
        with patch.object(wrapper, 'get_stats', return_value='presented=3'), self.assertLogs('app.native_streamer', level='INFO') as logs:
            wrapper.stop()
        dll.streamer_receiver_stop.assert_called_once_with(1)
        self.assertIn('presented=3', '\n'.join(logs.output))

    def test_launcher_defaults_to_baseline_and_does_not_install_dependencies(self):
        source = (Path(__file__).resolve().parents[1] / 'scripts' / 'run_video_stage1.ps1').read_text(encoding='utf-8')
        self.assertIn('[string]$Mode = "Baseline"', source)
        self.assertIn('CONDUIT_VIDEO_STAGE1', source)
        self.assertNotIn('pip install', source)

    def test_launcher_scopes_mode_and_restores_environment_on_success_and_failure(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        if os.name != 'nt':
            self.skipTest('Windows PowerShell launcher')
        root = Path(__file__).resolve().parents[1]
        def literal(value):
            return "'" + str(value).replace("'", "''") + "'"
        for exit_code, mode in ((0, 'Full'), (0, 'Decoder'), (9, 'Full')):
            with self.subTest(exit_code=exit_code, mode=mode), tempfile.TemporaryDirectory() as logdir:
                command = r'''
$env:CONDUIT_VIDEO_STAGE1 = 'previous-mode'
$global:Observed = $null
$global:Disposed = $false
function global:Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle,
          $RedirectStandardOutput, $RedirectStandardError, [switch]$PassThru)
    $global:Observed = @{
        mode=$env:CONDUIT_VIDEO_STAGE1; path=$FilePath;
        args=$ArgumentList; cwd=$WorkingDirectory; window=$WindowStyle;
        stdout=$RedirectStandardOutput; stderr=$RedirectStandardError
    }
    $fake = [pscustomobject]@{ ExitCode=EXIT_CODE }
    $fake | Add-Member ScriptMethod WaitForExit { }
    $fake | Add-Member ScriptMethod Dispose { $global:Disposed = $true }
    return $fake
}
$failed = $false
try { & LAUNCHER -Mode MODE -PythonPath PYTHON -LogDirectory LOGDIR }
catch { $failed = $true }
@{launch=$global:Observed; restored=$env:CONDUIT_VIDEO_STAGE1;
  failed=$failed; disposed=$global:Disposed} | ConvertTo-Json -Depth 5 -Compress
'''.replace('EXIT_CODE', str(exit_code)).replace('LAUNCHER', literal(root / 'scripts' / 'run_video_stage1.ps1')).replace('PYTHON', literal(sys.executable)).replace('LOGDIR', literal(logdir)).replace('MODE', mode)
                completed = subprocess.run(['powershell', '-NoProfile', '-Command', command],
                    text=True, capture_output=True, timeout=20)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout.strip().splitlines()[-1])
                self.assertEqual(result['launch']['mode'], mode.lower())
                self.assertEqual(result['launch']['cwd'], str(root))
                self.assertEqual(result['launch']['window'], 'Hidden')
                self.assertTrue(result['launch']['stdout'].endswith('.stdout.log'))
                self.assertTrue(result['launch']['stderr'].endswith('.stderr.log'))
                self.assertEqual(result['restored'], 'previous-mode')
                self.assertEqual(result['failed'], exit_code != 0)
                self.assertTrue(result['disposed'])
