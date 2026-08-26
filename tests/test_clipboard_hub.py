import threading
import unittest
from types import SimpleNamespace

from app.clipboard_formats import (
    ClipboardEntry,
    ClipboardSnapshot,
    decode_clipboard_message,
    encode_clipboard_message,
)
from app.clipboard_hub import ClipboardHub, ClipboardHubItem
from app.client import ConduitClient
from app.file_transfer.paste_coordinator import ClipboardOfferState, PasteCoordinator
from app.server import ConduitServer


def snapshot(text):
    return ClipboardSnapshot([ClipboardEntry("unicode_text", text.encode())])


class RecordingEndpoint:
    def __init__(self):
        self.items = []
        self.received = threading.Event()

    def __call__(self, item):
        self.items.append(item)
        self.received.set()
        return True

    def wait_for(self, count):
        for _ in range(100):
            if len(self.items) >= count:
                return True
            self.received.wait(0.01)
            self.received.clear()
        return False


class SessionRegistry:
    def __init__(self):
        self.sessions = {
            "session-1": SimpleNamespace(
                ready=True,
                peer_identity="client-1",
            ),
            "session-2": SimpleNamespace(
                ready=True,
                peer_identity="client-2",
            ),
        }

    def get(self, session_id):
        return self.sessions.get(session_id)


class TargetedNetwork:
    def __init__(self):
        self.messages = []
        self.received = threading.Event()

    def send_message(self, message, session_id=None):
        self.messages.append((session_id, message))
        self.received.set()
        return True


class PayloadClipboard:
    def __init__(self):
        self.payloads = []
        self.received = threading.Event()

    def inject(self, payload):
        self.payloads.append(payload)
        self.received.set()
        return True


