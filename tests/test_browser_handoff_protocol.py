import unittest

from app.browser_handoff.protocol import (
    BrowserHandoffProtocolError,
    MAX_URL_BYTES,
    validate_request,
    validate_candidate,
    validate_result,
)


class BrowserHandoffProtocolTests(unittest.TestCase):
    def candidate(self, **overrides):
        value = {
            "protocol": 1,
            "gesture_id": "b" * 32,
            "source_display_id": "display-1",
            "source_side": "right",
            "topology_version": 4,
            "incognito": False,
            "total_count": 1,
            "entries": [{"source_index": 0, "url": "https://example.test/a", "active": True}],
            "complete_capture": True,
        }
        value.update(overrides)
        return value

    def test_accepts_candidate_without_destination_authority(self):
        candidate = validate_candidate(self.candidate())

        self.assertEqual(candidate.gesture_id, "b" * 32)
        self.assertEqual(candidate.source_side, "right")
        self.assertEqual(candidate.entries[0].url, "https://example.test/a")

    def test_rejects_candidate_with_invalid_side_or_gesture(self):
        for message in (self.candidate(source_side="diagonal"), self.candidate(gesture_id="not-a-gesture")):
            with self.subTest(message=message):
                with self.assertRaises(BrowserHandoffProtocolError):
                    validate_candidate(message)

    def request(self, **overrides):
        value = {
            "protocol": 1,
            "request_id": "a" * 32,
            "route_ticket": "ticket-1",
            "topology_version": 4,
            "destination_machine_id": "client-b",
            "incognito": False,
            "total_count": 2,
            "entries": [
                {"source_index": 0, "url": "https://example.test/a", "active": False},
                {"source_index": 1, "url": "https://example.test/a", "active": True},
            ],
            "complete_capture": True,
        }
        value.update(overrides)
        return value

    def test_accepts_duplicate_urls_and_preserves_source_indexes(self):
        request = validate_request(self.request())

        self.assertEqual([entry.source_index for entry in request.entries], [0, 1])
        self.assertEqual(request.entries[0].url, request.entries[1].url)

    def test_rejects_bool_as_integer_unknown_version_and_duplicate_indexes(self):
        bad_bool = self.request(total_count=True)
        protocol_bool = self.request(protocol=True)
        bad_version = self.request(protocol=2)
        duplicate = self.request(entries=[
            {"source_index": 0, "url": "https://example.test/a", "active": False},
            {"source_index": 0, "url": "https://example.test/b", "active": True},
        ])

        for message in (bad_bool, protocol_bool, bad_version, duplicate):
            with self.subTest(message=message):
                with self.assertRaises(BrowserHandoffProtocolError):
                    validate_request(message)

    def test_rejects_unknown_fields_and_unicode_urls_over_the_utf8_limit(self):
        unknown = self.request(unexpected=True)
        too_large = self.request(
            total_count=1,
            entries=[{"source_index": 0, "url": "https://" + "é" * MAX_URL_BYTES, "active": True}],
        )

        for message in (unknown, too_large):
            with self.subTest(message=message):
                with self.assertRaises(BrowserHandoffProtocolError):
                    validate_request(message)

    def test_requires_all_source_indexes_for_a_complete_capture(self):
        message = self.request(
            total_count=2,
            entries=[{"source_index": 0, "url": "https://example.test/a", "active": True}],
            complete_capture=True,
        )

        with self.assertRaises(BrowserHandoffProtocolError):
            validate_request(message)

    def test_accepts_a_url_free_partial_result_and_rejects_inconsistent_counts(self):
        result = {
            "request_id": "a" * 32,
            "route_ticket": "ticket-1",
            "status": "partial",
            "opened_count": 1,
            "total_count": 2,
            "entries": [{"source_index": 1, "reason": "blocked_scheme"}],
            "receiver_epoch": "epoch-1",
        }

        validated = validate_result(result)
        self.assertEqual(validated.status, "partial")
        with self.assertRaises(BrowserHandoffProtocolError):
            validate_result({**result, "opened_count": 3})
        with self.assertRaises(BrowserHandoffProtocolError):
            validate_result({**result, "entries": [{"source_index": 1, "reason": "blocked", "url": "https://secret"}]})


if __name__ == "__main__":
    unittest.main()
