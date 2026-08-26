import unittest
from types import SimpleNamespace

from app.file_transfer.cluster_router import ClusterCommandBroadcaster
from app.server import ConduitServer


class ClusterCommandTests(unittest.TestCase):
    def test_release_precedes_best_effort_two_client_broadcast_and_cleanup(self):
        events = []
        sessions = (
            SimpleNamespace(session_id="one"),
            SimpleNamespace(session_id="two"),
        )

        def send(session_id, message):
            events.append(("send", session_id, message["type"]))
            return session_id == "one"

        commands = ClusterCommandBroadcaster(
            ready_sessions=lambda: sessions,
            send=send,
            release_input=lambda: events.append(("release",)),
            local_cleanup=lambda command: events.append(
                ("cleanup", command["type"])
            ),
        )

        result = commands.broadcast("reload_connection")

        self.assertEqual(events[0], ("release",))
        self.assertEqual(events[-1], ("cleanup", "reload_connection"))
        self.assertEqual(result.delivered, ("one",))
        self.assertEqual(result.failed, ("two",))

    def test_shutdown_and_background_use_unique_command_ids(self):
        messages = []
        commands = ClusterCommandBroadcaster(
            ready_sessions=lambda: (SimpleNamespace(session_id="one"),),
            send=lambda session_id, message: messages.append(message) or True,
        )

        commands.broadcast("shutdown_app")
        commands.broadcast("set_daemon_mode", {"hidden": True})

        self.assertNotEqual(messages[0]["command_id"], messages[1]["command_id"])
        self.assertEqual(messages[1]["hidden"], True)

    def test_server_targets_every_ready_session_and_resumes_after_background_sync(self):
        events = []

        class Registry:
            def ready_sessions(self):
                return (
                    SimpleNamespace(session_id="one"),
                    SimpleNamespace(session_id="two"),
                )

        class Network:
            def send_message(self, message, session_id=None):
                events.append(("send", session_id, message["type"]))
                return session_id == "one"

        class Router:
            def resume(self):
                events.append(("resume",))
                return True

        server = ConduitServer.__new__(ConduitServer)
        server.session_registry = Registry()
        server.control_network = Network()
        server.input_router = Router()
        server.prepare_app_shutdown = lambda: events.append(("release",))

        result = server.broadcast_cluster_command(
            "set_daemon_mode",
            {"hidden": True},
        )

        self.assertEqual(events[0], ("release",))
        self.assertEqual(events[-1], ("resume",))
        self.assertEqual(result.delivered, ("one",))
        self.assertEqual(result.failed, ("two",))


if __name__ == "__main__":
    unittest.main()
