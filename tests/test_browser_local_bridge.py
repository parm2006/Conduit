import os
import queue
import threading
import subprocess
import sys
import time
import unittest
import uuid

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
    def test_real_pipes_carry_capability_refresh_snapshot_and_authorized_delivery(self):
        try:
            result = subprocess.run(
                [sys.executable, "-c", "from tests.test_browser_local_bridge import exercise_browser_route; exercise_browser_route()"],
                capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired:
            self.fail("browser route stalled on real Windows pipes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplex_messages_flow_while_both_sides_have_pending_reads(self):
        # A subprocess bounds failures even when synchronous Windows I/O hangs.
        try:
            result = subprocess.run(
                [sys.executable, "-c", "from tests.test_browser_local_bridge import exercise_idle_duplex; exercise_idle_duplex()"],
                capture_output=True, text=True, timeout=8,
            )
        except subprocess.TimeoutExpired:
            self.fail("duplex pipe stalled with pending reads")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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


def exercise_idle_duplex():
    connected, delivered, received = queue.Queue(), queue.Queue(), queue.Queue()
    ready = threading.Event()
    bridge = DesktopBridge(
        pipe_name=rf"\\.\pipe\ConduitDuplexTest-{uuid.uuid4().hex}",
        on_message=lambda connection, message: delivered.put(message),
        on_connected=connected.put,
        identity_verifier=lambda pid, hello: pid == os.getpid(), on_ready=ready.set,
    )
    bridge.start()
    assert ready.wait(2)
    client = bridge.connect_for_test({
        "type": "bridge_hello", "browser_instance_id": "test-browser",
        "host_pid": os.getpid(), "browser_process_id": os.getpid(), "browser_process_created": 1,
    })
    connection = connected.get(timeout=2)
    def receive():
        while True:
            message = client.receive()
            if message is None:
                return
            received.put(message)
    threading.Thread(target=receive, daemon=True).start()
    try:
        assert received.get(timeout=2)["type"] == "bridge_ready"
        time.sleep(0.05)
        assert connection.send({"type": "browser_handoff_metadata_request", "request_id": "fresh"})
        assert received.get(timeout=2)["request_id"] == "fresh"
        # Client's read is pending again when the extension sends its answer.
        sender = threading.Thread(target=lambda: client.send({"type": "browser_handoff_metadata", "request_id": "fresh"}), daemon=True)
        sender.start()
        assert delivered.get(timeout=2)["request_id"] == "fresh"
    finally:
        bridge.stop()
        client.close()


def exercise_browser_route():
    """Real pipe/desktop/coordinator/router/endpoint; simulated Chrome APIs only."""
    from app.browser_handoff.desktop import BrowserHandoffDesktop
    from app.browser_handoff.endpoint import BrowserHandoffEndpoint
    from app.browser_handoff.coordinator import BrowserHandoffCoordinator
    from app.browser_handoff.cluster_router import ClusterBrowserRouter
    from app.browser_handoff.windows_drag import MoveTracker, EVENT_SYSTEM_MOVESIZESTART, EVENT_OBJECT_LOCATIONCHANGE, EVENT_SYSTEM_MOVESIZEEND
    from app.browser_handoff.window_match import PhysicalRect
    from app.server import ConduitServer
    from types import SimpleNamespace

    source, target = [BrowserHandoffDesktop(start_bridge=False, identity_verifier=lambda pid, hello: pid == os.getpid()) for _ in range(2)]
    capable, finished = threading.Event(), threading.Event()
    opened, results, clients = [], [], []
    def send(session, message):
        if session == "client-session":
            return endpoint.on_control_message(message)
        results.append(message)
        finished.set()
        return True
    router = ClusterBrowserRouter(server_session_id="server", endpoint_available=lambda session: True,
                                  send=send, now=time.monotonic, enqueue=lambda job: job())
    server = SimpleNamespace(cluster_browser_router=router)
    def control(message):
        if message["type"] == "browser_handoff_capabilities":
            accepted = router.register_capability("client-session", "client", message["receiver_epoch"], message["browser_instance_id"])
            if accepted:
                capable.set()
            return accepted
        return ConduitServer.on_browser_handoff_result(server, {
            **message, "session_id": "client-session", "peer_identity": "client", "addr": ("127.0.0.1", 1),
        })
    endpoint = BrowserHandoffEndpoint(machine_id="client", desktop=target, send_control=control)

    def browser(client, instance):
        receiver_epoch = None
        while True:
            message = client.receive()
            if message is None:
                return
            kind = message["type"]
            if kind == "bridge_ready":
                receiver_epoch = message["bridge_epoch"]
                client.send({"type": "browser_handoff_capabilities", "browser_instance_id": instance, "receiver_epoch": receiver_epoch})
            elif kind == "browser_handoff_metadata_request":
                client.send({"type": "browser_handoff_metadata", "browser_instance_id": instance,
                             "request_id": message["request_id"], "revision": 3,
                             "windows": [{"window_id": 7, "left": 100, "top": 100, "width": 800, "height": 600}]})
            elif kind == "browser_handoff_snapshot_request":
                client.send({"type": "browser_handoff_snapshot", "request_id": message["request_id"], "epoch": instance,
                             "snapshot": {"window_id": 7, "revision": 3, "incognito": False, "complete_capture": True,
                                          "total_count": 1, "entries": [{"source_index": 0, "url": "https://example.test", "active": True}]}})
            elif kind == "browser_handoff_request":
                request = message["request"]
                opened.append(request)
                client.send({"type": "browser_handoff_result", "request_id": request["request_id"],
                             "route_ticket": request["route_ticket"], "receiver_epoch": receiver_epoch,
                             "status": "complete", "opened_count": 1, "total_count": 1, "entries": []})
    coordinator = None
    try:
        for desktop, instance in ((source, "source-browser"), (target, "target-browser")):
            ready = threading.Event()
            desktop.bridge.pipe_name = rf"\\.\pipe\ConduitRouteTest-{uuid.uuid4().hex}"
            desktop.bridge.on_ready = ready.set
            desktop.start()
            assert ready.wait(2)
            client = desktop.bridge.connect_for_test({"type": "bridge_hello", "browser_instance_id": instance,
                "host_pid": os.getpid(), "browser_process_id": os.getpid(), "browser_process_created": 1})
            clients.append(client)
            threading.Thread(target=browser, args=(client, instance), daemon=True).start()
        assert capable.wait(2), "receiver capability never reached server"
        tracker = MoveTracker()
        bounds = PhysicalRect(100, 100, 900, 700)
        for event, rect in ((EVENT_SYSTEM_MOVESIZESTART, PhysicalRect(0, 0, 800, 600)),
                            (EVENT_OBJECT_LOCATIONCHANGE, bounds), (EVENT_SYSTEM_MOVESIZEEND, bounds)):
            tracker.observe(event, hwnd=1, process_id=1, process_created=1, bounds=rect, timestamp=time.monotonic(), left_button_down=True)
        coordinator = BrowserHandoffCoordinator(desktop=source, move_tracker=tracker,
            to_physical=lambda window: bounds, read_bounds=lambda token: bounds,
            send_candidate=lambda candidate: router.stage_candidate("server", "server", candidate))
        gesture = coordinator.claim_edge(display_rect=None, edge_region=None, source_display_id="display", source_side="left", topology_version=7)
        assert gesture
        assert router.authorize_edge("server", "server", "client-session", "client", 7,
            gesture_id=gesture, source_display_id="display", source_side="left"), "browser edge was not authorized"
        assert finished.wait(3), "snapshot did not reach destination/result path"
        assert len(opened) == 1
        assert opened[0]["entries"][0]["url"] == "https://example.test"
        assert results[0]["status"] == "complete"
    finally:
        if coordinator:
            coordinator.cancel()
        router.stop()
        for client in clients:
            client.close()
        source.stop()
        target.stop()
