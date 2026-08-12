import os
import unittest

from app.file_transfer.compression import CompressionError, decode_chunk, encode_chunk, should_compress


class CompressionTests(unittest.TestCase):
    def test_skips_small_and_already_compressed_files(self):
        self.assertFalse(should_compress("notes.txt", 100, b"a" * 100))
        self.assertFalse(should_compress("archive.zip", 2_000_000, b"a" * 262_144))
        self.assertFalse(should_compress("photo.jpg", 2_000_000, b"a" * 262_144))

    def test_uses_sample_only_when_savings_are_at_least_twelve_percent(self):
        self.assertTrue(should_compress("data.csv", 2_000_000, b"a" * 262_144))
        self.assertFalse(should_compress("random.bin", 2_000_000, os.urandom(262_144)))

    def test_chunk_round_trip_is_bounded_and_preserves_original_bytes(self):
        original = b"Conduit" * 10_000
        encoded = encode_chunk(original, compress=True)

        self.assertTrue(encoded.compressed)
        self.assertEqual(decode_chunk(encoded.data, True, len(original)), original)
        with self.assertRaises(CompressionError):
            decode_chunk(encoded.data, True, len(original) - 1)


if __name__ == "__main__":
    unittest.main()
