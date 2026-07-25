import tempfile
import unittest
from pathlib import Path

from app.trust import PeerTrustStore


class PeerTrustStoreTests(unittest.TestCase):
    def test_commits_loads_and_clears_a_canonical_peer_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = PeerTrustStore(root)
            peer = store.peer_id("  EXAMPLE.local ", 5000)

            store.commit(peer, "aa" * 32)

            self.assertEqual(store.load(peer), "aa" * 32)
            files = list(root.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].parent.resolve(), root.resolve())
            self.assertNotIn("example.local", files[0].name)
            self.assertTrue(store.clear(peer))
            self.assertIsNone(store.load(peer))

    def test_rejects_invalid_fingerprints_without_changing_existing_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PeerTrustStore(directory)
            peer = store.peer_id("192.0.2.5", 5000)
            store.commit(peer, "11" * 32)

            with self.assertRaises(ValueError):
                store.commit(peer, "not-a-fingerprint")

            self.assertEqual(store.load(peer), "11" * 32)

    def test_load_migrates_and_removes_legacy_address_named_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = PeerTrustStore(root)
            peer = store.peer_id("192.0.2.10", 5000)
            legacy = root / "192.0.2.10.fingerprint"
            legacy.write_text("ab" * 32, encoding="ascii")

            self.assertEqual(store.load(peer), "ab" * 32)
            self.assertFalse(legacy.exists())
            self.assertTrue(store._path(peer).exists())

    def test_load_discards_invalid_legacy_address_named_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = PeerTrustStore(root)
            peer = store.peer_id("192.0.2.11", 5000)
            legacy = root / "192.0.2.11.fingerprint"
            legacy.write_text("not-a-fingerprint", encoding="ascii")

            self.assertIsNone(store.load(peer))
            self.assertFalse(legacy.exists())
            self.assertFalse(store._path(peer).exists())


if __name__ == "__main__":
    unittest.main()
