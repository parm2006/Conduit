import logging
import queue
import threading
import time

import pythoncom
from pynput.keyboard import Controller as KeyboardController, Key

from app.windows_virtual_files import (
    VirtualFile,
    VirtualFileSet,
    open_callback_stream,
    publish_virtual_files,
)
from app.safe_errors import error_name


logger = logging.getLogger(__name__)


def capture_clipboard_owner(get_clipboard=pythoncom.OleGetClipboard):
    try:
        return get_clipboard()
    except pythoncom.com_error:
        return None


def restore_virtual_clipboard_owner(
    owner,
    previous_owner,
    is_current=pythoncom.OleIsCurrentClipboard,
    restore=pythoncom.OleSetClipboard,
):
    if not is_current(owner):
        return False
    restore(previous_owner)
    return True


def release_virtual_clipboard_owner(
    owner,
    is_current=pythoncom.OleIsCurrentClipboard,
    clear=lambda: pythoncom.OleSetClipboard(None),
):
    if not is_current(owner):
        return False
    clear()
    return True


def inject_paste_shortcut(keyboard, ctrl_key=Key.ctrl, paste_key="v"):
    try:
        keyboard.press(ctrl_key)
        keyboard.press(paste_key)
    finally:
        try:
            keyboard.release(paste_key)
        finally:
            keyboard.release(ctrl_key)


def build_virtual_file_set(manifest, receiver, on_stream_open=None):
    job_id = manifest["job_id"]
    files = []
    for item in manifest["items"]:
        relative_path = item["relative_path"]
        if item["item_type"] == "directory":
            files.append(VirtualFile(relative_path, 0, None, is_directory=True))
            continue

        size = item["size"]

        def open_stream(path=relative_path, stream_size=size):
            def record_open():
                receiver.record_stream_open(job_id, path)
                if on_stream_open is not None:
                    on_stream_open()

            return open_callback_stream(
                lambda offset, count: receiver.read_range(
                    job_id, path, offset, count
                ),
                stream_size,
                on_read=lambda offset, count, path=path: receiver.record_stream_read(
                    job_id, path, offset, count
                ),
                on_open=record_open,
                on_close=lambda path=path: receiver.record_stream_close(job_id, path),
            )

        files.append(VirtualFile(relative_path, size, None, open_stream=open_stream))
    return VirtualFileSet(files)


class VirtualPastePublisher:
    def __init__(
        self,
        publish=None,
        inject=None,
        release=None,
        capture=None,
        restore=None,
        keyboard_factory=None,
        explorer_start_timeout=15.0,
    ):
        self._queue = queue.Queue()
        self._thread = None
        self._publish = publish or publish_virtual_files
        self._inject = inject or inject_paste_shortcut
        self._capture = capture or capture_clipboard_owner
        if restore is not None:
            self._restore = restore
            self._restore_after_accept = True
        elif release is not None:
            self._restore = lambda owner, previous: release(owner)
            self._restore_after_accept = False
        else:
            self._restore = restore_virtual_clipboard_owner
            self._restore_after_accept = True
        self._keyboard_factory = keyboard_factory or KeyboardController
        self.explorer_start_timeout = float(explorer_start_timeout)
        self._owner = None
        self._owner_lock = threading.Lock()
        self._idle = threading.Condition()
        self._pending = 0

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def publish_and_paste(self, manifest, receiver):
        self.start()
        with self._idle:
            self._pending += 1
        self._queue.put((manifest, receiver))

    def wait_until_idle(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._idle:
            while self._pending:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._idle.wait(remaining)
            return True

    @property
    def retained_owner_count(self):
        with self._owner_lock:
            return int(self._owner is not None)

    @property
    def has_pending_paste(self):
        with self._idle:
            return bool(self._pending)

    def _worker(self):
        pythoncom.OleInitialize()
        keyboard = self._keyboard_factory()
        try:
            while True:
                try:
                    manifest, receiver = self._queue.get(timeout=0.01)
                except queue.Empty:
                    pythoncom.PumpWaitingMessages()
                    continue
                try:
                    self._process(manifest, receiver, keyboard)
                except Exception as error:
                    self._report_failure(
                        receiver, manifest.get("job_id"), "ClipboardPublishFailed"
                    )
                    logger.error(
                        "Virtual paste worker failed (%s)", error_name(error)
                    )
                finally:
                    self._queue.task_done()
                    with self._idle:
                        self._pending -= 1
                        self._idle.notify_all()
        finally:
            pythoncom.CoUninitialize()

    def _process(self, manifest, receiver, keyboard):
        job_id = manifest["job_id"]
        consumed = threading.Event()
        previous_owner = self._capture()

        def performed_drop():
            consumed.set()
            return receiver.record_performed_drop(job_id)

        file_set = build_virtual_file_set(
            manifest, receiver, on_stream_open=consumed.set
        )
        owner = self._publish(file_set, on_performed_drop=performed_drop)
        with self._owner_lock:
            self._owner = owner
        accepted = False
        try:
            try:
                self._inject(keyboard)
            except Exception as error:
                self._report_failure(receiver, job_id, "PasteInjectionFailed")
                logger.error("Could not inject file paste (%s)", error_name(error))
                return False

            deadline = time.monotonic() + self.explorer_start_timeout
            while not consumed.is_set():
                pythoncom.PumpWaitingMessages()
                if receiver.is_paste_terminal(job_id):
                    return False
                if time.monotonic() >= deadline:
                    self._report_failure(receiver, job_id, "ExplorerStartTimeout")
                    return False
                consumed.wait(0.005)
            accepted = True
            return True
        finally:
            self._restore_owner(owner, previous_owner, retain=accepted)

    def _restore_owner(self, owner, previous_owner, retain=False):
        with self._owner_lock:
            if self._owner is not owner:
                return False
            if retain and not self._restore_after_accept:
                return True
            try:
                self._restore(owner, previous_owner)
            except Exception as error:
                logger.error(
                    "Could not restore clipboard after virtual paste (%s)",
                    error_name(error),
                )
            finally:
                if not retain:
                    self._owner = None
        return True

    @staticmethod
    def _report_failure(receiver, job_id, error_code):
        try:
            receiver.fail_paste(job_id, error_code)
        except Exception as error:
            logger.error(
                "Could not report virtual paste failure (%s)", error_name(error)
            )
