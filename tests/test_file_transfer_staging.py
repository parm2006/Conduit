import hashlib
import tempfile
import unittest
from pathlib import Path

from app.file_transfer.staging import (
    IntegrityError, StagedFile, cleanup_staging_root,
)


class StagedFileTests(unittest.TestCase):
    def test_startup_cleanup_removes_unrecoverable_partial_and_completed_ciphertext(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partial = root / "partial" / "old" / "x.partial"
            completed = root / "completed" / "old" / "x.cache"
            partial.parent.mkdir(parents=True)
            completed.parent.mkdir(parents=True)
            partial.write_bytes(b"ciphertext")
            completed.write_bytes(b"ciphertext")

            cleanup_staging_root(root)

            self.assertFalse((root / "partial").exists())
            self.assertFalse((root / "completed").exists())

    def test_tail_range_lookup_visits_only_the_intersecting_record(self):
        class CountingRecords(list):
            def __init__(self, values):
                super().__init__(values)
                self.lookups = 0

            def __getitem__(self, index):
                if isinstance(index, slice):
                    raise AssertionError("range reads must not copy the record suffix")
                self.lookups += 1
                return super().__getitem__(index)

        chunks = [bytes([index]) * 32 for index in range(100)]
        content = b"".join(chunks)
        with tempfile.TemporaryDirectory() as directory:
            staged = StagedFile(
                Path(directory), "job", "safe.bin", len(content),
                hashlib.sha256(content).hexdigest(),
            )
            for index, chunk in enumerate(chunks):
                staged.write(index * len(chunk), chunk)
            staged._records = CountingRecords(staged._records)

            self.assertEqual(staged.read_available(len(content) - 32, 32), chunks[-1])
            self.assertEqual(staged._records.lookups, 1)
            staged.abort()

    def test_writes_in_order_verifies_hash_and_finalizes_without_overwrite(self):
        content = b"Conduit file bytes"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = StagedFile(root, "job-1", "folder/report.txt", len(content), digest)

            staged.write(0, content[:8])
            staged.write(8, content[8:])
            completed = staged.finalize()

            self.assertEqual(completed.read_available(0, len(content)), content)
            self.assertFalse(staged.partial_path.exists())
            self.assertNotIn(content, completed.storage_path.read_bytes())

            duplicate = StagedFile(root, "job-1", "folder/report.txt", len(content), digest)
            duplicate.write(0, content)
            other = duplicate.finalize()
            self.assertNotEqual(completed.storage_path, other.storage_path)
            duplicate.abort()

    def test_rejects_out_of_order_or_oversized_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            staged = StagedFile(Path(directory), "job", "safe.bin", 4, hashlib.sha256(b"data").hexdigest())
            with self.assertRaisesRegex(ValueError, "offset"):
                staged.write(1, b"d")
            with self.assertRaisesRegex(ValueError, "declared size"):
                staged.write(0, b"extra")
            staged.abort()

    def test_hash_mismatch_never_publishes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = StagedFile(root, "job", "safe.bin", 4, "0" * 64)
            staged.write(0, b"data")

            with self.assertRaises(IntegrityError):
                staged.finalize()

            self.assertFalse((root / "completed" / "job" / "safe.bin").exists())
            self.assertFalse(staged.partial_path.exists())

    def test_random_range_reads_cross_encrypted_chunk_records(self):
        content = b"0123456789abcdefghijklmnopqrstuvwxyz"
        with tempfile.TemporaryDirectory() as directory:
            staged = StagedFile(
                Path(directory), "job", "safe.bin", len(content),
                hashlib.sha256(content).hexdigest(),
            )
            staged.write(0, content[:10])
            staged.write(10, content[10:23])
            staged.write(23, content[23:])

            self.assertEqual(staged.read_available(7, 21), content[7:28])
            self.assertEqual(staged.read_available(17, 5), content[17:22])
            staged.abort()

    def test_tampered_ciphertext_is_rejected(self):
        content = b"authenticated bytes"
        with tempfile.TemporaryDirectory() as directory:
            staged = StagedFile(
                Path(directory), "job", "safe.bin", len(content),
                hashlib.sha256(content).hexdigest(),
            )
            staged.write(0, content)
            staged._file.flush()
            with staged.partial_path.open("r+b") as target:
                target.seek(-1, 2)
                original = target.read(1)
                target.seek(-1, 2)
                target.write(bytes([original[0] ^ 1]))
            with self.assertRaises(IntegrityError):
                staged.read_available(0, len(content))
            staged.abort()


if __name__ == "__main__":
    unittest.main()
