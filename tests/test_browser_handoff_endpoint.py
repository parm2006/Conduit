import unittest

from app.browser_handoff.endpoint import BrowserHandoffEndpoint


class FakeDesktop:
    def __init__(self):
        self.on_capability = None
        self.on_result = None
        self.requests = []

    def submit_receiver_request(self, browser_instance_id, request):
        self.requests.append((browser_instance_id, dict(request)))
        return browser_instance_id == "browser-1"


class BrowserHandoffEndpointTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.desktop = FakeDesktop()
        self.endpoint = BrowserHandoffEndpoint(
            machine_id="client-1", desktop=self.desktop,
            send_control=lambda message: self.sent.append(dict(message)) or True,
        )

    def test_announces_only_opaque_capability_and_forwards_authorized_receiver_request(self):
        self.desktop.on_capability("browser-1", "epoch-1")

        self.assertEqual(self.sent, [{
            "type": "browser_handoff_capabilities", "browser_instance_id": "browser-1",
            "receiver_epoch": "epoch-1",
        }])
        self.assertTrue(self.endpoint.on_control_message({
            "type": "browser_handoff_request", "browser_instance_id": "browser-1",
            "request": {"request_id": "a" * 32},
        }))
        self.assertEqual(self.desktop.requests, [("browser-1", {"request_id": "a" * 32})])

    def test_forwards_only_result_from_live_extension_and_rejects_unknown_messages(self):
        self.desktop.on_result("browser-1", {
            "type": "browser_handoff_result", "request_id": "a" * 32,
            "route_ticket": "ticket", "receiver_epoch": "epoch-1",
            "status": "complete", "opened_count": 1, "total_count": 1, "entries": [],
        })

        self.assertEqual(self.sent[0]["type"], "browser_handoff_result")
        self.assertNotIn("url", str(self.sent[0]))
        self.assertFalse(self.endpoint.on_control_message({"type": "browser_handoff_result"}))
        self.assertFalse(self.endpoint.on_control_message({"type": "other"}))

    def test_reannounces_live_capability_after_control_lane_becomes_ready(self):
        self.desktop.on_capability("browser-1", "epoch-1")
        self.sent.clear()

        self.assertTrue(self.endpoint.announce_all())
        self.assertEqual(self.sent[0]["browser_instance_id"], "browser-1")
