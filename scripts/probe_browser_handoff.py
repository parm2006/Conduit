"""Opt-in, URL-free diagnostic harness for browser-window correlation.

Run ``python scripts/probe_browser_handoff.py --help`` before use.  The normal
observer starts only with ``--observe``; ``--native-host`` is reserved for the
disposable Chromium probe's binary stdio connection.
"""

import argparse
import json
import os
from pathlib import Path
import queue
import re
import struct
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.browser_handoff.window_match import (
    BrowserWindowCandidate,
    MAX_EDGE_DELTA_PIXELS,
    MAX_METADATA_AGE_SECONDS,
    NativeWindowObservation,
    PhysicalRect,
    match_window,
)


MAX_NATIVE_FRAME_BYTES = 64 * 1024
NATIVE_HOST_NAME = "com.conduit.browser_handoff_probe"
_EXTENSION_ID = re.compile(r"^[a-p]{32}$")


class FrameError(ValueError):
    pass


def host_ready_message(instance_id, host_pid, browser_process_id, browser_process_created):
    """Build the URL-free connection identity reported to the probe worker."""
    return {
        "type": "probe_host_ready",
        "host_pid": host_pid,
        "browser_process_id": browser_process_id,
        "browser_process_created": browser_process_created,
        "received_instance_id": instance_id,
    }


