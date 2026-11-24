import unittest

from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.validation import ValidationError, validate_manifest, validate_relative_path


class RelativePathValidationTests(unittest.TestCase):
    def test_rejects_paths_that_can_escape_or_address_special_windows_locations(self):
        unsafe_paths = (
            "../secret.txt",
            "folder/../../secret.txt",
            r"C:\secret.txt",
            r"\\server\share\secret.txt",
            "/rooted.txt",
            "report.txt:payload",
            "CON.txt",
            "folder/NUL",
        )

        for path in unsafe_paths:
            with self.subTest(path=path), self.assertRaises(ValidationError):
                validate_relative_path(path)

    def test_accepts_nested_unicode_relative_path(self):
        self.assertEqual(validate_relative_path("資料/reports/summary.txt"), "資料/reports/summary.txt")


class ManifestValidationTests(unittest.TestCase):
    def test_rejects_job_ids_outside_the_generated_32_lowercase_hex_format(self):
        item = FileItem("safe.txt", ItemType.FILE, 1, 1, "0" * 64)
        invalid_ids = ("", "a" * 31, "a" * 33, "A" * 32, "g" * 32)

        for job_id in invalid_ids:
            manifest = Manifest(job_id, (item,), total_size=1, file_count=1)
            with self.subTest(job_id=job_id), self.assertRaisesRegex(
                ValidationError, "job ID"
            ):
                validate_manifest(manifest)

    def test_rejects_sender_supplied_totals_that_do_not_match_items(self):
        item = FileItem("safe.txt", ItemType.FILE, 4, 123, "0" * 64)
        manifest = Manifest("a" * 32, (item,), total_size=5, file_count=1)

        with self.assertRaisesRegex(ValidationError, "total size"):
            validate_manifest(manifest)

    def test_rejects_duplicate_paths_case_insensitively(self):
        manifest = Manifest.create(
            [
                FileItem("Report.txt", ItemType.FILE, 1, 1, "0" * 64),
                FileItem("report.TXT", ItemType.FILE, 1, 1, "1" * 64),
            ]
        )

        with self.assertRaisesRegex(ValidationError, "duplicate"):
            validate_manifest(manifest)

    def test_rejects_missing_or_invalid_file_hash(self):
        manifest = Manifest.create([FileItem("safe.txt", ItemType.FILE, 1, 1, "bad")])

        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
