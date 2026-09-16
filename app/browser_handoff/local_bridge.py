"""Current-user Windows named-pipe bridge for browser handoff.

The desktop owns the server.  Native hosts are untrusted until the pipe's
kernel-reported client PID, current logon-session ACL, and an injected browser
parent verifier agree.  This is intentionally not a localhost protocol.
"""

from dataclasses import dataclass
import json
import os
import queue
import secrets
import struct
import threading
import time


MAX_BRIDGE_MESSAGE_BYTES = 64 * 1024
DEFAULT_MAX_CONNECTIONS = 4
DEFAULT_QUEUE_SIZE = 64


class BridgeProtocolError(ValueError):
    """URL-free local bridge protocol failure."""


@dataclass(frozen=True)
class BridgeHello:
    browser_instance_id: str
    host_pid: int
    browser_process_id: int
    browser_process_created: int


def validate_bridge_hello(message, *, client_pid):
    fields = {
        "type", "browser_instance_id", "host_pid", "browser_process_id",
        "browser_process_created",
    }
    if type(message) is not dict or set(message) != fields:
        raise BridgeProtocolError("invalid_hello_fields")
    if message.get("type") != "bridge_hello":
        raise BridgeProtocolError("invalid_hello_type")
    instance = message["browser_instance_id"]
    if type(instance) is not str or not instance or len(instance) > 256:
        raise BridgeProtocolError("invalid_browser_instance")
    if any(type(message[name]) is not int or message[name] < 1 for name in (
        "host_pid", "browser_process_id", "browser_process_created",
    )):
        raise BridgeProtocolError("invalid_process_identity")
    if message["host_pid"] != client_pid:
        raise BridgeProtocolError("invalid_host_identity")
    return BridgeHello(
        instance,
        message["host_pid"],
        message["browser_process_id"],
        message["browser_process_created"],
    )


def _encode_message(message):
    if type(message) is not dict:
        raise BridgeProtocolError("invalid_message")
    try:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BridgeProtocolError("invalid_message") from error
    if len(payload) > MAX_BRIDGE_MESSAGE_BYTES:
        raise BridgeProtocolError("frame_too_large")
    return struct.pack("<I", len(payload)) + payload


def _decode_message(payload):
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeProtocolError("invalid_json") from error
    if type(message) is not dict:
        raise BridgeProtocolError("invalid_message")
    return message


def _win32():
    if os.name != "nt":
        raise RuntimeError("Windows named pipes are required")
    import pywintypes
    import ntsecuritycon
    import win32api
    import win32con
    import win32file
    import win32pipe
    import win32security
    import win32ts
    return pywintypes, ntsecuritycon, win32api, win32con, win32file, win32pipe, win32security, win32ts


