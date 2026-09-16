import os
import queue
import threading
import unittest

from app.browser_handoff.local_bridge import (
    MAX_BRIDGE_MESSAGE_BYTES,
    BridgeConnection,
    BridgeProtocolError,
    DesktopBridge,
    validate_bridge_hello,
)


class BridgeProtocolTests(unittest.TestCase):
    def test_hello_binds_the_os_reported_host_pid(self):
        hello = validate_bridge_hello({
            "type": "bridge_hello",
            "browser_instance_id": "browser-a",
            "host_pid": 123,
            "browser_process_id": 456,
            "browser_process_created": 789,
        }, client_pid=123)

        self.assertEqual(hello.browser_instance_id, "browser-a")
        self.assertEqual(hello.browser_process_id, 456)

    def test_hello_rejects_spoofed_host_pid_and_unknown_fields(self):
        with self.assertRaisesRegex(BridgeProtocolError, "host_identity"):
            validate_bridge_hello({
                "type": "bridge_hello", "browser_instance_id": "browser-a",
                "host_pid": 99, "browser_process_id": 456,
                "browser_process_created": 789,
            }, client_pid=123)
        with self.assertRaisesRegex(BridgeProtocolError, "hello_fields"):
            validate_bridge_hello({
                "type": "bridge_hello", "browser_instance_id": "browser-a",
                "host_pid": 123, "browser_process_id": 456,
                "browser_process_created": 789, "origin": "forged",
            }, client_pid=123)

    def test_bounded_outbound_queue_refuses_overload(self):
        connection = BridgeConnection(None, max_queue=1)

        self.assertTrue(connection.send({"type": "one"}))
        self.assertFalse(connection.send({"type": "two"}))
        connection.close()


@unittest.skipUnless(os.name == "nt", "Windows named pipes are required")
class WindowsPipeIntegrationTests(unittest.TestCase):
    def test_same_user_pipe_accepts_verified_host_and_shutdown_closes_client(self):
        delivered = queue.Queue()
        ready = threading.Event()
        bridge = DesktopBridge(
            pipe_name=r"\\.\pipe\ConduitBrowserHandoffTest-" + str(os.getpid()),
            on_message=lambda connection, message: delivered.put(message),
            identity_verifier=lambda client_pid, hello: client_pid == os.getpid(),
            on_ready=ready.set,
        )
        bridge.start()
        self.assertTrue(ready.wait(2))
        client = bridge.connect_for_test({
            "type": "bridge_hello", "browser_instance_id": "test-browser",
            "host_pid": os.getpid(), "browser_process_id": os.getpid(),
            "browser_process_created": 1,
        })
        try:
            self.assertEqual(client.receive()["type"], "bridge_ready")
            client.send({"type": "browser_handoff_metadata", "windows": []})
            self.assertEqual(delivered.get(timeout=2)["type"], "browser_handoff_metadata")
        finally:
            client.close()
            bridge.stop()
