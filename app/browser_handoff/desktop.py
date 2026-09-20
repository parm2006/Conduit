"""Desktop-side lifecycle for authenticated local browser connections."""

import logging
import threading
import time

from .local_bridge import DesktopBridge
from .window_match import BrowserWindowCandidate


SNAPSHOT_RETENTION_SECONDS = 5.0
MAX_RETAINED_SNAPSHOTS = 8
logger = logging.getLogger(__name__)


class BrowserHandoffDesktop:
    """Keep URL-free metadata and bridge connections in memory for one app run."""

    def __init__(
        self, *, start_bridge=True, identity_verifier=None,
        on_capability=None, on_result=None, on_snapshot=None,
    ):
        self._lock = threading.RLock()
        self._connections = {}
        self._metadata = {}
        self._metadata_received_at = {}
        self._snapshots = {}
        self._refreshes = {}
        self.on_capability = on_capability or (lambda instance, epoch: None)
        self.on_result = on_result or (lambda instance, message: None)
        self.on_snapshot = on_snapshot or (lambda instance, message: None)
        self.bridge = DesktopBridge(
            on_message=self.on_message,
            identity_verifier=identity_verifier or verify_native_host_parent,
            on_connected=self.on_connected,
            on_disconnected=self.on_disconnected,
        )
        self._started = False
        if start_bridge:
            self.start()

    def start(self):
        with self._lock:
            if self._started:
                return False
            self._started = self.bridge.start()
            return self._started

    def stop(self):
        with self._lock:
            self._started = False
            self._connections.clear()
            self._metadata.clear()
            self._metadata_received_at.clear()
            self._snapshots.clear()
            self._refreshes.clear()
        self.bridge.stop()

    def on_connected(self, connection):
        instance = connection.hello.browser_instance_id
        with self._lock:
            previous = self._connections.get(instance)
            if previous is not None and previous is not connection:
                previous.close()
                self._metadata.pop(instance, None)
                self._metadata_received_at.pop(instance, None)
            self._connections[instance] = connection
        logger.info("browser_handoff stage=bridge_connected instance=%s", instance)

    def on_disconnected(self, connection):
        instance = connection.hello.browser_instance_id
        with self._lock:
            if self._connections.get(instance) is connection:
                self._connections.pop(instance, None)
                self._metadata.pop(instance, None)
                self._metadata_received_at.pop(instance, None)
                for key in tuple(self._snapshots):
                    if key[0] == instance:
                        self._snapshots.pop(key, None)
        logger.info("browser_handoff stage=bridge_disconnected instance=%s", instance)

    def on_message(self, connection, message):
        if type(message) is not dict:
            return False
        instance = connection.hello.browser_instance_id
        if message.get("type") == "browser_handoff_metadata":
            if message.get("browser_instance_id") != instance or type(message.get("windows")) is not list:
                return False
            # Metadata is intentionally URL-free; reject rather than strip a
            # malformed extension message so no browsing data enters memory.
            if "url" in message or any(type(window) is not dict or "url" in window for window in message["windows"]):
                return False
            with self._lock:
                if self._connections.get(instance) is not connection:
                    return False
                self._metadata[instance] = dict(message)
                self._metadata_received_at[instance] = time.monotonic()
                received_at = self._metadata_received_at[instance]
                refresh = self._refreshes.get(message.get("request_id"))
                if refresh is not None and refresh[0].get(instance) is connection:
                    refresh[1][instance] = (dict(message), received_at)
            logger.info(
                "browser_handoff stage=metadata_received instance=%s windows=%d revision=%s",
                instance, len(message["windows"]), message.get("revision"),
            )
            for window in message["windows"]:
                logger.info(
                    "browser_handoff stage=metadata_window instance=%s window=%s bridge_epoch=%s revision=%s received_at=%.6f bounds=%s",
                    instance, window.get("window_id"), connection.epoch,
                    message.get("revision"), received_at,
                    tuple(window.get(key) for key in ("left", "top", "width", "height")),
                )
            return True
        if message.get("type") == "browser_handoff_snapshot":
            request_id = message.get("request_id")
            if type(request_id) is not str or not request_id:
                return False
            with self._lock:
                if self._connections.get(instance) is not connection:
                    return False
                now = time.monotonic()
                for key, (_snapshot, received_at) in tuple(self._snapshots.items()):
                    if received_at + SNAPSHOT_RETENTION_SECONDS <= now:
                        self._snapshots.pop(key, None)
                while len(self._snapshots) >= MAX_RETAINED_SNAPSHOTS:
                    self._snapshots.pop(next(iter(self._snapshots)), None)
                self._snapshots[(instance, request_id)] = (dict(message), time.monotonic())
            callback_message = dict(message)
            callback_message["_bridge_epoch"] = connection.epoch
            self._notify(self.on_snapshot, instance, callback_message)
            logger.info("browser_handoff stage=snapshot_received instance=%s request=%s", instance, request_id)
            return True
        if message.get("type") == "browser_handoff_capabilities":
            if (
                message.get("browser_instance_id") != instance
                or message.get("receiver_epoch") != connection.epoch
            ):
                return False
            self._notify(self.on_capability, instance, connection.epoch)
            return True
        if message.get("type") == "browser_handoff_result":
            if _contains_url(message):
                return False
            with self._lock:
                if self._connections.get(instance) is not connection:
                    return False
            self._notify(self.on_result, instance, dict(message))
            return True
        return False

    def metadata(self, browser_instance_id):
        with self._lock:
            item = self._metadata.get(browser_instance_id)
            return None if item is None else dict(item)

    def snapshot(self, browser_instance_id, request_id):
        with self._lock:
            item = self._snapshots.get((browser_instance_id, request_id))
            if item is not None and item[1] + SNAPSHOT_RETENTION_SECONDS <= time.monotonic():
                self._snapshots.pop((browser_instance_id, request_id), None)
                item = None
            return None if item is None else dict(item[0])

    def browser_candidates(self, to_physical, *, received_at=None, refresh_id=None):
        """Return only metadata bound to a live authenticated host process.

        ``to_physical`` is deliberately injected: plan 001 must prove the
        Chromium-DIP-to-Windows-physical conversion before production supplies
        one.  A converter failure merely omits that candidate.
        """
        observed_at = time.monotonic() if received_at is None else received_at
        with self._lock:
            if refresh_id is not None:
                refresh = self._refreshes.get(refresh_id)
                if refresh is None or any(self._connections.get(key) is not conn for key, conn in refresh[0].items()):
                    raise ConnectionError("metadata bridge replaced")
                # All live bridges must answer the same query before deciding
                # uniqueness; a fast bridge must not hide a slower competitor.
                if set(self._connections) != set(refresh[0]):
                    raise ConnectionError("metadata bridge set changed")
                if set(refresh[1]) != set(refresh[0]):
                    return None
                snapshots = tuple((key, item[0], refresh[0][key], item[1]) for key, item in refresh[1].items())
            else:
                snapshots = tuple(
                    (instance, dict(message), self._connections.get(instance), self._metadata_received_at.get(instance))
                    for instance, message in self._metadata.items()
                )
        candidates = []
        for instance, message, connection, metadata_received_at in snapshots:
            if connection is None:
                continue
            hello = connection.hello
            for window in message.get("windows", ()):
                if type(window) is not dict:
                    continue
                try:
                    window_id = window["window_id"]
                    if type(window_id) is not int:
                        continue
                    bounds = to_physical(dict(window))
                    candidates.append(BrowserWindowCandidate(
                        browser_instance_id=instance,
                        window_id=window_id,
                        process_id=hello.browser_process_id,
                        process_created=hello.browser_process_created,
                        bounds=bounds,
                        focused=window.get("focused") is True,
                        observed_at=(
                            observed_at
                            if received_at is not None
                            else (metadata_received_at if metadata_received_at is not None else observed_at)
                        ),
                        metadata_revision=message.get("revision") if type(message.get("revision")) is int else None,
                        bridge_epoch=connection.epoch,
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
        return candidates

    def request_metadata_refresh(self, request_id):
        with self._lock:
            connections = dict(self._connections)
            if not connections or len(self._refreshes) >= 24:
                return False
            self._refreshes[request_id] = (connections, {})
        try:
            return all(connection.send({"type": "browser_handoff_metadata_request", "request_id": request_id}) for connection in connections.values())
        except Exception:
            return False

    def release_metadata_refresh(self, request_id):
        with self._lock:
            self._refreshes.pop(request_id, None)

    def bridge_is_current(self, instance, epoch):
        with self._lock:
            connection = self._connections.get(instance)
            return connection is not None and connection.epoch == epoch

    @property
    def has_connections(self):
        with self._lock:
            return bool(self._connections)

    def close_window(self, browser_instance_id, window_id, *, expected_epoch=None):
        if type(window_id) is not int:
            return False
        with self._lock:
            connection = self._connections.get(browser_instance_id)
            if connection is None or (expected_epoch is not None and connection.epoch != expected_epoch):
                return False
            return bool(connection.send({
                "type": "browser_handoff_close_window", "window_id": window_id,
            }))

    def request_snapshot(self, browser_instance_id, window_id, request_id, *, expected_epoch=None):
        if type(window_id) is not int or type(request_id) is not str or not request_id:
            return False
        with self._lock:
            connection = self._connections.get(browser_instance_id)
            if connection is None or (expected_epoch is not None and connection.epoch != expected_epoch):
                return False
            return bool(connection.send({
                "type": "browser_handoff_snapshot_request", "window_id": window_id, "request_id": request_id,
            }))

    def submit_receiver_request(self, browser_instance_id, request):
        """Deliver a Server-authorized job to one exact native-host instance."""
        if type(browser_instance_id) is not str or type(request) is not dict:
            return False
        with self._lock:
            connection = self._connections.get(browser_instance_id)
        return bool(connection and connection.send({
            "type": "browser_handoff_request", "request": dict(request),
        }))

    @staticmethod
    def _notify(callback, *args):
        threading.Thread(
            target=lambda: _safe_callback(callback, *args),
            name="browser-handoff-desktop-callback",
            daemon=True,
        ).start()


def _safe_callback(callback, *args):
    try:
        callback(*args)
    except Exception:
        pass


def _contains_url(value):
    if type(value) is dict:
        return "url" in value or any(_contains_url(item) for item in value.values())
    if type(value) is list:
        return any(_contains_url(item) for item in value)
    return False


def verify_native_host_parent(client_pid, hello):
    """Fail closed unless native host's actual parent matches the reported browser.

    Executable names are used only after this immutable PID/birth-time check;
    they are not a window-matching fallback.
    """
    if type(client_pid) is not int or client_pid < 1:
        return False
    try:
        return _windows_parent_identity(client_pid) == (
            hello.browser_process_id, hello.browser_process_created,
        )
    except Exception:
        return False


def _windows_parent_identity(process_id):
    import ctypes
    import ctypes.wintypes
    import os

    if os.name != "nt":
        raise RuntimeError("Windows process identity is required")
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD), ("cntUsage", ctypes.wintypes.DWORD),
            ("th32ProcessID", ctypes.wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.wintypes.DWORD), ("cntThreads", ctypes.wintypes.DWORD),
            ("th32ParentProcessID", ctypes.wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise OSError("Process32First failed")
        parent_pid = None
        while True:
            if entry.th32ProcessID == process_id:
                parent_pid = entry.th32ParentProcessID
                break
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        if not parent_pid:
            raise OSError("native host parent not found")
    finally:
        kernel32.CloseHandle(snapshot)
    parent = kernel32.OpenProcess(0x1000, False, parent_pid)
    if not parent:
        raise OSError("browser parent process is unavailable")
    try:
        created = ctypes.wintypes.FILETIME()
        exited = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        if not kernel32.GetProcessTimes(parent, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError("GetProcessTimes failed")
        return parent_pid, (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(parent)