def _read_exact(handle, size):
    _pywintypes, _ntsecuritycon, _win32api, _win32con, win32file, _win32pipe, _win32security, _win32ts = _win32()
    chunks = []
    remaining = size
    while remaining:
        _error, chunk = win32file.ReadFile(handle, remaining)
        if not chunk:
            raise BridgeProtocolError("truncated_frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_pipe_message(handle):
    size = struct.unpack("<I", _read_exact(handle, 4))[0]
    if size > MAX_BRIDGE_MESSAGE_BYTES:
        raise BridgeProtocolError("frame_too_large")
    return _decode_message(_read_exact(handle, size))


def write_pipe_message(handle, message):
    _pywintypes, _ntsecuritycon, _win32api, _win32con, win32file, _win32pipe, _win32security, _win32ts = _win32()
    win32file.WriteFile(handle, _encode_message(message))


class BridgeConnection:
    """A bounded outbound queue; sends are safe from input/router threads."""

    def __init__(self, handle, *, max_queue=DEFAULT_QUEUE_SIZE, hello=None, epoch=None):
        if type(max_queue) is not int or max_queue < 1:
            raise ValueError("max_queue must be positive")
        self.handle = handle
        self.hello = hello
        self.epoch = epoch
        self._outbound = queue.Queue(maxsize=max_queue)
        self._closed = threading.Event()
        self._write_lock = threading.Lock()

    def send(self, message):
        if self._closed.is_set() or type(message) is not dict:
            return False
        try:
            self._outbound.put_nowait(dict(message))
            return True
        except queue.Full:
            return False

    def receive(self):
        if self._closed.is_set():
            return None
        return read_pipe_message(self.handle)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._outbound.put_nowait(None)
        except queue.Full:
            pass

    def _writer_loop(self):
        while not self._closed.is_set():
            try:
                message = self._outbound.get(timeout=0.1)
            except queue.Empty:
                continue
            if message is None:
                return
            try:
                with self._write_lock:
                    write_pipe_message(self.handle, message)
            except Exception:
                self.close()
                return


class NamedPipeClient:
    """Native-host-side client; it has no network fallback."""

    def __init__(self, pipe_name, *, connect_timeout=1.0):
        self.pipe_name = pipe_name
        self.connect_timeout = connect_timeout
        self._handle = None

    def connect(self, hello):
        if os.name != "nt":
            return False
        _pywintypes, _ntsecuritycon, _win32api, win32con, win32file, _win32pipe, _win32security, _win32ts = _win32()
        deadline = time.monotonic() + self.connect_timeout
        while time.monotonic() < deadline:
            try:
                self._handle = win32file.CreateFile(
                    self.pipe_name,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    0, None, win32con.OPEN_EXISTING, 0, None,
                )
                write_pipe_message(self._handle, hello)
                return True
            except Exception:
                self.close()
                time.sleep(0.05)
        return False

    def send(self, message):
        if self._handle is None:
            return False
        try:
            write_pipe_message(self._handle, message)
            return True
        except Exception:
            self.close()
            return False

    def receive(self):
        if self._handle is None:
            return None
        try:
            return read_pipe_message(self._handle)
        except Exception:
            self.close()
            return None

    def close(self):
        handle, self._handle = self._handle, None
        if handle is not None and os.name == "nt":
            try:
                _pywintypes, _ntsecuritycon, _win32api, _win32con, win32file, _win32pipe, _win32security, _win32ts = _win32()
                win32file.CloseHandle(handle)
            except Exception:
                pass


class DesktopBridge:
    """Desktop-owned per-user/logon pipe with bounded isolated connections."""

    def __init__(
        self,
        *,
        pipe_name=None,
        on_message,
        identity_verifier,
        on_connected=None,
        on_disconnected=None,
        on_ready=None,
        max_connections=DEFAULT_MAX_CONNECTIONS,
        max_queue=DEFAULT_QUEUE_SIZE,
    ):
        if type(max_connections) is not int or not 1 <= max_connections <= 16:
            raise ValueError("max_connections is invalid")
        self.pipe_name = pipe_name or default_pipe_name()
        self.on_message = on_message
        self.identity_verifier = identity_verifier
        self.on_connected = on_connected or (lambda connection: None)
        self.on_disconnected = on_disconnected or (lambda connection: None)
        self.on_ready = on_ready or (lambda: None)
        self.max_connections = max_connections
        self.max_queue = max_queue
        self._stop = threading.Event()
        self._thread = None
        self._pending_handle = None
        self._connections = set()
        self._lock = threading.Lock()
        self._announced_ready = False

    def start(self):
        if os.name != "nt":
            raise RuntimeError("Windows named pipes are required")
        with self._lock:
            if self._thread is not None:
                return False
            self._stop.clear()
            self._announced_ready = False
            self._thread = threading.Thread(
                target=self._accept_loop, name="browser-handoff-pipe", daemon=True,
            )
            self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        with self._lock:
            pending = self._pending_handle
            connections = tuple(self._connections)
        # The listener uses PIPE_NOWAIT so it observes this event without a
        # cross-thread CloseHandle (which can deadlock a synchronous connect).
        for connection in connections:
            connection.close()
            if connection.handle is not None:
                self._disconnect_and_close(connection.handle)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1)
        with self._lock:
            self._thread = None
            self._pending_handle = None
            self._connections.clear()

    def connect_for_test(self, hello):
        client = NamedPipeClient(self.pipe_name)
        if not client.connect(hello):
            raise RuntimeError("test pipe connection failed")
        return client

    def _accept_loop(self):
        while not self._stop.is_set():
            handle = None
            try:
                handle = self._create_pipe()
                with self._lock:
                    self._pending_handle = handle
                    announce_ready = not self._announced_ready
                    self._announced_ready = True
                if announce_ready:
                    self.on_ready()
                if not self._connect_pipe(handle):
                    self._close_handle(handle)
                    with self._lock:
                        self._pending_handle = None
                    continue
                with self._lock:
                    self._pending_handle = None
                if self._stop.is_set():
                    self._close_handle(handle)
                    return
                self._set_wait_mode(handle)
                threading.Thread(
                    target=self._serve_connection,
                    args=(handle,),
                    name="browser-handoff-pipe-client",
                    daemon=True,
                ).start()
            except Exception:
                if handle is not None:
                    self._close_handle(handle)
                with self._lock:
                    self._pending_handle = None
                if not self._stop.is_set():
                    time.sleep(0.05)

    def _serve_connection(self, handle):
        connection = None
        try:
            client_pid = self._client_pid(handle)
            hello = validate_bridge_hello(read_pipe_message(handle), client_pid=client_pid)
            if not bool(self.identity_verifier(client_pid, hello)):
                return
            connection = BridgeConnection(
                handle, max_queue=self.max_queue, hello=hello, epoch=secrets.token_hex(16),
            )
            with self._lock:
                if self._stop.is_set() or len(self._connections) >= self.max_connections:
                    return
                self._connections.add(connection)
            if not connection.send({"type": "bridge_ready", "bridge_epoch": connection.epoch}):
                return
            writer = threading.Thread(
                target=connection._writer_loop,
                name="browser-handoff-pipe-writer", daemon=True,
            )
            writer.start()
            self.on_connected(connection)
            while not self._stop.is_set():
                message = connection.receive()
                if message is None:
                    return
                try:
                    self.on_message(connection, message)
                except Exception:
                    return
        except Exception:
            return
        finally:
            if connection is not None:
                connection.close()
                with self._lock:
                    self._connections.discard(connection)
                try:
                    self.on_disconnected(connection)
                except Exception:
                    pass
            self._close_handle(handle)

    def _create_pipe(self):
        _pywintypes, _ntsecuritycon, _win32api, _win32con, _win32file, win32pipe, _win32security, _win32ts = _win32()
        return win32pipe.CreateNamedPipe(
            self.pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE |
            win32pipe.PIPE_NOWAIT | win32pipe.PIPE_REJECT_REMOTE_CLIENTS,
            self.max_connections,
            MAX_BRIDGE_MESSAGE_BYTES,
            MAX_BRIDGE_MESSAGE_BYTES,
            0,
            _current_logon_security_attributes(),
        )

    def _connect_pipe(self, handle):
        pywintypes, _ntsecuritycon, _win32api, _win32con, _win32file, win32pipe, _win32security, _win32ts = _win32()
        while not self._stop.is_set():
            try:
                win32pipe.ConnectNamedPipe(handle, None)
                return True
            except pywintypes.error as error:
                code = getattr(error, "winerror", None)
                if code == 535:  # ERROR_PIPE_CONNECTED
                    return True
                if code == 536:  # ERROR_PIPE_LISTENING
                    time.sleep(0.025)
                    continue
                if code == 232:  # ERROR_NO_DATA, disconnected before hello
                    try:
                        win32pipe.DisconnectNamedPipe(handle)
                    except Exception:
                        pass
                    continue
                raise
        return False

    @staticmethod
    def _client_pid(handle):
        _pywintypes, _ntsecuritycon, _win32api, _win32con, _win32file, win32pipe, _win32security, _win32ts = _win32()
        return int(win32pipe.GetNamedPipeClientProcessId(handle))

    @staticmethod
    def _set_wait_mode(handle):
        _pywintypes, _ntsecuritycon, _win32api, _win32con, _win32file, win32pipe, _win32security, _win32ts = _win32()
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_WAIT, None, None)

    @staticmethod
    def _close_handle(handle):
        if handle is None or os.name != "nt":
            return
        try:
            _pywintypes, _ntsecuritycon, _win32api, _win32con, win32file, _win32pipe, _win32security, _win32ts = _win32()
            win32file.CloseHandle(handle)
        except Exception:
            pass

    @staticmethod
    def _disconnect_and_close(handle):
        if handle is None or os.name != "nt":
            return
        try:
            _pywintypes, _ntsecuritycon, _win32api, _win32con, win32file, win32pipe, _win32security, _win32ts = _win32()
            win32file.CancelIoEx(handle, None)
        except Exception:
            pass
        # Closing after CancelIoEx is sufficient here.  DisconnectNamedPipe can
        # wait behind a concurrent synchronous ReadFile on some pywin32 builds.
        DesktopBridge._close_handle(handle)