def native_host_manifest(host_path, extension_id):
    """Return a Windows host manifest bound to exactly one extension origin."""
    if not isinstance(host_path, str) or not host_path:
        raise ValueError("host_path must be a non-empty string")
    if not isinstance(extension_id, str) or not _EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("extension_id must be a 32-character Chromium extension ID")
    return {
        "name": NATIVE_HOST_NAME,
        "description": "Conduit browser-window correlation diagnostic probe",
        "path": host_path,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def candidates_from_metadata(message, *, received_at, to_physical):
    """Build candidates using local receipt time, never extension clock time."""
    if type(message) is not dict or not isinstance(message.get("browser_instance_id"), str):
        return []
    windows = message.get("windows")
    if type(windows) is not list:
        return []
    candidates = []
    for item in windows:
        if type(item) is not dict:
            continue
        window_id = item.get("window_id")
        process_id = item.get("browser_process_id")
        process_created = item.get("browser_process_created")
        if any(type(value) is not int for value in (window_id, process_id, process_created)):
            continue
        try:
            bounds = to_physical(item)
        except (KeyError, TypeError, ValueError):
            continue
        candidates.append(
            BrowserWindowCandidate(
                browser_instance_id=message["browser_instance_id"],
                window_id=window_id,
                process_id=process_id,
                process_created=process_created,
                bounds=bounds,
                focused=item.get("focused") is True,
                observed_at=received_at,
            )
        )
    return candidates


class ProbeMetadataStore:
    """Memory-only latest metadata snapshot for each diagnostic connection."""

    def __init__(self):
        self._snapshots = {}
        self._lock = threading.Lock()

    def record(self, message, *, received_at):
        if (
            type(message) is not dict
            or not isinstance(message.get("browser_instance_id"), str)
            or _contains_url(message)
        ):
            return False
        with self._lock:
            self._snapshots[message["browser_instance_id"]] = (dict(message), received_at)
        return True

    def candidates(self, *, to_physical):
        with self._lock:
            snapshots = tuple(self._snapshots.values())
        candidates = []
        for message, received_at in snapshots:
            candidates.extend(candidates_from_metadata(message, received_at=received_at, to_physical=to_physical))
        return candidates

    def latest(self):
        with self._lock:
            if len(self._snapshots) != 1:
                return None
            message, received_at = next(iter(self._snapshots.values()))
            return dict(message), received_at


class ProbeHostDiagnostics:
    """Bounded URL-free counters for the native-host pipeline."""

    def __init__(self):
        self._state = {
            "messages_received": 0,
            "hello_received": 0,
            "metadata_received": 0,
            "metadata_accepted": 0,
            "metadata_rejected": 0,
            "windows_in_latest_metadata": 0,
            "correlations_emitted": 0,
            "last_correlation_status": None,
            "last_stage": "observer_started",
        }

    def record_message(self, message_type):
        self._state["messages_received"] += 1
        if message_type == "probe_hello":
            self._state["hello_received"] += 1
            self._state["last_stage"] = "hello_received"
        elif message_type == "window_metadata":
            self._state["metadata_received"] += 1
            self._state["last_stage"] = "metadata_received"
        else:
            self._state["last_stage"] = "unsupported_message"

    def record_metadata(self, *, accepted, window_count):
        key = "metadata_accepted" if accepted else "metadata_rejected"
        self._state[key] += 1
        if accepted:
            self._state["windows_in_latest_metadata"] = window_count
            self._state["last_stage"] = "metadata_accepted"
        else:
            self._state["last_stage"] = "metadata_rejected"

    def record_correlation(self, status):
        self._state["correlations_emitted"] += 1
        self._state["last_correlation_status"] = status
        self._state["last_stage"] = "correlation_emitted"

    def message(self, observer_snapshot):
        return {
            "type": "probe_diagnostics",
            "schema_version": 1,
            "host": dict(self._state),
            "observer": observer_snapshot,
        }

    @property
    def hello_received(self):
        return self._state["hello_received"] > 0


def correlation_evidence(token, metadata, *, received_at, now):
    """Return URL-free raw-coordinate evidence for one native move token.

    This is diagnostic data only.  It intentionally does not apply a DPI
    conversion or choose a fallback candidate when raw bounds do not match.
    """
    native = NativeWindowObservation(
        hwnd=token.hwnd,
        process_id=token.process_id,
        process_created=token.process_created,
        bounds=token.bounds,
        observed_at=token.completed_at,
    )
    result = {
        "type": "probe_correlation",
        "native": {
            "process_id": token.process_id,
            "process_created": token.process_created,
            "bounds": _rect_values(token.bounds),
        },
        "metadata_age_ms": int(max(0, now - received_at) * 1000),
        "candidates": [],
    }
    if not isinstance(metadata, dict) or _contains_url(metadata):
        result["status"] = "no_metadata"
        return result
    if now - received_at > MAX_METADATA_AGE_SECONDS:
        result["status"] = "stale_metadata"
        return result

    candidates = candidates_from_metadata(
        metadata,
        received_at=received_at,
        to_physical=_raw_browser_bounds,
    )
    matching_identity = [
        candidate
        for candidate in candidates
        if candidate.process_id == token.process_id
        and candidate.process_created == token.process_created
    ]
    matching_geometry = [
        candidate
        for candidate in candidates
        if all(
            abs(native_edge - browser_edge) <= MAX_EDGE_DELTA_PIXELS
            for native_edge, browser_edge in zip(
                _rect_values(token.bounds), _rect_values(candidate.bounds)
            )
        )
    ]
    result["candidates"] = [
        {
            "window_id": candidate.window_id,
            "process_id": candidate.process_id,
            "process_created": candidate.process_created,
            "same_process": candidate in matching_identity,
            "same_geometry": candidate in matching_geometry,
            "bounds": _rect_values(candidate.bounds),
            "edge_deltas": [
                native_edge - browser_edge
                for native_edge, browser_edge in zip(
                    _rect_values(token.bounds), _rect_values(candidate.bounds)
                )
            ],
        }
        for candidate in candidates[:32]
    ]
    if not candidates:
        result["status"] = "no_candidates"
        return result
    if not matching_identity:
        if len(matching_geometry) == 1:
            result["status"] = "unique_geometry_match_process_unverified"
            result["matching_window_id"] = matching_geometry[0].window_id
            return result
        if len(matching_geometry) > 1:
            result["status"] = "ambiguous_geometry_match_process_unverified"
            return result
        result["status"] = "no_process_match"
        return result
    matched = match_window(native, candidates, now=now)
    if matched is None:
        result["status"] = "ambiguous_or_bounds_mismatch"
        return result
    result["status"] = "unique_raw_match"
    result["matching_window_id"] = matched.window_id
    return result


def consume_probe_correlation(store, tracker, *, now):
    """Consume at most one move token and correlate it with one probe peer."""
    token = tracker.consume_eligible_move(now=now)
    if token is None:
        return None
    latest = store.latest()
    if latest is None:
        return correlation_evidence(token, None, received_at=now, now=now)
    metadata, received_at = latest
    return correlation_evidence(token, metadata, received_at=received_at, now=now)


def emit_probe_correlation(store, tracker, emit, *, now):
    """Emit one completed move immediately, even when messages keep arriving."""
    evidence = consume_probe_correlation(store, tracker, now=now)
    if evidence is None:
        return False
    emit(evidence)
    return True


def _raw_browser_bounds(item):
    return PhysicalRect(
        item["left"],
        item["top"],
        item["left"] + item["width"],
        item["top"] + item["height"],
    )


def _rect_values(rect):
    return [rect.left, rect.top, rect.right, rect.bottom]


def _contains_url(value):
    if type(value) is dict:
        return "url" in value or any(_contains_url(item) for item in value.values())
    if type(value) is list:
        return any(_contains_url(item) for item in value)
    return False


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise FrameError("truncated native-messaging frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_native_message(stream):
    """Read one Chromium native-messaging JSON object with a hard size bound."""
    header = _read_exact(stream, 4)
    size = struct.unpack("<I", header)[0]
    if size > MAX_NATIVE_FRAME_BYTES:
        raise FrameError("native-messaging frame exceeds diagnostic limit")
    try:
        message = json.loads(_read_exact(stream, size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrameError("invalid native-messaging JSON") from error
    if type(message) is not dict:
        raise FrameError("native-messaging payload must be an object")
    return message


def write_native_message(stream, message):
    """Write one bounded Chromium native-messaging object without stdout logs."""
    if type(message) is not dict:
        raise FrameError("native-messaging payload must be an object")
    encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(encoded) > MAX_NATIVE_FRAME_BYTES:
        raise FrameError("native-messaging frame exceeds diagnostic limit")
    stream.write(struct.pack("<I", len(encoded)))
    stream.write(encoded)
    stream.flush()


def _native_host_loop():
    if os.name != "nt":
        print("probe host is only available on Windows", file=sys.stderr)
        return 2
    from app.browser_handoff.windows_drag import WinEventMoveObserver

    store = ProbeMetadataStore()
    observer = WinEventMoveObserver()
    diagnostics = ProbeHostDiagnostics()
    try:
        observer.start()
    except Exception as error:
        print(f"probe observer failed: {type(error).__name__}", file=sys.stderr)
        return 1

    inbox = queue.Queue()

    def read_messages():
        try:
            while True:
                inbox.put(("message", read_native_message(sys.stdin.buffer)))
        except FrameError as error:
            inbox.put(("frame_error", error))
        except BrokenPipeError:
            inbox.put(("closed", None))

    reader = threading.Thread(
        target=read_messages,
        name="browser-handoff-probe-reader",
        daemon=True,
    )
    reader.start()
    last_diagnostics = None

    def emit_correlation(evidence):
        diagnostics.record_correlation(evidence.get("status"))
        write_native_message(sys.stdout.buffer, evidence)

    def emit_diagnostics_if_changed():
        nonlocal last_diagnostics
        if not diagnostics.hello_received:
            return
        message = diagnostics.message(observer.diagnostic_snapshot())
        if message != last_diagnostics:
            write_native_message(sys.stdout.buffer, message)
            last_diagnostics = message

    try:
        while True:
            try:
                kind, payload = inbox.get(timeout=0.05)
            except queue.Empty:
                emit_probe_correlation(
                    store,
                    observer.tracker,
                    emit_correlation,
                    now=time.monotonic(),
                )
                emit_diagnostics_if_changed()
                continue
            if kind == "closed":
                return 0
            if kind == "frame_error":
                print(f"probe host stopped: {payload}", file=sys.stderr)
                return 1
            message = payload
            message_type = message.get("type")
            diagnostics.record_message(message_type)
            if message_type == "probe_hello":
                browser_process_id, browser_process_created = _parent_process_identity()
                write_native_message(
                    sys.stdout.buffer,
                    host_ready_message(
                        message.get("browser_instance_id"),
                        os.getpid(),
                        browser_process_id,
                        browser_process_created,
                    ),
                )
            elif message_type == "window_metadata":
                accepted = store.record(message, received_at=time.monotonic())
                windows = message.get("windows", [])
                diagnostics.record_metadata(
                    accepted=accepted,
                    window_count=len(windows) if type(windows) is list else 0,
                )
                if not accepted:
                    write_native_message(
                        sys.stdout.buffer,
                        {"type": "probe_error", "reason": "invalid_metadata"},
                    )
                else:
                    print(f"probe metadata: {len(windows)} opaque windows", file=sys.stderr)
            else:
                write_native_message(
                    sys.stdout.buffer,
                    {"type": "probe_error", "reason": "unsupported_message"},
                )
            emit_probe_correlation(
                store,
                observer.tracker,
                emit_correlation,
                now=time.monotonic(),
            )
            emit_diagnostics_if_changed()
    finally:
        observer.stop()


def _parent_process_identity():
    """Return the native host's parent PID and immutable Windows birth time."""
    parent_pid = os.getppid()
    if os.name != "nt":
        return parent_pid, None
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.windll.kernel32
    process = kernel32.OpenProcess(0x1000, False, parent_pid)
    if not process:
        return parent_pid, None
    try:
        created = ctypes.wintypes.FILETIME()
        exited = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            process,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return parent_pid, None
        return parent_pid, (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(process)


def _observe():
    if os.name != "nt":
        print("This passive WinEvent harness is available only on Windows.", file=sys.stderr)
        return 2
    from app.browser_handoff.windows_drag import WinEventMoveObserver

    observer = WinEventMoveObserver()
    observer.start()
    print("Observing opaque native move tokens. Press Ctrl+C to stop.")
    try:
        while True:
            token = observer.tracker.consume_eligible_move(now=time.monotonic())
            if token is not None:
                print(
                    json.dumps(
                        {
                            "hwnd": token.hwnd,
                            "process_id": token.process_id,
                            "process_created": token.process_created,
                            "bounds": [
                                token.bounds.left,
                                token.bounds.top,
                                token.bounds.right,
                                token.bounds.bottom,
                            ],
                            "completed_monotonic": token.completed_at,
                        }
                    )
                )
            time.sleep(0.05)
    except KeyboardInterrupt:
        return 0
    finally:
        observer.stop()


def _write_manifest(manifest_path, host_path, extension_id):
    path = Path(manifest_path)
    path.write_text(
        json.dumps(native_host_manifest(host_path, extension_id), indent=2) + "\n",
        encoding="utf-8",
    )
    print(path.resolve())


def _register_manifest(manifest_path, browser):
    if os.name != "nt":
        raise RuntimeError("current-user native-host registration is only available on Windows")
    import winreg

    roots = {
        "chrome": [r"Software\Google\Chrome\NativeMessagingHosts"],
        "edge": [r"Software\Microsoft\Edge\NativeMessagingHosts"],
        "all": [
            r"Software\Google\Chrome\NativeMessagingHosts",
            r"Software\Microsoft\Edge\NativeMessagingHosts",
        ],
    }[browser]
    resolved = str(Path(manifest_path).resolve())
    for root in roots:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + "\\" + NATIVE_HOST_NAME) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, resolved)
    print("registered " + ", ".join(roots))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--observe", action="store_true", help="passively observe native move tokens")
    mode.add_argument("--native-host", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--write-native-manifest", metavar="PATH", help="write an exact-origin development host manifest")
    mode.add_argument("--register-native-host", metavar="PATH", help="register an existing host manifest for the current Windows user")
    parser.add_argument("--host-path", help="absolute path to the separately built probe-host executable")
    parser.add_argument("--extension-id", help="unpacked extension ID shown by Chromium")
    parser.add_argument("--browser", choices=("chrome", "edge", "all"), default="all")
    args = parser.parse_args(argv)
    if args.native_host:
        return _native_host_loop()
    if args.observe:
        return _observe()
    if args.write_native_manifest:
        if not args.host_path or not args.extension_id:
            parser.error("--write-native-manifest requires --host-path and --extension-id")
        _write_manifest(args.write_native_manifest, args.host_path, args.extension_id)
        return 0
    _register_manifest(args.register_native_host, args.browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
