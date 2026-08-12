import unittest

from app.file_transfer import paste_coordinator as paste_routing
from app.file_transfer.paste_coordinator import PasteCoordinator


class PasteCoordinatorTests(unittest.TestCase):
    def test_file_transfer_route_depends_only_on_offer_source_and_destination(self):
        route = getattr(paste_routing, "should_transfer_files", None)
        self.assertIsNotNone(route)

        self.assertFalse(route("files", "server", "server"))
        self.assertTrue(route("files", "server", "client"))
        self.assertFalse(route("files", "client", "client"))
        self.assertTrue(route("files", "client", "server"))
        self.assertFalse(route("ordinary", "server", "client"))
        self.assertFalse(route("unknown", "client", "server"))

    def test_clipboard_offer_state_rejects_stale_and_prior_session_messages(self):
        state_type = getattr(paste_routing, "ClipboardOfferState", None)
        self.assertIsNotNone(state_type)
        state = state_type("server")
        state.start_session("session-one")

        local = state.observe_local("files", sequence=20)
        self.assertEqual(local.source, "server")
        self.assertTrue(state.should_transfer_to("client"))
        self.assertFalse(state.should_transfer_to("server"))

        self.assertTrue(
            state.accept_remote(
                {
                    "type": "clipboard_offer",
                    "session_id": "session-one",
                    "revision": 2,
                    "source": "client",
                    "kind": "ordinary",
                    "sequence": 31,
                }
            )
        )
        self.assertEqual(state.current_offer.source, "client")
        self.assertEqual(state.current_offer.kind, "ordinary")

        self.assertFalse(
            state.accept_remote(
                {
                    "type": "clipboard_offer",
                    "session_id": "session-one",
                    "revision": 1,
                    "source": "client",
                    "kind": "files",
                    "sequence": 30,
                }
            )
        )
        self.assertEqual(state.current_offer.kind, "ordinary")

        state.start_session("session-two")
        self.assertIsNone(state.current_offer)
        self.assertFalse(
            state.accept_remote(
                {
                    "type": "clipboard_offer",
                    "session_id": "session-one",
                    "revision": 3,
                    "source": "client",
                    "kind": "files",
                    "sequence": 32,
                }
            )
        )

    def test_snapshot_offer_cannot_claim_the_receivers_local_identity(self):
        state = paste_routing.ClipboardOfferState("server")
        state.start_session("session-one")
        local = state.observe_local("ordinary", sequence=20)

        self.assertFalse(state.accept_snapshot(local.to_message()))

    def test_unannounced_remote_snapshot_cannot_displace_newer_local_copy(self):
        state = paste_routing.ClipboardOfferState("client")
        state.start_session("session-one")
        local = state.observe_local("ordinary", sequence=40)
        delayed_remote_snapshot = paste_routing.ClipboardOffer(
            "session-one", 7, "server", "ordinary", 30
        )

        self.assertFalse(
            state.accept_snapshot(delayed_remote_snapshot.to_message())
        )
        self.assertEqual(state.current_offer, local)

    def test_coordinator_uses_explicit_offer_and_destination(self):
        requested = []
        coordinator = PasteCoordinator(lambda: requested.append("paste"))
        configure = getattr(coordinator, "set_route", None)
        self.assertIsNotNone(configure)
        offer = paste_routing.ClipboardOffer(
            "session-one", 1, "server", "files", 20
        )

        configure(offer, "server")
        coordinator.on_key_press("ctrl")
        self.assertFalse(coordinator.on_key_press("v"))

        coordinator.on_key_release("v")
        configure(offer, "client")
        self.assertTrue(coordinator.on_key_press("v"))
        self.assertEqual(requested, ["paste"])

        coordinator.on_key_release("v")
        configure(
            paste_routing.ClipboardOffer(
                "session-one", 2, "server", "ordinary", 21
            ),
            "client",
        )
        self.assertFalse(coordinator.on_key_press("v"))

    def test_coordinator_refreshes_destination_offer_before_suppressing_v(self):
        requested = []
        refreshed = []
        coordinator = PasteCoordinator(lambda: requested.append("paste"))
        remote_offer = paste_routing.ClipboardOffer(
            "session-one", 1, "client", "files", 20
        )
        local_offer = paste_routing.ClipboardOffer(
            "session-one", 1, "server", "files", 30
        )
        coordinator.set_route(remote_offer, "server")

        def refresh_destination():
            refreshed.append(True)
            coordinator.set_route(local_offer, "server")

        coordinator.before_paste = refresh_destination
        coordinator.on_key_press("ctrl")

        self.assertFalse(coordinator.on_key_press("v"))
        self.assertEqual(refreshed, [True])
        self.assertEqual(requested, [])

    def test_coordinator_suppresses_paste_when_destination_refresh_is_unknown(self):
        requested = []
        coordinator = PasteCoordinator(lambda: requested.append("paste"))
        coordinator.set_route(
            paste_routing.ClipboardOffer(
                "session-one", 1, "client", "files", 20
            ),
            "server",
        )
        coordinator.before_paste = lambda: False
        coordinator.on_key_press("ctrl")

        self.assertTrue(coordinator.on_key_press("v"))
        self.assertEqual(requested, [])
        self.assertTrue(coordinator.on_key_release("v"))

    def test_intercepts_ctrl_v_only_when_remote_files_are_available(self):
        requested = []
        coordinator = PasteCoordinator(lambda: requested.append("paste"))

        coordinator.set_route(
            paste_routing.ClipboardOffer(
                "session-one", 1, "client", "files", 20
            ),
            "server",
        )
        self.assertFalse(coordinator.on_key_press("ctrl"))
        self.assertTrue(coordinator.on_key_press("v"))
        self.assertEqual(requested, ["paste"])
        self.assertTrue(coordinator.on_key_release("v"))
        self.assertFalse(coordinator.on_key_release("ctrl"))

    def test_ordinary_and_repeated_paste_keys_are_not_accidentally_suppressed(self):
        requested = []
        coordinator = PasteCoordinator(lambda: requested.append("paste"))

        coordinator.set_route(None, "server")
        coordinator.on_key_press("ctrl")
        self.assertFalse(coordinator.on_key_press("v"))
        coordinator.set_route(
            paste_routing.ClipboardOffer(
                "session-one", 1, "client", "files", 20
            ),
            "server",
        )
        self.assertTrue(coordinator.on_key_press("v"))
        self.assertTrue(coordinator.on_key_press("v"))
        self.assertEqual(requested, ["paste"])
        coordinator.on_key_release("v")
        coordinator.set_route(None, "server")
        self.assertFalse(coordinator.on_key_press("v"))

    def test_disconnect_clears_availability_and_modifier_state(self):
        coordinator = PasteCoordinator(lambda: None)
        coordinator.set_route(
            paste_routing.ClipboardOffer(
                "session-one", 1, "client", "files", 20
            ),
            "server",
        )
        coordinator.on_key_press("ctrl")
        coordinator.reset()

        self.assertFalse(coordinator.transfer_required)
        self.assertFalse(coordinator.on_key_press("v"))


if __name__ == "__main__":
    unittest.main()