def default_pipe_name():
    """Name includes the local interactive session; the ACL supplies authority."""
    if os.name != "nt":
        return r"\\.\pipe\ConduitBrowserHandoff-unsupported"
    _pywintypes, _ntsecuritycon, _win32api, _win32con, _win32file, _win32pipe, _win32security, win32ts = _win32()
    return r"\\.\pipe\ConduitBrowserHandoff-" + str(win32ts.ProcessIdToSessionId(os.getpid()))


def _current_logon_security_attributes():
    """Grant pipe access only to this user and Windows logon session.

    Microsoft documents that a logon SID in a named-pipe DACL prevents access
    from other terminal-services sessions; PIPE_REJECT_REMOTE_CLIENTS handles
    network clients separately.
    """
    _pywintypes, ntsecuritycon, win32api, _win32con, _win32file, _win32pipe, win32security, _win32ts = _win32()
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
        logon_sid = next(
            sid for sid, attributes in groups
            if attributes & win32security.SE_GROUP_LOGON_ID
        )
    finally:
        token.Close()
    descriptor = win32security.SECURITY_DESCRIPTOR()
    dacl = win32security.ACL()
    access = ntsecuritycon.FILE_GENERIC_READ | ntsecuritycon.FILE_GENERIC_WRITE
    for sid in (user_sid, logon_sid):
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, access, sid)
    descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
    attributes = win32security.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    return attributes
