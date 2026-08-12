import tempfile
import unittest
from pathlib import Path

from app.file_transfer.source import SourceChangedError, SourceFile
from app.file_transfer.compression import MAX_CHUNK_SIZE


class SourceFileTests(unittest.TestCase):
    def test_default_network_burst_is_at_most_256_kib(self):
        self.assertLessEqual(MAX_CHUNK_SIZE, 256 * 1024)

    def test_streams_bounded_chunks_and_preserves_snapshot_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.bin"
            path.write_bytes(b"abcdefghij")
            source = SourceFile.snapshot(path, chunk_size=4)

            self.assertEqual(list(source.iter_chunks()), [b"abcd", b"efgh", b"ij"])
            self.assertEqual(source.size, 10)
            self.assertEqual(len(source.sha256), 64)

    def test_detects_mutation_or_deletion_after_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.bin"
            path.write_bytes(b"original")
            changed = SourceFile.snapshot(path)
            path.write_bytes(b"changed-longer")
            with self.assertRaises(SourceChangedError):
                list(changed.iter_chunks())

            deleted = SourceFile.snapshot(path)
            path.unlink()
            with self.assertRaises(SourceChangedError):
                list(deleted.iter_chunks())


if __name__ == "__main__":
    unittest.main()
