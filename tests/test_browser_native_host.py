import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest

from app.browser_handoff.native_host import (
    MAX_NATIVE_MESSAGE_BYTES,
    NativeMessageError,
    NativeHost,
    read_message,
    write_message,
)


class ShortReadStream(io.BytesIO):
    def read(self, size=-1):
        if size > 1:
            size = 1
        return super().read(size)


class NativeFramingTests(unittest.TestCase):
    def test_reads_little_endian_json_with_partial_reads(self):
        encoded = b'{"type":"hello"}'
        stream = ShortReadStream(struct.pack("<I", len(encoded)) + encoded)

        self.assertEqual(read_message(stream), {"type": "hello"})

    def test_rejects_oversize_before_reading_payload(self):
        stream = io.BytesIO(struct.pack("<I", MAX_NATIVE_MESSAGE_BYTES + 1))

        with self.assertRaisesRegex(NativeMessageError, "too_large"):
            read_message(stream)

    def test_rejects_truncated_multibyte_json(self):
        payload = '{"label":"é"}'.encode("utf-8")[:-1]
        stream = io.BytesIO(struct.pack("<I", len(payload)) + payload)

        with self.assertRaisesRegex(NativeMessageError, "invalid_json"):
            read_message(stream)

    def test_write_emits_only_one_binary_frame(self):
        stream = io.BytesIO()

        write_message(stream, {"type": "result", "opened_count": 1})

        payload = stream.getvalue()
        self.assertEqual(struct.unpack("<I", payload[:4])[0], len(payload) - 4)
        self.assertEqual(json.loads(payload[4:]), {"type": "result", "opened_count": 1})


class FakeBridge:
    def __init__(self, incoming=()):
        self.incoming = iter(incoming)
        self.connected = []
        self.sent = []
        self.closed = False

    def connect(self, hello):
        self.connected.append(hello)
        return True

    def receive(self):
        return next(self.incoming)

    def send(self, message):
        self.sent.append(message)
        return True

    def close(self):
        self.closed = True


class NativeHostTests(unittest.TestCase):
    def test_console_entrypoint_uses_binary_stdout_and_never_echoes_url_on_disconnect(self):
        payload = json.dumps({
            "type": "browser_handoff_hello", "browser_instance_id": "instance-1",
            "url": "https://secret.example/never-log-this",
        }, separators=(",", ":")).encode("utf-8")
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "conduit_browser_host.py")],
            input=struct.pack("<I", len(payload)) + payload,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=5,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertNotIn(b"secret.example", completed.stdout + completed.stderr)
        self.assertEqual(read_message(io.BytesIO(completed.stdout)), {
            "type": "browser_handoff_error", "reason": "bridge_disconnected",
        })

    def test_disconnected_host_returns_bounded_error_without_payload_echo(self):
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        write_message(stdin, {
            "type": "browser_handoff_hello", "browser_instance_id": "instance-1",
            "url": "https://secret.example",
        })
        stdin.seek(0)

        exit_code = NativeHost(lambda: None).run(stdin, stdout)

        stdout.seek(0)
        self.assertEqual(exit_code, 0)
        self.assertEqual(read_message(stdout), {
            "type": "browser_handoff_error", "reason": "bridge_disconnected"
        })

    def test_relays_only_after_hello_and_never_writes_diagnostics_to_stdout(self):
        bridge = FakeBridge(incoming=({"type": "browser_handoff_request"},))
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        for message in (
            {"type": "browser_handoff_hello", "browser_instance_id": "instance-1"},
            {"type": "browser_handoff_result", "opened_count": 1},
        ):
            write_message(stdin, message)
        stdin.seek(0)

        exit_code = NativeHost(
            lambda: bridge, host_pid=lambda: 42, parent_identity=lambda: (77, 88)
        ).run(stdin, stdout)

        stdout.seek(0)
        self.assertEqual(exit_code, 0)
        self.assertEqual(bridge.connected, [{
            "type": "bridge_hello", "browser_instance_id": "instance-1", "host_pid": 42,
            "browser_process_id": 77, "browser_process_created": 88,
        }])
        self.assertEqual(bridge.sent, [{"type": "browser_handoff_result", "opened_count": 1}])
        self.assertEqual(read_message(stdout), {"type": "browser_handoff_request"})
        self.assertTrue(bridge.closed)