class ClipboardHubTests(unittest.TestCase):
    def setUp(self):
        self.hub = ClipboardHub("server")
        self.addCleanup(self.hub.stop)
        self.server = RecordingEndpoint()
        self.client1 = RecordingEndpoint()
        self.client2 = RecordingEndpoint()
        self.hub.register_endpoint("server", self.server)
        self.hub.register_endpoint("client-1", self.client1)
        self.hub.register_endpoint("client-2", self.client2)

    def test_accepts_three_sources_in_server_receive_order_and_broadcasts_elsewhere(self):
        one = self.hub.accept_ordinary("client-1", 7, snapshot("one"))
        self.assertTrue(self.server.wait_for(1))
        self.assertTrue(self.client2.wait_for(1))
        two = self.hub.accept_ordinary("server", 3, snapshot("two"))
        self.assertTrue(self.client1.wait_for(1))
        self.assertTrue(self.client2.wait_for(2))
        three = self.hub.accept_ordinary("client-2", 11, snapshot("three"))

        self.assertEqual([one.revision, two.revision, three.revision], [1, 2, 3])
        self.assertTrue(self.server.wait_for(2))
        self.assertTrue(self.client1.wait_for(2))
        self.assertTrue(self.client2.wait_for(2))
        self.assertEqual([item.revision for item in self.server.items], [1, 3])
        self.assertEqual([item.revision for item in self.client1.items], [2, 3])
        self.assertEqual([item.revision for item in self.client2.items], [1, 2])

    def test_stale_or_duplicate_source_sequence_cannot_replace_newest_item(self):
        accepted = self.hub.accept_ordinary("client-1", 4, snapshot("new"))

        self.assertIsNone(
            self.hub.accept_ordinary("client-1", 4, snapshot("duplicate"))
        )
        self.assertIsNone(
            self.hub.accept_ordinary("client-1", 3, snapshot("stale"))
        )
        self.assertEqual(self.hub.latest_item, accepted)
        self.assertEqual(self.hub.revision, 1)

    def test_newly_ready_endpoint_receives_current_ordinary_item(self):
        self.hub.disconnect_endpoint("client-2")
        accepted = self.hub.accept_ordinary("client-1", 1, snapshot("current"))
        replacement = RecordingEndpoint()

        self.hub.register_endpoint("client-2", replacement)

        self.assertTrue(replacement.wait_for(1))
        self.assertEqual(replacement.items, [accepted])

    def test_disconnect_preserves_state_and_other_endpoints(self):
        self.hub.accept_ordinary("client-1", 1, snapshot("first"))
        self.assertTrue(self.server.wait_for(1))
        self.hub.disconnect_endpoint("client-1")

        accepted = self.hub.accept_ordinary("client-2", 1, snapshot("second"))

        self.assertTrue(self.server.wait_for(2))
        self.assertEqual(self.hub.latest_item, accepted)
        self.assertEqual(self.server.items[-1], accepted)

    def test_reconnected_endpoint_gets_a_fresh_source_sequence_domain(self):
        self.hub.accept_ordinary("client-1", 100, snapshot("before-reboot"))
        self.hub.disconnect_endpoint("client-1")
        self.hub.register_endpoint("client-1", RecordingEndpoint())

        accepted = self.hub.accept_ordinary(
            "client-1",
            1,
            snapshot("after-reboot"),
        )

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.revision, 2)
        self.assertEqual(self.hub.latest_item, accepted)

    def test_late_item_from_replaced_session_cannot_enter_fresh_sequence_domain(self):
        self.hub.disconnect_endpoint("client-1")
        self.hub.register_endpoint(
            "client-1",
            RecordingEndpoint(),
            source_domain="old-session",
        )
        before = self.hub.accept_ordinary(
            "client-1",
            100,
            snapshot("before"),
            source_domain="old-session",
        )
        self.hub.register_endpoint(
            "client-1",
            RecordingEndpoint(),
            source_domain="new-session",
        )

        current = self.hub.accept_ordinary(
            "client-1",
            1,
            snapshot("current"),
            source_domain="new-session",
        )
        delayed = self.hub.accept_ordinary(
            "client-1",
            101,
            snapshot("delayed-old-session"),
            source_domain="old-session",
        )

        self.assertIsNotNone(before)
        self.assertIsNotNone(current)
        self.assertIsNone(delayed)
        self.assertEqual(self.hub.latest_item, current)

    def test_pause_holds_broadcast_and_coalesces_to_newest_revision(self):
        self.hub.pause_delivery()

        first = self.hub.accept_ordinary("client-1", 1, snapshot("first"))
        newest = self.hub.accept_ordinary("client-1", 2, snapshot("newest"))

        self.assertEqual(first.revision, 1)
        self.assertEqual(newest.revision, 2)
        self.assertEqual(self.server.items, [])
        self.assertEqual(self.client2.items, [])
        self.hub.resume_delivery()
        self.assertTrue(self.server.wait_for(1))
        self.assertTrue(self.client2.wait_for(1))
        self.assertEqual(self.server.items, [newest])
        self.assertEqual(self.client2.items, [newest])

    def test_success_and_rollback_resume_converge_on_newest_revision(self):
        for commit in (True, False):
            with self.subTest(commit=commit):
                hub = ClipboardHub("server")
                self.addCleanup(hub.stop)
                endpoint = RecordingEndpoint()
                hub.register_endpoint("server", RecordingEndpoint())
                hub.register_endpoint("client", endpoint)
                hub.pause_delivery()
                hub.accept_ordinary("server", 1, snapshot("before"))
                newest = hub.accept_ordinary("server", 2, snapshot("during"))

                hub.resume_delivery()

                self.assertTrue(endpoint.wait_for(1))
                self.assertEqual(endpoint.items, [newest])

    def test_file_offer_supersedes_payload_without_storing_file_bytes(self):
        self.hub.accept_ordinary("server", 1, snapshot("ordinary"))

        offer = self.hub.accept_offer("client-1", 2, "files")

        self.assertIsInstance(offer, ClipboardHubItem)
        self.assertEqual(offer.kind, "files")
        self.assertIsNone(offer.snapshot)
        self.assertEqual(self.hub.latest_item, offer)

    def test_stop_erases_memory_and_rejects_future_items(self):
        self.hub.accept_ordinary("server", 1, snapshot("secret"))

        self.hub.stop()

        self.assertIsNone(self.hub.latest_item)
        self.assertIsNone(self.hub.accept_ordinary("server", 2, snapshot("late")))


