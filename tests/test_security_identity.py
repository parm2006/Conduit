import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from app.crypto import IdentityStore


class FakeProtector:
    prefix = b"conduit-protected:"

    def protect(self, value):
        return self.prefix + bytes(value)[::-1]

    def unprotect(self, value):
        value = bytes(value)
        if not value.startswith(self.prefix):
            raise ValueError("not protected")
        return value[len(self.prefix):][::-1]


class IdentityStoreTests(unittest.TestCase):
    def make_store(self, root, legacy=None):
        return IdentityStore(
            root=Path(root),
            legacy_root=Path(legacy) if legacy else False,
            protector=FakeProtector(),
        )

    def test_generates_encrypted_private_key_without_plaintext_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = self.make_store(root).load_or_create()

            key_bytes = material.key_path.read_bytes()
            self.assertNotIn(b"BEGIN RSA PRIVATE KEY", key_bytes)
            self.assertNotIn(b"BEGIN PRIVATE KEY", key_bytes)
            password_blob = material.password_path.read_bytes()
            self.assertTrue(password_blob.startswith(FakeProtector.prefix))
            private_key = serialization.load_pem_private_key(
                key_bytes,
                password=material.password,
            )
            self.assertIsNotNone(private_key)
            self.assertFalse(material.recovered)
            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_reloads_the_same_valid_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            first = store.load_or_create()
            second = store.load_or_create()

            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertEqual(first.password, second.password)
            self.assertFalse(second.recovered)

    def test_corrupt_identity_is_quarantined_and_regenerated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root)
            first = store.load_or_create()
            first.key_path.write_bytes(b"corrupt")

            recovered = store.load_or_create()

            self.assertTrue(recovered.recovered)
            self.assertNotEqual(first.fingerprint, recovered.fingerprint)
            quarantined = list((root / "quarantine").glob("*/key.pem"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b"corrupt")

    def test_invalid_generation_pointer_cannot_quarantine_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "identity"
            unrelated = base / "unrelated"
            unrelated.mkdir()
            marker = unrelated / "must-stay.txt"
            marker.write_text("unrelated data", encoding="utf-8")
            root.mkdir()
            (root / "current.json").write_text(
                json.dumps({"generation": "../../unrelated"}),
                encoding="utf-8",
            )

            recovered = self.make_store(root).load_or_create()

            self.assertTrue(recovered.recovered)
            self.assertTrue(marker.exists(), "identity recovery moved an unrelated file")
            self.assertEqual(marker.read_text(encoding="utf-8"), "unrelated data")
            self.assertFalse(list((root / "quarantine").rglob("must-stay.txt")))

    def test_migrates_valid_legacy_plaintext_identity_and_removes_plaintext_key(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as legacy_dir:
            legacy = Path(legacy_dir)
            legacy_material = self.make_store(legacy).load_or_create()
            private_key = serialization.load_pem_private_key(
                legacy_material.key_path.read_bytes(),
                password=legacy_material.password,
            )
            plaintext = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            (legacy / "key.pem").write_bytes(plaintext)
            (legacy / "cert.pem").write_bytes(legacy_material.cert_path.read_bytes())

            migrated = self.make_store(root_dir, legacy).load_or_create()

            self.assertEqual(migrated.fingerprint, legacy_material.fingerprint)
            self.assertFalse((legacy / "key.pem").exists())
            self.assertNotIn(b"BEGIN PRIVATE KEY", migrated.key_path.read_bytes())
            self.assertNotIn(b"BEGIN RSA PRIVATE KEY", migrated.key_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
