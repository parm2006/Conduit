import unittest

from app.file_transfer.rate_control import BalancedRateController, AckWindowController


class BalancedRateControllerTests(unittest.TestCase):
    def test_throttles_at_latency_thresholds_and_recovers_gradually(self):
        controller = BalancedRateController(spare_bytes_per_second=10_000_000, baseline_rtt_ms=10)

        self.assertEqual(controller.allowed_bytes_per_second, 5_000_000)
        self.assertGreater(controller.observe_rtt(26), 0)
        reduced = controller.allowed_bytes_per_second
        self.assertLess(reduced, 5_000_000)

        controller.observe_rtt(51)
        self.assertLess(controller.allowed_bytes_per_second, reduced)
        self.assertEqual(controller.observe_rtt(111), 0)

        controller.observe_rtt(10)
        first_recovery = controller.allowed_bytes_per_second
        self.assertGreater(first_recovery, 0)
        self.assertLess(first_recovery, 5_000_000)
        for _ in range(20):
            controller.observe_rtt(10)
        self.assertEqual(controller.allowed_bytes_per_second, 5_000_000)

    def test_repeated_stalls_pause_file_chunks(self):
        controller = BalancedRateController(1_000_000, 10)

        controller.note_control_stall()
        controller.note_control_stall()

        self.assertEqual(controller.allowed_bytes_per_second, 0)


class AckWindowControllerTests(unittest.TestCase):
    def test_clean_acknowledgments_grow_window_up_to_cap(self):
        controller = AckWindowController(1024, minimum=256)
        self.assertEqual(controller.window, 512)
        for index in range(100):
            controller.observe_ack(256, 0.005, index * 0.005)
        self.assertEqual(controller.window, 1024)

    def test_delay_inflation_reduces_once_per_feedback_interval(self):
        controller = AckWindowController(1024, minimum=256)
        controller.observe_ack(256, 0.005, 0)
        controller.observe_ack(256, 0.030, 1)
        self.assertEqual(controller.window, 256)
        controller.observe_ack(256, 0.030, 1.001)
        self.assertEqual(controller.window, 256)
        for index in range(20):
            controller.observe_ack(256, 0.005, 2 + index * 0.005)
        self.assertEqual(controller.window, 1024)

    def test_small_explicit_window_is_preserved(self):
        controller = AckWindowController(8)
        self.assertEqual(controller.window, 8)
        controller.observe_ack(4, 0.005, 0)
        controller.observe_ack(4, 0.1, 1)
        self.assertEqual(controller.window, 8)

    def test_window_never_exceeds_non_multiple_cap(self):
        controller = AckWindowController(700, minimum=256)
        for index in range(100):
            controller.observe_ack(256, 0.005, index)
        self.assertEqual(controller.window, 700)


if __name__ == "__main__":
    unittest.main()
