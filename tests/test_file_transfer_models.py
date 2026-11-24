import unittest

from app.file_transfer.models import FileItem, ItemType, Manifest


class ManifestTests(unittest.TestCase):
    def test_manifest_is_immutable_and_never_serializes_local_source_paths(self):
        item = FileItem(
            relative_path="folder/report.txt",
            item_type=ItemType.FILE,
            size=4,
            modified_ns=123,
            sha256="0" * 64,
            local_source_path=r"C:\\private\\report.txt",
        )

        manifest = Manifest.create([item])
        wire = manifest.to_wire()

        self.assertIsInstance(manifest.items, tuple)
        self.assertEqual(manifest.total_size, 4)
        self.assertEqual(manifest.file_count, 1)
        self.assertNotIn("local_source_path", wire["items"][0])
        self.assertNotIn("C:\\private", repr(wire))
        self.assertEqual(Manifest.from_wire(wire).items[0].sha256, "0" * 64)
        with self.assertRaises(AttributeError):
            manifest.total_size = 8


if __name__ == "__main__":
    unittest.main()
