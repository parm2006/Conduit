import unittest

from app.file_transfer.rate_control import BalancedRateController


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


if __name__ == "__main__":
    unittest.main()
