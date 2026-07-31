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
from app.server import DeskFlowServer
from app.file_transfer.transport import FileLaneServer


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
            inspect.signature(DeskFlowServer).parameters["port"].default,
            DEFAULT_BASE_PORT,
        )
        self.assertEqual(
            inspect.signature(FileLaneServer).parameters["port"].default,
            DEFAULT_FILE_PORT,
        )


if __name__ == "__main__":
    unittest.main()
