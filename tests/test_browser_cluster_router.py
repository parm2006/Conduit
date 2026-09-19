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
    def test_network_result_boundary_removes_only_transport_fields(self):
        from app.server import ConduitServer
        from types import SimpleNamespace
        ticket = self.router.authorize_edge("client-a-session", "client-a", "client-b-session", "client-b", 7)
        self.assertTrue(self.router.accept_request("client-a-session", request(ticket)))
        server = SimpleNamespace(cluster_browser_router=self.router)
        message = {**result(ticket), "type": "browser_handoff_result", "session_id": "client-b-session",
                   "peer_identity": "client-b", "addr": ("127.0.0.1", 123)}
        self.assertFalse(ConduitServer.on_browser_handoff_result(server, {**message, "url": "https://unexpected.test"}))
        self.assertFalse(ConduitServer.on_browser_handoff_result(server, {**message, "session_id": "attacker"}))
        self.assertTrue(ConduitServer.on_browser_handoff_result(server, message))

    def test_local_result_boundary_removes_native_message_type(self):
        from app.server import ConduitServer
        from types import SimpleNamespace
        self.router.register_capability("server", "server", "receiver-1", "browser-server")
        ticket = self.router.authorize_edge("client-a-session", "client-a", "server", "server", 7)
        self.assertTrue(self.router.accept_request("client-a-session", request(ticket, destination="server")))
        server = SimpleNamespace(cluster_browser_router=self.router, server_machine_id="server")
        message = {**result(ticket), "type": "browser_handoff_result"}
        self.assertTrue(ConduitServer._on_server_browser_result(server, "browser-server", message))

    def test_missing_receiver_capability_logs_specific_rejection(self):
        router = ClusterBrowserRouter(server_session_id="server", endpoint_available=lambda session: True,
                                      send=lambda *args: True, enqueue=lambda job: None, now=lambda: 100.0)
        with self.assertLogs("app.browser_handoff.cluster_router", level="INFO") as logs:
            self.assertIsNone(router.authorize_edge("server", "server", "client-session", "client", 7))
        self.assertIn("destination_capability_missing", str(logs.output))

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

    def test_candidate_and_accepted_edge_join_in_either_arrival_order(self):
        candidate = {
            "protocol": 1, "gesture_id": "c" * 32,
            "source_display_id": "display-a", "source_side": "right",
            "topology_version": 7, "incognito": False, "total_count": 1,
            "entries": [{"source_index": 0, "url": "https://example.test", "active": True}],
            "complete_capture": True,
        }
        self.assertTrue(self.router.stage_candidate("client-a-session", "client-a", candidate))
        self.assertEqual(self.sent, [])
        ticket = self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 7,
            gesture_id="c" * 32, source_display_id="display-a", source_side="right",
        )
        self.assertIsNotNone(ticket)
        self._run_queued()
        self.assertEqual(self.sent[0][0], "client-b-session")
        outbound = self.sent[0][1]["request"]
        self.assertEqual(outbound["destination_machine_id"], "client-b")
        self.assertEqual(outbound["entries"][0]["url"], "https://example.test")

    def test_candidate_only_or_mismatched_edge_does_not_open(self):
        candidate = {
            "protocol": 1, "gesture_id": "d" * 32,
            "source_display_id": "display-a", "source_side": "right",
            "topology_version": 7, "incognito": False, "total_count": 1,
            "entries": [{"source_index": 0, "url": "https://example.test", "active": True}],
            "complete_capture": True,
        }
        self.assertTrue(self.router.stage_candidate("client-a-session", "client-a", candidate))
        self.assertEqual(self.sent, [])
        self.router.authorize_edge(
            "client-a-session", "client-a", "client-b-session", "client-b", 7,
            gesture_id="d" * 32, source_display_id="display-a", source_side="left",
        )
        self._run_queued()
        self.assertEqual(self.sent, [])

    def test_unmatched_candidates_are_bounded_per_source_and_expire(self):
        router = ClusterBrowserRouter(
            server_session_id="server", endpoint_available=lambda session: True,
            send=lambda session, message: True, now=lambda: self.clock[0],
            enqueue=self.queued.append, max_staged_candidates_per_source=1,
            max_staged_candidate_bytes_per_source=10_000,
        )
        router.register_capability("client-a-session", "client-a", "source-1")
        candidate = {
            "protocol": 1, "gesture_id": "e" * 32,
            "source_display_id": "display-a", "source_side": "right",
            "topology_version": 7, "incognito": False, "total_count": 1,
            "entries": [{"source_index": 0, "url": "https://example.test", "active": True}],
            "complete_capture": True,
        }
        self.assertTrue(router.stage_candidate("client-a-session", "client-a", candidate))
        candidate["gesture_id"] = "f" * 32
        self.assertFalse(router.stage_candidate("client-a-session", "client-a", candidate))
        self.clock[0] += 61
        self.assertTrue(router.stage_candidate("client-a-session", "client-a", candidate))
