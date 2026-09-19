"""Dedicated Chromium native-messaging host.

This module deliberately owns only the binary stdio boundary.  Its bridge is
injected so the GUI process is never used as a native-messaging executable and
stdout can remain an exact Chromium protocol stream.
"""

import json
import os
import struct
import sys
import threading


MAX_NATIVE_MESSAGE_BYTES = 64 * 1024


class NativeMessageError(ValueError):
    """A URL-free native-messaging framing failure."""


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise NativeMessageError("truncated_frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream):
    """Read one bounded little-endian Chromium native-messaging object."""
    size = struct.unpack("<I", _read_exact(stream, 4))[0]
    if size > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeMessageError("frame_too_large")
    try:
        value = json.loads(_read_exact(stream, size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativeMessageError("invalid_json") from error
    if type(value) is not dict:
        raise NativeMessageError("invalid_message")
    return value


def write_message(stream, message):
    """Write one bounded object.  Callers must never print on stdout."""
    if type(message) is not dict:
        raise NativeMessageError("invalid_message")
    try:
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise NativeMessageError("invalid_message") from error
    if len(encoded) > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeMessageError("frame_too_large")
    stream.write(struct.pack("<I", len(encoded)))
    stream.write(encoded)
    stream.flush()


class NativeHost:
    """Relay one extension connection to the desktop's authenticated bridge."""

    def __init__(self, bridge_factory, *, host_pid=os.getpid, parent_identity=None, stderr=None):
        self._bridge_factory = bridge_factory
        self._host_pid = host_pid
        self._parent_identity = parent_identity or _parent_process_identity
        self._stderr = stderr if stderr is not None else sys.stderr

    def run(self, stdin, stdout):
        bridge = None
        connected = False
        bridge_reader = None
        stdout_lock = threading.Lock()
        stop_reader = threading.Event()
        try:
            while True:
                try:
                    message = read_message(stdin)
                except NativeMessageError as error:
                    if str(error) == "truncated_frame":
                        return 0
                    self._diagnostic(f"native host stopped: {error}")
                    return 1
                if not connected:
                    if message.get("type") != "browser_handoff_hello":
                        write_message(stdout, self._error("hello_required"))
                        continue
                    instance_id = message.get("browser_instance_id")
                    if type(instance_id) is not str or not instance_id or len(instance_id) > 256:
                        write_message(stdout, self._error("invalid_browser_instance"))
                        continue
                    bridge = self._bridge_factory()
                    browser_pid, browser_created = self._parent_identity()
                    if bridge is None or not bridge.connect({
                        "type": "bridge_hello",
                        "browser_instance_id": instance_id,
                        "host_pid": self._host_pid(),
                        "browser_process_id": browser_pid,
                        "browser_process_created": browser_created,
                    }):
                        write_message(stdout, self._error("bridge_disconnected"))
                        return 0
                    connected = True
                    bridge_reader = threading.Thread(
                        target=self._relay_desktop_messages,
                        args=(bridge, stdout, stdout_lock, stop_reader),
                        name="browser-handoff-native-reader",
                        daemon=True,
                    )
                    bridge_reader.start()
                    continue
                if not bridge.send(message):
                    with stdout_lock:
                        write_message(stdout, self._error("bridge_disconnected"))
                    return 0
        except (BrokenPipeError, OSError):
            return 0
        finally:
            stop_reader.set()
            if bridge is not None:
                try:
                    bridge.close()
                except Exception:
                    pass
            if bridge_reader is not None:
                bridge_reader.join(timeout=0.1)

    def _diagnostic(self, message):
        try:
            print(message, file=self._stderr)
        except Exception:
            pass

    @staticmethod
    def _error(reason):
        return {"type": "browser_handoff_error", "reason": reason}

    @staticmethod
    def _relay_desktop_messages(bridge, stdout, stdout_lock, stop_reader):
        while not stop_reader.is_set():
            try:
                message = bridge.receive()
            except (StopIteration, BrokenPipeError, OSError):
                message = None
            if message is None:
                if not stop_reader.is_set():
                    try:
                        with stdout_lock:
                            write_message(stdout, NativeHost._error("bridge_disconnected"))
                    except (NativeMessageError, BrokenPipeError, OSError):
                        pass
                return
            try:
                with stdout_lock:
                    write_message(stdout, message)
            except (NativeMessageError, BrokenPipeError, OSError):
                return


def main(bridge_factory):
    """Console-capable entry point used only by the dedicated host binary."""
    return NativeHost(bridge_factory).run(sys.stdin.buffer, sys.stdout.buffer)


def _parent_process_identity():
    """Return a PID plus immutable Windows creation time for bridge validation."""
    parent_pid = os.getppid()
    if os.name != "nt":
        return parent_pid, 1
    import ctypes
    import ctypes.wintypes

    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, parent_pid)
    if not process:
        return parent_pid, 1
    try:
        created = ctypes.wintypes.FILETIME()
        exited = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetProcessTimes(
            process, ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            return parent_pid, 1
        return parent_pid, (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        ctypes.windll.kernel32.CloseHandle(process)
