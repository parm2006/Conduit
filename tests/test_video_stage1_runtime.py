import os
from pathlib import Path
import subprocess
import sys
import unittest

class Stage1RuntimeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows desktop required')
    def _exercise_mode(self, mode):
        # Bound the whole GPU exercise: a native startup deadlock must fail rather
        # than hanging unittest or leaving a mapped DLL in the main test process.
        root = Path(__file__).resolve().parents[1]
        script = r'''
import os, socket, threading, time
import win32con, win32gui
from app.native_streamer import NativeStreamerReceiver, NativeStreamerSender
mode = os.environ['CONDUIT_STAGE1_TEST_MODE']
os.environ['CONDUIT_VIDEO_STAGE1'] = mode
wc = win32gui.WNDCLASS(); wc.lpszClassName = 'ConduitStage1Lifecycle'
wc.lpfnWndProc = win32gui.DefWindowProc
win32gui.RegisterClass(wc)
hwnd = win32gui.CreateWindowEx(0, wc.lpszClassName, 'stage1 test', win32con.WS_OVERLAPPEDWINDOW, 0,0,640,480,0,0,0,None)
events = []
receiver = NativeStreamerReceiver(hwnd, on_event=lambda code, message: events.append(code))
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
errors = []
def run():
    sender = None
    try:
        assert receiver.start(port, b'T' * 32), 'receiver startup failed'
        assert receiver.resize(800, 600)
        sender = NativeStreamerSender()
        assert sender.start(0, '127.0.0.1', port, b'T' * 32, 5000, 30), 'sender startup failed'
        deadline = time.monotonic() + 3
        while receiver.get_frame_count() == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        if mode in ('receiver', 'full'):
            assert 'decode call' in receiver.get_stats(), receiver.get_stats()
        assert 'sender mode=' in sender.get_stats(), sender.get_stats()
        # The controlled lifecycle is required regardless of interactive capture availability.
        initial_count = receiver.get_frame_count()
        idle_deadline = time.monotonic() + 2
        while receiver.get_frame_count() < initial_count + 3 and time.monotonic() < idle_deadline:
            time.sleep(.01)
        assert receiver.get_frame_count() >= initial_count + 3, 'idle keepalive failed'
        print(mode, 'presented frames:', receiver.get_frame_count())
        assert receiver.get_frame_count() > 0, receiver.get_stats()
        assert sender.get_frame_count() > 0, sender.get_stats()
        assert 3 in events, events
        print(receiver.get_stats())
        print(sender.get_stats())
    except BaseException as error:
        errors.append(str(error))
    finally:
        if sender:
            sender.stop(); sender.destroy()
        receiver.stop(); receiver.destroy()
worker = threading.Thread(target=run)
worker.start()
deadline = time.monotonic() + 15
while worker.is_alive() and time.monotonic() < deadline:
    win32gui.PumpWaitingMessages(); worker.join(.005)
assert not worker.is_alive(), 'native stop/start did not finish'
win32gui.DestroyWindow(hwnd)
assert not errors, errors
'''
        environment = dict(os.environ, CONDUIT_STAGE1_TEST_MODE=mode)
        tested = subprocess.run([sys.executable, '-u', '-c', script], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
        self.assertIn('presented frames:', tested.stdout)
        print(tested.stdout)

    def test_all_modes_deliver_frames_and_clean_up(self):
        if os.name != 'nt':
            self.skipTest('Windows desktop required')
        for mode in ('baseline', 'decoder', 'sender', 'receiver', 'full'):
            with self.subTest(mode=mode):
                self._exercise_mode(mode)
