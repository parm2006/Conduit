import unittest

from app.browser_handoff.cluster_router import ClusterBrowserRouter


def request(ticket, *, destination="client-b", request_id="a" * 32):
    return {
        "protocol": 1, "request_id": request_id, "route_ticket": ticket,
        "topology_version": 7, "destination_machine_id": destination,
        "incognito": False, "total_count": 1,
        "entries": [{"source_index": 0, "url": "https://example.test", "active": True}],
        "complete_capture": True,
    }


def result(ticket, *, request_id="a" * 32, epoch="receiver-1"):
    return {
        "request_id": request_id, "route_ticket": ticket, "status": "complete",
        "opened_count": 1, "total_count": 1, "entries": [], "receiver_epoch": epoch,
    }


class ClusterBrowserRouterTests(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.queued = []
        self.sent = []
        self.router = ClusterBrowserRouter(
            server_session_id="server", endpoint_available=lambda session: session != "gone",
            send=lambda session, message: self.sent.append((session, message)) or True,
            now=lambda: self.clock[0], enqueue=self.queued.append,
        )
        self.router.register_capability("client-a-session", "client-a", "source-1")
        self.router.register_capability("client-b-session", "client-b", "receiver-1", "browser-b")

    def _run_queued(self):
        while self.queued:
            self.queued.pop(0)()

    def test_authorized_source_relays_only_to_bound_destination_then_result_back(self):
        ticket = self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 7,
        )
        self.assertIsNotNone(ticket)

        self.assertTrue(self.router.accept_request("client-a-session", request(ticket)))
        self.assertEqual(self.sent, [])
        self._run_queued()
        self.assertEqual(self.sent[0][0], "client-b-session")
        self.assertEqual(self.sent[0][1]["type"], "browser_handoff_request")
        self.assertEqual(self.sent[0][1]["browser_instance_id"], "browser-b")

        self.assertTrue(self.router.accept_result("client-b-session", result(ticket)))
        self._run_queued()
        self.assertEqual(self.sent[1][0], "client-a-session")
        self.assertEqual(self.sent[1][1]["type"], "browser_handoff_result")
        self.assertNotIn("url", str(self.sent[1][1]))

    def test_rejects_forged_source_destination_and_receiver_epoch(self):
        ticket = self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 7,
        )
        self.assertFalse(self.router.accept_request("attacker", request(ticket)))
        self.assertFalse(self.router.accept_request("client-a-session", request(ticket, destination="client-a")))
        self.assertTrue(self.router.accept_request("client-a-session", request(ticket)))
        self.assertFalse(self.router.accept_result("client-a-session", result(ticket)))
        self.assertFalse(self.router.accept_result("client-b-session", result(ticket, epoch="other")))
        self.assertEqual(self.queued, [self.queued[0]])

    def test_expiry_topology_change_and_reconnect_invalidate_ticket(self):
        ticket = self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 7,
        )
        self.clock[0] += 61
        self.assertFalse(self.router.accept_request("client-a-session", request(ticket)))

        ticket = self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 7,
        )
        self.router.topology_changed(8)
        self.assertFalse(self.router.accept_request("client-a-session", request(ticket)))

        ticket = self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 8,
        )
        self.router.endpoint_disconnected("client-b-session")
        self.assertFalse(self.router.accept_request("client-a-session", request(ticket)))

    def test_duplicate_request_is_not_enqueued_twice_and_queue_overflow_fails_closed(self):
        ticket = self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 7,
        )
        message = request(ticket)
        self.assertTrue(self.router.accept_request("client-a-session", message))
        self.assertFalse(self.router.accept_request("client-a-session", message))
        self.assertEqual(len(self.queued), 1)

        limited = ClusterBrowserRouter(
            server_session_id="server", endpoint_available=lambda session: True,
            send=lambda session, message: True, now=lambda: 1, enqueue=lambda task: None,
            max_active=1,
        )
        limited.register_capability("a", "a", "a-epoch")
        limited.register_capability("b", "b", "b-epoch")
        first = limited.authorize_edge("a", "a", "b", "b", 7)
        second = limited.authorize_edge("a", "a", "b", "b", 7)
        self.assertTrue(limited.accept_request("a", request(first, request_id="1" * 32, destination="b")))
        self.assertFalse(limited.accept_request("a", request(second, request_id="2" * 32, destination="b")))
