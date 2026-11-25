import inspect
import sys
import threading
import unittest
from types import SimpleNamespace

from app.clipboard_formats import ClipboardEntry, ClipboardSnapshot
from app.clipboard_hub import ClipboardHub
from app.file_transfer.paste_coordinator import ClipboardOfferState, PasteCoordinator
from app.server import ConduitServer


class FilePasteOfferRaceTests(unittest.TestCase):
    def make_server(self, destination):
        server = ConduitServer.__new__(ConduitServer)
        server.server_machine_id = "server"
        server._paste_route_lock = threading.RLock()
        server.pressed_keys = set()
        server.messages = []
        server.forwarded = []
        server.paste_requests = []
        server.injected = []
        sessions = {
            f"session-{machine}": SimpleNamespace(
                session_id=f"session-{machine}",
                ready=True,
                peer_identity=machine,
            )
            for machine in ("client-a", "client-b")
        }
        server.session_registry = SimpleNamespace(
            get=sessions.get,
            active_sessions=lambda: tuple(sessions.values()),
        )
        server.input_router = SimpleNamespace(
            active_machine_id=destination,
            active_session_id=(
                None if destination == "server" else f"session-{destination}"
            ),
            forward_key_press=lambda key: server.forwarded.append(dict(key)),
        )
        server.control_network = SimpleNamespace(
            send_message=lambda message, session_id=None: (
                server.messages.append((session_id, message)) or True
            ),
        )
        server.file_paste_service = SimpleNamespace(
            request_paste=lambda: server.paste_requests.append(True),
        )
        server.clipboard = SimpleNamespace(
            inject=lambda payload: server.injected.append(payload),
        )
        server.paste_coordinator = PasteCoordinator(server._request_remote_file_paste)
        server.clipboard_hub = ClipboardHub("server")
        self.addCleanup(server.clipboard_hub.stop)
        # Hold endpoint publication at the precise boundary between accepting a
        # remote copy and its background delivery to the server clipboard.
        server.clipboard_hub.pause_delivery()
        server.clipboard_hub.register_endpoint(
            "server", server._deliver_clipboard_to_server
        )
        for machine in ("client-a", "client-b"):
            server.clipboard_hub.register_endpoint(
                machine, lambda item: True, source_domain=f"session-{machine}"
            )
        return server

    @staticmethod
    def remote_offer(server, sequence=20):
        return server.on_remote_clipboard_offer({
            "type": "clipboard_offer",
            "session_id": "session-client-a",
            "peer_identity": "client-a",
            "kind": "files",
            "sequence": sequence,
        })

    def test_first_paste_routes_accepted_files_before_background_delivery(self):
        for destination in ("server", "client-b"):
            with self.subTest(destination=destination):
                server = self.make_server(destination)
                self.assertTrue(self.remote_offer(server))

                server.on_key_press({"type": "special", "value": "ctrl_l"})
                server.on_key_press({"type": "char", "value": "v"})

                self.assertNotIn("v", [key["value"] for key in server.forwarded])
                if destination == "server":
                    self.assertEqual(server.paste_requests, [True])
                else:
                    self.assertEqual(server.messages, [
                        ("session-client-b", {"type": "file_paste_intent"})
                    ])

    def test_delayed_ordinary_delivery_cannot_replace_newer_file_offer(self):
        server = self.make_server("server")
        old_item = server.clipboard_hub.accept_ordinary(
            "client-a", 19,
            ClipboardSnapshot([ClipboardEntry("unicode_text", b"old copy")]),
            source_domain="session-client-a",
        )
        self.assertTrue(self.remote_offer(server))

        self.assertFalse(server._deliver_clipboard_to_server(old_item))

        self.assertEqual(server.injected, [])
        self.assertEqual(server.clipboard_offer_state.current_offer.kind, "files")
        self.assertEqual(
            server.clipboard_offer_state.current_offer.revision,
            server.clipboard_hub.latest_item.revision,
        )

    def test_inflight_ordinary_delivery_cannot_overwrite_newer_offer(self):
        for newer_copy in ("remote-files", "local-files", "local-text"):
            with self.subTest(newer_copy=newer_copy):
                server = self.make_server("client-b")
                server._get_clipboard_offer_state()
                snapshot = ClipboardSnapshot([
                    ClipboardEntry("unicode_text", b"old copy")
                ])
                old_item = server.clipboard_hub.accept_ordinary(
                    "client-a", 19, snapshot,
                    source_domain="session-client-a",
                )
                old_validated = threading.Event()
                release_old = threading.Event()
                newer_started = threading.Event()
                newer_finished = threading.Event()
                errors = []
                source, first_line = inspect.getsourcelines(
                    ClipboardOfferState.accept_cluster
                )
                commit_line = first_line + next(
                    index for index, line in enumerate(source)
                    if "offer = ClipboardOffer(" in line
                )

                def pause_after_validation(frame, event, arg):
                    if (
                        frame.f_code is ClipboardOfferState.accept_cluster.__code__
                        and event == "line"
                        and frame.f_lineno == commit_line
                    ):
                        old_validated.set()
                        if not release_old.wait(2):
                            raise RuntimeError("old delivery was not released")
                    return pause_after_validation

                def deliver_old():
                    sys.settrace(pause_after_validation)
                    try:
                        server._deliver_clipboard_to_server(old_item)
                    except Exception as error:
                        errors.append(error)
                    finally:
                        sys.settrace(None)

                def copy_newer():
                    newer_started.set()
                    try:
                        if newer_copy == "remote-files":
                            self.remote_offer(server)
                        elif newer_copy == "local-files":
                            server.on_local_clipboard_offer("files", 30)
                        else:
                            server._send_clipboard_snapshot({
                                "snapshot": snapshot, "source_sequence": 30,
                            })
                    except Exception as error:
                        errors.append(error)
                    finally:
                        newer_finished.set()

                old_worker = threading.Thread(target=deliver_old)
                new_worker = threading.Thread(target=copy_newer)
                old_worker.start()
                try:
                    self.assertTrue(old_validated.wait(1))
                    new_worker.start()
                    self.assertTrue(newer_started.wait(1))
                    self.assertFalse(newer_finished.wait(0.05))
                finally:
                    release_old.set()
                    old_worker.join(2)
                    if new_worker.ident is not None:
                        new_worker.join(2)

                self.assertFalse(old_worker.is_alive())
                self.assertFalse(new_worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(
                    server.clipboard_offer_state.current_offer.revision,
                    server.clipboard_hub.latest_item.revision,
                )
                self.assertEqual(
                    server.clipboard_offer_state.current_offer.kind,
                    "ordinary" if newer_copy == "local-text" else "files",
                )


if __name__ == "__main__":
    unittest.main()
