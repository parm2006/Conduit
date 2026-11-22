import unittest
from scripts.benchmark_networking import summarize, run_scenario

class NetworkingBenchmarkTests(unittest.TestCase):
    def test_summary_uses_observed_samples_and_reports_missing(self):
        result = summarize({0: 1.0, 2: 3.0}, offered=4)
        self.assertEqual(result['observed'], 2)
        self.assertEqual(result['coalesced_or_undelivered'], 2)
        self.assertEqual(result['round_trip_ms']['p50'], 1.0)
        self.assertEqual(result['round_trip_ms']['p95'], 3.0)

    def test_no_observations_are_not_reported_as_zero_latency(self):
        self.assertIsNone(summarize({}, offered=4)['round_trip_ms'])

    def test_invalid_workload_rejected_before_opening_listeners(self):
        for kwargs in ({'samples': 0}, {'interval_ms': -1}, {'transport': 'unknown'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_scenario(**kwargs)

    def test_real_tls_and_udp_smoke(self):
        import os
        if os.name != 'nt':
            self.skipTest('Production identity protection requires Windows')
        for transport in ('tls', 'udp'):
            with self.subTest(transport=transport):
                result = run_scenario(samples=12, interval_ms=2, transport=transport)
                self.assertEqual(result['transport'], transport)
                self.assertGreater(result['observed'], 0)
                self.assertTrue(result['final_position_observed'])
                self.assertEqual(result['queue']['failed'], 0)
                self.assertEqual(result['file_bytes_acknowledged'], 0)
                self.assertGreater(result['actual_input_sends'][transport], 0)

    def test_file_load_uses_real_file_lane_and_ack_window(self):
        import os
        if os.name != 'nt':
            self.skipTest('Production identity protection requires Windows')
        result = run_scenario(samples=20, interval_ms=2, transport='udp', bulk=True)
        self.assertGreater(result['file_bytes_acknowledged'], 0)
        self.assertGreater(result['file_mib_per_second'], 0)
        self.assertTrue(result['final_position_observed'])

    def test_reliable_relative_and_keyboard_workloads_keep_all_samples(self):
        import os
        if os.name != 'nt':
            self.skipTest('Production identity protection requires Windows')
        for input_kind in ('relative', 'keyboard'):
            with self.subTest(input_kind=input_kind):
                result = run_scenario(samples=20, interval_ms=1, transport='tls', input_kind=input_kind)
                self.assertEqual(result['observed'], 20)
                self.assertTrue(result['delivery_order_valid'])

    def test_missing_file_acks_cancel_worker_before_return(self):
        import os
        import threading
        from unittest.mock import patch
        from app.file_transfer.transport import FileLaneClient
        if os.name != 'nt':
            self.skipTest('Production identity protection requires Windows')
        original = FileLaneClient.send
        def drop_acks(lane, metadata, payload=b''):
            if metadata.get('type') != 'chunk_received':
                return original(lane, metadata, payload)
        with patch.object(FileLaneClient, 'send', drop_acks):
            with self.assertRaisesRegex(RuntimeError, 'Benchmark incomplete'):
                run_scenario(samples=3, interval_ms=1, bulk=True, transport='tls')
        self.assertFalse(any(t.name == 'benchmark-file-load' for t in threading.enumerate()))
