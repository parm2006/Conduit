import unittest
import threading

from app.browser_handoff.desktop import BrowserHandoffDesktop
from app.browser_handoff.window_match import PhysicalRect


class FakeConnection:
    def __init__(self, hello, epoch="bridge-1"):
        self.hello = hello
        self.epoch = epoch
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return True

    def close(self):
        pass


class BrowserHandoffDesktopTests(unittest.TestCase):
    def test_refresh_requires_all_live_bridges_and_exact_request_id(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        first, second = self._connection(), self._connection("instance-b", "bridge-2")
        desktop.on_connected(first)
        desktop.on_connected(second)
        self.assertTrue(desktop.request_metadata_refresh("refresh-1"))
        convert = lambda window: PhysicalRect(window["left"], 0, window["left"] + 10, 10)
        def reply(connection, request_id):
            return desktop.on_message(connection, {
                "type": "browser_handoff_metadata", "browser_instance_id": connection.hello.browser_instance_id,
                "request_id": request_id, "revision": 9,
                "windows": [{"window_id": 7, "left": 1, "top": 0, "width": 10, "height": 10}],
            })
        reply(first, "old-refresh")
        self.assertIsNone(desktop.browser_candidates(convert, refresh_id="refresh-1"))
        reply(first, "refresh-1")
        self.assertIsNone(desktop.browser_candidates(convert, refresh_id="refresh-1"))
        reply(second, "refresh-1")
        candidates = desktop.browser_candidates(convert, refresh_id="refresh-1")
        self.assertEqual(len(candidates), 2)
        self.assertEqual({c.bridge_epoch for c in candidates}, {"bridge-1", "bridge-2"})
        reply(first, "unrelated-later-message")
        self.assertEqual(desktop.browser_candidates(convert, refresh_id="refresh-1"), candidates)
        desktop.release_metadata_refresh("refresh-1")
        self.assertEqual(desktop._refreshes, {})

    def test_replaced_epoch_cannot_reuse_old_metadata_or_satisfy_refresh(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        old = self._connection()
        desktop.on_connected(old)
        desktop.on_message(old, {"type": "browser_handoff_metadata", "browser_instance_id": "instance-a", "windows": []})
        desktop.request_metadata_refresh("refresh-1")
        new = self._connection(epoch="new-epoch")
        desktop.on_connected(new)
        self.assertIsNone(desktop.metadata("instance-a"))
        self.assertFalse(desktop.on_message(old, {"type": "browser_handoff_metadata", "browser_instance_id": "instance-a", "windows": []}))
        desktop.on_disconnected(old)
        self.assertTrue(desktop.bridge_is_current("instance-a", "new-epoch"))
        self.assertFalse(desktop.request_snapshot("instance-a", 7, "snapshot-1", expected_epoch="bridge-1"))
        self.assertEqual(new.sent, [])
        with self.assertRaises(ConnectionError):
            desktop.browser_candidates(lambda window: window, refresh_id="refresh-1")

    def test_disconnect_or_new_bridge_invalidates_refresh(self):
        for replace_set in (False, True):
            with self.subTest(new_bridge=replace_set):
                desktop = BrowserHandoffDesktop(start_bridge=False)
                first = self._connection()
                desktop.on_connected(first)
                desktop.request_metadata_refresh("refresh-1")
                if replace_set:
                    desktop.on_connected(self._connection("instance-b"))
                else:
                    desktop.on_disconnected(first)
                with self.assertRaises(ConnectionError):
                    desktop.browser_candidates(lambda window: window, refresh_id="refresh-1")

    @staticmethod
    def _connection(instance="instance-a", epoch="bridge-1"):
        hello = type("Hello", (), {
            "browser_instance_id": instance,
            "browser_process_id": 123,
            "browser_process_created": 456,
        })()
        return FakeConnection(hello, epoch=epoch)

    def test_binds_metadata_to_authenticated_connection_and_never_persists_urls(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = self._connection()
        desktop.on_connected(connection)

        self.assertTrue(desktop.on_message(connection, {
            "type": "browser_handoff_metadata", "browser_instance_id": "instance-a",
            "windows": [{"window_id": 7, "left": 1, "top": 2, "width": 3, "height": 4}],
        }))
        self.assertEqual(desktop.metadata("instance-a")["windows"][0]["window_id"], 7)
        self.assertNotIn("url", str(desktop.metadata("instance-a")))

    def test_rejects_spoofed_instance_and_clears_memory_on_disconnect(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = self._connection()
        desktop.on_connected(connection)

        self.assertFalse(desktop.on_message(connection, {
            "type": "browser_handoff_metadata", "browser_instance_id": "other", "windows": [],
        }))
        desktop.on_disconnected(connection)
        self.assertIsNone(desktop.metadata("instance-a"))

    def test_snapshot_retention_is_bounded_and_disconnect_clears_all_instance_requests(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = self._connection()
        desktop.on_connected(connection)
        for index in range(10):
            request_id = f"{index:032x}"
            self.assertTrue(desktop.on_message(connection, {
                "type": "browser_handoff_snapshot", "request_id": request_id,
                "snapshot": {"window_id": 7, "revision": index, "complete_capture": True},
            }))
        self.assertLessEqual(len(desktop._snapshots), 8)
        desktop.on_disconnected(connection)
        self.assertEqual(desktop._snapshots, {})

    def test_routes_snapshot_requests_only_to_live_matched_browser_instance(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = self._connection()
        desktop.on_connected(connection)

        self.assertTrue(desktop.request_snapshot("instance-a", 9, "f" * 32))
        self.assertEqual(connection.sent[0], {
            "type": "browser_handoff_snapshot_request", "window_id": 9, "request_id": "f" * 32,
        })
        self.assertFalse(desktop.request_snapshot("missing", 9, "f" * 32))

    def test_routes_receiver_request_to_exact_connected_browser_instance(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = self._connection()
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
        connection = self._connection()
        desktop.on_connected(connection)

        self.assertFalse(desktop.on_message(connection, {
            "type": "browser_handoff_capabilities", "browser_instance_id": "instance-a", "receiver_epoch": "wrong",
        }))
        self.assertTrue(desktop.on_message(connection, {
            "type": "browser_handoff_capabilities", "browser_instance_id": "instance-a", "receiver_epoch": "bridge-1",
        }))
        self.assertTrue(ready.wait(0.2))
        self.assertEqual(announced, [("instance-a", "bridge-1")])

    def test_builds_candidates_only_with_the_authenticated_browser_process_identity(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        connection = self._connection()
        desktop.on_connected(connection)
        desktop.on_message(connection, {
            "type": "browser_handoff_metadata", "browser_instance_id": "instance-a",
            "windows": [{"window_id": 7, "left": 1, "top": 2, "width": 3, "height": 4}],
        })

        candidates = desktop.browser_candidates(
            lambda item: PhysicalRect(item["left"], item["top"], item["left"] + item["width"], item["top"] + item["height"]),
            received_at=1.0,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].process_id, 123)
        self.assertEqual(candidates[0].process_created, 456)
