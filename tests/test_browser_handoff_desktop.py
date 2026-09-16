import unittest
import threading

from app.browser_handoff.desktop import BrowserHandoffDesktop


class FakeConnection:
    def __init__(self, hello, epoch="bridge-1"):
        self.hello = hello
        self.epoch = epoch
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return True


class BrowserHandoffDesktopTests(unittest.TestCase):
    def test_binds_metadata_to_authenticated_connection_and_never_persists_urls(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = FakeConnection(type("Hello", (), {"browser_instance_id": "instance-a"})())
        desktop.on_connected(connection)

        self.assertTrue(desktop.on_message(connection, {
            "type": "browser_handoff_metadata", "browser_instance_id": "instance-a",
            "windows": [{"window_id": 7, "left": 1, "top": 2, "width": 3, "height": 4}],
        }))
        self.assertEqual(desktop.metadata("instance-a")["windows"][0]["window_id"], 7)
        self.assertNotIn("url", str(desktop.metadata("instance-a")))

    def test_rejects_spoofed_instance_and_clears_memory_on_disconnect(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = FakeConnection(type("Hello", (), {"browser_instance_id": "instance-a"})())
        desktop.on_connected(connection)

        self.assertFalse(desktop.on_message(connection, {
            "type": "browser_handoff_metadata", "browser_instance_id": "other", "windows": [],
        }))
        desktop.on_disconnected(connection)
        self.assertIsNone(desktop.metadata("instance-a"))

    def test_routes_snapshot_requests_only_to_live_matched_browser_instance(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = FakeConnection(type("Hello", (), {"browser_instance_id": "instance-a"})())
        desktop.on_connected(connection)

        self.assertTrue(desktop.request_snapshot("instance-a", 9, "f" * 32))
        self.assertEqual(connection.sent[0], {
            "type": "browser_handoff_snapshot_request", "window_id": 9, "request_id": "f" * 32,
        })
        self.assertFalse(desktop.request_snapshot("missing", 9, "f" * 32))

    def test_routes_receiver_request_to_exact_connected_browser_instance(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = FakeConnection(type("Hello", (), {"browser_instance_id": "instance-a"})())
        desktop.on_connected(connection)
        request = {"request_id": "a" * 32}

        self.assertTrue(desktop.submit_receiver_request("instance-a", request))
        self.assertEqual(connection.sent[0], {
            "type": "browser_handoff_request", "request": request,
        })
        self.assertFalse(desktop.submit_receiver_request("other", request))

    def test_announces_capability_only_after_matching_bridge_ready_ack(self):
        announced = []
        ready = threading.Event()
        desktop = BrowserHandoffDesktop(
            start_bridge=False,
            on_capability=lambda instance, epoch: (announced.append((instance, epoch)), ready.set()),
        )
        connection = FakeConnection(type("Hello", (), {"browser_instance_id": "instance-a"})(), epoch="bridge-1")
        desktop.on_connected(connection)

        self.assertFalse(desktop.on_message(connection, {
            "type": "browser_handoff_capabilities", "browser_instance_id": "instance-a", "receiver_epoch": "wrong",
        }))
        self.assertTrue(desktop.on_message(connection, {
            "type": "browser_handoff_capabilities", "browser_instance_id": "instance-a", "receiver_epoch": "bridge-1",
        }))
        self.assertTrue(ready.wait(0.2))
        self.assertEqual(announced, [("instance-a", "bridge-1")])
