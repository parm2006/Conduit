import unittest


class HashingTests(unittest.TestCase):
    def test_blake3_known_vector_and_incremental_updates(self):
        from app.hashing import new_hasher
        expected = '6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85'
        self.assertEqual(new_hasher(b'abc').hexdigest(), expected)
        digest = new_hasher()
        digest.update(b'a')
        digest.update(b'bc')
        self.assertEqual(digest.hexdigest(), expected)

    def test_clipboard_uses_blake3_without_changing_ordering(self):
        from blake3 import blake3
        from app.clipboard_formats import ClipboardEntry, ClipboardSnapshot
        entries = (ClipboardEntry('unicode_text', b'hello'), ClipboardEntry('html', b'<b>hello</b>'))
        expected = blake3(b'html<b>hello</b>unicode_texthello').hexdigest()
        self.assertEqual(ClipboardSnapshot(entries).fingerprint(), expected)
        self.assertEqual(ClipboardSnapshot(tuple(reversed(entries))).fingerprint(), expected)
