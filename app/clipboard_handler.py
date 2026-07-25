import threading
import time
import logging
import win32clipboard

from app.safe_errors import error_name
from app.clipboard_formats import ClipboardPayloadError, decode_clipboard_message
from app.windows_clipboard import ClipboardAccessError, WindowsClipboardAdapter

logger = logging.getLogger(__name__)

class ClipboardHandler:
    def __init__(
        self,
        on_clipboard_change,
        on_file_availability=None,
        clipboard_adapter=None,
    ):
        self.on_clipboard_change = on_clipboard_change
        self.on_file_availability = on_file_availability
        self.clipboard_adapter = clipboard_adapter or WindowsClipboardAdapter()
        self.file_availability = None
        self._file_drop_format = win32clipboard.CF_HDROP
        self.last_sequence_num = 0
        self.is_running = False
        self.thread = None
        self.is_injecting = False
        self.last_sent_fingerprint = None
        self.last_injected_fingerprint = None

    def start(self):
        self.is_running = True
        try:
            self.last_sequence_num = win32clipboard.GetClipboardSequenceNumber()
        except:
            pass
        self._update_file_availability()
        self.thread = threading.Thread(target=self._poll_clipboard, daemon=True)
        self.thread.start()
        logger.info("Rich Clipboard polling started (Native Zlib Compression)")

    def stop(self):
        self.is_running = False
        self.wipe_clipboard()

    def wipe_clipboard(self):
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    logger.info("Clipboard securely wiped on disconnect")
                    break
                finally:
                    win32clipboard.CloseClipboard()
            except Exception as e:
                time.sleep(0.1)
        
    def inject(self, payload):
        try:
            snapshot = decode_clipboard_message(payload)
        except ClipboardPayloadError as error:
            logger.warning(
                "Remote clipboard snapshot rejected (%s)",
                error_name(error),
            )
            return False

        injected_fp = snapshot.fingerprint()
        self.last_injected_fingerprint = injected_fp
        self.is_injecting = True
        injected_sequence = None
        clipboard_updated = False
        publication_attempted = False
        try:
            for _ in range(5):
                try:
                    win32clipboard.OpenClipboard()
                except Exception as error:
                    logger.debug(
                        "Clipboard locked during inject, retrying (%s)",
                        error_name(error),
                    )
                    time.sleep(0.1)
                    continue
                try:
                    publication_attempted = True
                    try:
                        self.clipboard_adapter.publish_open_clipboard(snapshot)
                    except (ClipboardPayloadError, ClipboardAccessError) as error:
                        logger.warning(
                            "Remote clipboard publication failed (%s)",
                            error_name(error),
                        )
                        return False
                    clipboard_updated = True
                    try:
                        injected_sequence = (
                            win32clipboard.GetClipboardSequenceNumber()
                        )
                    except Exception:
                        pass
                finally:
                    win32clipboard.CloseClipboard()
                logger.info("Injected rich clipboard payload (with formatting)")
                break

            return clipboard_updated
        finally:
            try:
                if clipboard_updated:
                    # Record only DeskFlow's write. A user copy made while
                    # Windows settles must remain visible as a newer sequence.
                    time.sleep(0.1)
                    if injected_sequence is not None:
                        self.last_sequence_num = injected_sequence
                if publication_attempted:
                    self._update_file_availability()
            finally:
                self.is_injecting = False

    def _read_clipboard(self):
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                try:
                    return self.clipboard_adapter.capture_open_clipboard()
                finally:
                    win32clipboard.CloseClipboard()
            except ClipboardPayloadError as error:
                logger.warning(
                    "Local clipboard snapshot rejected (%s)",
                    error_name(error),
                )
                return None
            except ClipboardAccessError as error:
                logger.warning(
                    "Local clipboard capture failed (%s)",
                    error_name(error),
                )
                return None
            except Exception as error:
                logger.debug(
                    "Clipboard locked during read, retrying (%s)",
                    error_name(error),
                )
                time.sleep(0.1)
        return None

    def _update_file_availability(self):
        try:
            available = bool(
                win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP)
            )
        except Exception:
            available = False
        if available == self.file_availability:
            return
        self.file_availability = available
        if self.on_file_availability:
            self.on_file_availability(available)

    def read_file_selection(self):
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(self._file_drop_format):
                return ()
            paths = win32clipboard.GetClipboardData(self._file_drop_format)
            return tuple(paths)
        finally:
            win32clipboard.CloseClipboard()

    def _poll_clipboard(self):
        while self.is_running:
            try:
                if self.is_injecting:
                    time.sleep(0.1)
                    continue
                seq = win32clipboard.GetClipboardSequenceNumber()
                if seq != self.last_sequence_num:
                    self._process_clipboard_sequence(seq)
            except Exception as error:
                logger.error("Error in poll clipboard (%s)", error_name(error))
            time.sleep(0.5)

    def _process_clipboard_sequence(self, sequence):
        if sequence == self.last_sequence_num:
            return
        self.last_sequence_num = sequence
        self._update_file_availability()
        if self.file_availability:
            return
        snapshot = self._read_clipboard()
        if snapshot:
            captured_fp = snapshot.fingerprint()
            if captured_fp == self.last_injected_fingerprint:
                logger.info(
                    "Suppressing echo of injected clipboard snapshot (fingerprint %s)",
                    captured_fp[:8],
                )
                self.last_injected_fingerprint = None  # Single-use suppression clearing
                return
            logger.info("Local rich clipboard change detected, forwarding...")
            self.on_clipboard_change(snapshot)
