import unittest
from unittest.mock import MagicMock
from app.gui import EntryUndoManager, enable_textbox_qol


class DummyEntry:
    def __init__(self, initial=""):
        self.text = initial
        self.cursor_pos = len(initial)
        self.selected_range = None
        self.bindings = {}

    def get(self):
        return self.text

    def delete(self, first, last=None):
        if first == 0 and (last == 'end' or last == len(self.text)):
            self.text = ""
            self.cursor_pos = 0
        else:
            first_idx = int(first)
            last_idx = len(self.text) if last in (None, 'end') else int(last)
            self.text = self.text[:first_idx] + self.text[last_idx:]
            self.cursor_pos = first_idx

    def insert(self, index, string):
        idx = len(self.text) if index in ('end', None) else int(index)
        self.text = self.text[:idx] + string + self.text[idx:]
        self.cursor_pos = idx + len(string)

    def index(self, position):
        if position == 'insert':
            return self.cursor_pos
        if position == 'end':
            return len(self.text)
        return int(position)

    def select_range(self, start, end):
        self.selected_range = (start, end)

    def icursor(self, pos):
        if pos == 'end':
            self.cursor_pos = len(self.text)
        else:
            self.cursor_pos = int(pos)

    def bind(self, sequence, func, add=None):
        self.bindings[sequence] = func


class GuiTextboxQolTests(unittest.TestCase):
    def test_undo_manager_tracks_changes_and_restores_snapshots(self):
        entry = DummyEntry("5000")
        mgr = EntryUndoManager(entry)

        entry.text = "50001"
        mgr.on_change()

        entry.text = "500012"
        mgr.on_change()

        self.assertEqual(entry.get(), "500012")

        mgr.undo()
        self.assertEqual(entry.get(), "50001")

        mgr.undo()
        self.assertEqual(entry.get(), "5000")

        mgr.redo()
        self.assertEqual(entry.get(), "50001")

    def test_enable_textbox_qol_registers_bindings(self):
        entry = DummyEntry("127.0.0.1")
        mgr = enable_textbox_qol(entry)

        self.assertIsNotNone(mgr)
        self.assertIn("<Control-a>", entry.bindings)
        self.assertIn("<Control-z>", entry.bindings)
        self.assertIn("<Control-y>", entry.bindings)
        self.assertIn("<Control-BackSpace>", entry.bindings)
        self.assertIn("<Control-Delete>", entry.bindings)
        self.assertIn("<Button-3>", entry.bindings)

    def test_select_all_binding_selects_full_range(self):
        entry = DummyEntry("password123")
        enable_textbox_qol(entry)

        select_all_fn = entry.bindings["<Control-a>"]
        res = select_all_fn(MagicMock())

        self.assertEqual(res, "break")
        self.assertEqual(entry.selected_range, (0, 'end'))
        self.assertEqual(entry.cursor_pos, len("password123"))

    def test_delete_word_left_removes_previous_word(self):
        entry = DummyEntry("hello world test")
        enable_textbox_qol(entry)

        delete_left_fn = entry.bindings["<Control-BackSpace>"]
        res = delete_left_fn(MagicMock())

        self.assertEqual(res, "break")
        self.assertEqual(entry.get(), "hello world ")

    def test_get_local_ip_addresses_returns_valid_ip_list(self):
        from app.gui import get_local_ip_addresses
        ips = get_local_ip_addresses()
        self.assertIsInstance(ips, list)
        self.assertGreater(len(ips), 0)
        self.assertTrue(all(isinstance(ip, str) for ip in ips))


if __name__ == "__main__":
    unittest.main()
