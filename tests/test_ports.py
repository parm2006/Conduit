import unittest
import tempfile
from pathlib import Path
import inspect

from app.ports import (
    DEFAULT_BASE_PORT,
    DEFAULT_DATA_PORT,
    DEFAULT_FILE_PORT,
)
from app.preferences import UserPreferences
from app.network import NetworkServer
from app.server import ConduitServer
from app.file_transfer.transport import FileLaneServer
from app.session import SessionRegistry


class DefaultPortTests(unittest.TestCase):
    def test_default_lanes_are_the_requested_three_consecutive_ports(self):
        self.assertEqual(DEFAULT_BASE_PORT, 28903)
        self.assertEqual(DEFAULT_DATA_PORT, 28904)
        self.assertEqual(DEFAULT_FILE_PORT, 28905)

    def test_preferences_fall_back_to_the_requested_default_base_port(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                UserPreferences(Path(directory)).load_server_port(),
                DEFAULT_BASE_PORT,
            )

    def test_listener_defaults_use_the_canonical_three_lane_ports(self):
        self.assertEqual(
            inspect.signature(NetworkServer).parameters["port"].default,
            DEFAULT_BASE_PORT,
        )
        self.assertEqual(
            inspect.signature(ConduitServer).parameters["port"].default,
            DEFAULT_BASE_PORT,
        )
        self.assertEqual(
            inspect.signature(FileLaneServer).parameters["port"].default,
            DEFAULT_FILE_PORT,
        )

    def test_two_client_server_composition_adds_no_fourth_listener(self):
        source = inspect.getsource(ConduitServer.__init__)

        self.assertEqual(source.count("NetworkServer("), 2)
        self.assertEqual(source.count("FileLaneServer("), 1)
        self.assertIn("port + 1", source)
        self.assertIn("port + 2", source)

    def test_session_capacity_and_pending_candidate_timeout_are_bounded(self):
        parameters = inspect.signature(SessionRegistry).parameters

        self.assertEqual(parameters["capacity"].default, 2)
        self.assertEqual(parameters["candidate_timeout"].default, 15.0)


if __name__ == "__main__":
    unittest.main()