class ServerClipboardHubRoutingTests(unittest.TestCase):
    def setUp(self):
        self.server = ConduitServer.__new__(ConduitServer)
        self.server.server_machine_id = "server"
        self.server.session_registry = SessionRegistry()
        self.server.data_network = TargetedNetwork()
        self.server.control_network = TargetedNetwork()
        self.server.clipboard = PayloadClipboard()
        self.server._clipboard_endpoint_ids = {}
        self.server.clipboard_hub = ClipboardHub("server")
        self.server.clipboard_hub.register_endpoint(
            "server",
            self.server._deliver_clipboard_to_server,
        )
        self.server._register_clipboard_endpoint("session-1")
        self.server._register_clipboard_endpoint("session-2")
        self.addCleanup(self.server.clipboard_hub.stop)

    def test_client_copy_is_injected_on_server_and_relayed_only_to_other_client(self):
        payload = encode_clipboard_message(snapshot("shared"))
        payload.update({
            "session_id": "session-1",
            "peer_identity": "client-1",
            "source_id": "untrusted-wire-value",
            "source_sequence": 8,
        })

        self.assertTrue(self.server.on_remote_copy(payload))

        self.assertTrue(self.server.clipboard.received.wait(1))
        self.assertTrue(self.server.data_network.received.wait(1))
        self.assertEqual(
            [session_id for session_id, _ in self.server.data_network.messages],
            ["session-2"],
        )
        session_id, relayed = self.server.data_network.messages[0]
        self.assertEqual(session_id, "session-2")
        self.assertEqual(relayed["source_id"], "client-1")
        self.assertEqual(relayed["source_sequence"], 8)
        self.assertEqual(relayed["cluster_revision"], 1)
        self.assertEqual(
            decode_clipboard_message(
                ConduitServer._clipboard_wire_payload(relayed)
            ),
            snapshot("shared"),
        )

    def test_unbound_or_spoofed_session_is_rejected_before_hub_ordering(self):
        payload = encode_clipboard_message(snapshot("private"))
        payload.update({
            "session_id": "session-1",
            "peer_identity": "client-2",
            "source_sequence": 1,
        })

        self.assertFalse(self.server.on_remote_copy(payload))

        self.assertEqual(self.server.clipboard_hub.revision, 0)
        self.assertEqual(self.server.data_network.messages, [])

    def test_file_offer_uses_same_revision_stream_and_relays_no_payload_bytes(self):
        payload = {
            "type": "clipboard_offer",
            "session_id": "session-1",
            "peer_identity": "client-1",
            "kind": "files",
            "sequence": 12,
        }

        self.assertTrue(self.server.on_remote_clipboard_offer(payload))

        self.assertTrue(self.server.control_network.received.wait(1))
        self.assertEqual(self.server.clipboard_hub.latest_item.kind, "files")
        self.assertIsNone(self.server.clipboard_hub.latest_item.snapshot)
        self.assertEqual(
            [session_id for session_id, _ in self.server.control_network.messages],
            ["session-2"],
        )
        _, relayed = self.server.control_network.messages[0]
        self.assertEqual(relayed["cluster_revision"], 1)
        self.assertEqual(relayed["source_id"], "client-1")
        self.assertNotIn("formats", relayed)


class ClientClipboardRevisionTests(unittest.TestCase):
    def setUp(self):
        self.client = ConduitClient.__new__(ConduitClient)
        self.client.clipboard = PayloadClipboard()
        self.client.control_network = SimpleNamespace(
            session_info={"session_id": "session-2"},
            session_id="session-2",
        )
        self.client.clipboard_offer_state = ClipboardOfferState("client")
        self.client.paste_coordinator = PasteCoordinator(lambda: None)
        self.client.is_active = False

    def message(self, revision, text):
        payload = encode_clipboard_message(snapshot(text))
        payload["cluster_revision"] = revision
        payload["source_id"] = "client-1"
        payload["source_sequence"] = revision
        return payload

    def test_client_injects_new_revision_and_rejects_stale_or_duplicate_delivery(self):
        self.assertTrue(self.client.on_remote_copy(self.message(2, "new")))

        self.assertFalse(self.client.on_remote_copy(self.message(2, "duplicate")))
        self.assertFalse(self.client.on_remote_copy(self.message(1, "stale")))
        self.assertEqual(len(self.client.clipboard.payloads), 1)
        self.assertEqual(
            decode_clipboard_message(self.client.clipboard.payloads[0]),
            snapshot("new"),
        )

    def test_client_rejects_unversioned_delivery(self):
        payload = encode_clipboard_message(snapshot("legacy"))

        self.assertFalse(self.client.on_remote_copy(payload))
        self.assertEqual(self.client.clipboard.payloads, [])

    def test_cluster_revision_is_not_compared_to_local_offer_revision(self):
        state = self.client._get_clipboard_offer_state()
        for sequence in range(1, 101):
            state.observe_local("ordinary", sequence)

        self.assertTrue(self.client.on_remote_copy(self.message(2, "remote-newest")))

        self.assertEqual(state.cluster_revision, 2)
        self.assertEqual(state.current_offer.source, "server")
        self.assertEqual(len(self.client.clipboard.payloads), 1)


if __name__ == "__main__":
    unittest.main()
