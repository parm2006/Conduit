import logging
import unittest
from unittest.mock import patch

import run
from app.gui import run_mainloop


class RuntimeLoggingTests(unittest.TestCase):
    def test_console_interrupt_runs_application_cleanup(self):
        events = []

        class App:
            def mainloop(self):
                events.append("mainloop")
                raise KeyboardInterrupt

            def on_close(self):
                events.append("close")

        with self.assertRaises(KeyboardInterrupt):
            run_mainloop(App())

        self.assertEqual(events, ["mainloop", "close"])

    def test_gui_entry_point_enables_privacy_safe_info_console_logging(self):
        with patch("run.logging.basicConfig") as configure:
            run.configure_runtime_logging()

        configure.assert_called_once_with(
            level=logging.INFO,
            format="%(levelname)s: %(message)s",
        )

    def test_reserved_firewall_prefix_dispatches_without_starting_gui(self):
        calls = []

        result = run.main(
            [
                "--conduit-firewall-helper",
                "inspect",
                "--base-port",
                "5000",
            ],
            helper_runner=lambda arguments: calls.append(
                ("helper", arguments)
            )
            or 17,
            gui_runner=lambda: calls.append(("gui",)),
        )

        self.assertEqual(result, 17)
        self.assertEqual(
            calls,
            [("helper", ["inspect", "--base-port", "5000"])],
        )

    def test_ordinary_launch_configures_logging_and_starts_gui(self):
        calls = []

        with patch("run.configure_runtime_logging") as configure:
            result = run.main([], gui_runner=lambda: calls.append("gui"))

        self.assertEqual(result, 0)
        configure.assert_called_once_with()
        self.assertEqual(calls, ["gui"])


if __name__ == "__main__":
    unittest.main()
