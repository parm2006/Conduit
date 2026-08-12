import tempfile
import unittest
from pathlib import Path

from app.file_transfer.explorer_session import (
    DestinationContext,
    ExplorerPasteSession,
    WindowSnapshot,
    WindowsExplorerAdapter,
)
from app.file_transfer.status import TransferPhase


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeExplorerAdapter:
    def __init__(self, destination, windows):
        self.destination = destination
        self.windows = {window.hwnd: window for window in windows}
        self.closed = []

    def capture_destination(self):
        return self.destination

    def list_windows(self):
        return tuple(self.windows.values())

    def window_snapshot(self, hwnd):
        return self.windows.get(hwnd)

    def close_window(self, hwnd):
        if hwnd not in self.windows:
            return False
        self.closed.append(hwnd)
        return True


class ExplorerPasteSessionTests(unittest.TestCase):
    destination_hwnd = 10
    destination_pid = 100

    @staticmethod
    def manifest(*relative_directories):
        return {
            "job_id": "a" * 32,
            "items": [
                {
                    "relative_path": relative_path,
                    "item_type": "directory",
                    "size": 0,
                    "modified_ns": 0,
                    "sha256": None,
                }
                for relative_path in relative_directories
            ],
            "total_size": 0,
            "file_count": 0,
        }

    def destination_window(self):
        return WindowSnapshot(
            self.destination_hwnd,
            self.destination_pid,
            owner_hwnd=None,
            root_owner_hwnd=self.destination_hwnd,
            visible=True,
        )

    def popup(self, hwnd=20, *, pid=None, owner=destination_hwnd, visible=True):
        return WindowSnapshot(
            hwnd,
            self.destination_pid if pid is None else pid,
            owner_hwnd=owner,
            root_owner_hwnd=owner,
            visible=visible,
        )

    def make_session(self, adapter, clock=None, grace=0.25):
        return ExplorerPasteSession.capture(
            self.manifest(),
            adapter,
            clock=clock or FakeClock(),
            resolution_grace=grace,
        )

    def test_exactly_one_new_owned_popup_is_correlated(self):
        destination = DestinationContext(
            self.destination_hwnd, self.destination_pid, None
        )
        adapter = FakeExplorerAdapter(destination, [self.destination_window()])
        session = self.make_session(adapter)

        adapter.windows[20] = self.popup()

        self.assertFalse(session.observe())
        self.assertEqual(session.popup_hwnd, 20)
        self.assertTrue(session.decision_pending)

    def test_unrelated_preexisting_other_process_ownerless_and_ambiguous_popups_are_rejected(self):
        destination = DestinationContext(
            self.destination_hwnd, self.destination_pid, None
        )
        cases = {
            "preexisting": ([self.destination_window(), self.popup()], []),
            "other_process": ([self.destination_window()], [self.popup(pid=200)]),
            "ownerless": ([self.destination_window()], [self.popup(owner=None)]),
            "ambiguous": (
                [self.destination_window()],
                [self.popup(20), self.popup(21)],
            ),
        }
        for name, (before, added) in cases.items():
            with self.subTest(name=name):
                adapter = FakeExplorerAdapter(destination, before)
                session = self.make_session(adapter)
                adapter.windows.update({window.hwnd: window for window in added})

                self.assertFalse(session.observe())
                self.assertIsNone(session.popup_hwnd)
                self.assertFalse(session.request_cancel())
                self.assertEqual(adapter.closed, [])

    def test_ambiguous_popup_set_is_never_narrowed_later(self):
        destination = DestinationContext(
            self.destination_hwnd, self.destination_pid, None
        )
        adapter = FakeExplorerAdapter(destination, [self.destination_window()])
        session = self.make_session(adapter)
        adapter.windows[20] = self.popup(20)
        adapter.windows[21] = self.popup(21)

        session.observe()
        adapter.windows.pop(21)

        self.assertFalse(session.observe())
        self.assertIsNone(session.popup_hwnd)

    def test_popup_close_without_positive_evidence_cancels_after_grace(self):
        clock = FakeClock()
        destination = DestinationContext(
            self.destination_hwnd, self.destination_pid, None
        )
        adapter = FakeExplorerAdapter(destination, [self.destination_window()])
        session = self.make_session(adapter, clock=clock)
        adapter.windows[20] = self.popup()
        session.observe()

        adapter.windows.pop(20)
        self.assertFalse(session.observe())
        clock.value = 0.24
        self.assertFalse(session.observe())
        clock.value = 0.25

        self.assertTrue(session.observe())
        self.assertTrue(session.inferred_cancelled)

    def test_stream_or_copy_evidence_during_close_grace_prevents_cancellation(self):
        for evidence in ("stream", "copy"):
            with self.subTest(evidence=evidence):
                clock = FakeClock()
                destination = DestinationContext(
                    self.destination_hwnd, self.destination_pid, None
                )
                adapter = FakeExplorerAdapter(
                    destination, [self.destination_window()]
                )
                session = self.make_session(adapter, clock=clock)
                adapter.windows[20] = self.popup()
                session.observe()
                adapter.windows.pop(20)
                session.observe()

                if evidence == "stream":
                    session.record_stream_open()
                else:
                    session.record_performed_effect(1)
                clock.value = 1.0

                self.assertFalse(session.observe())
                self.assertFalse(session.inferred_cancelled)

    def test_deskflow_cancel_closes_only_the_correlated_popup(self):
        destination = DestinationContext(
            self.destination_hwnd, self.destination_pid, None
        )
        adapter = FakeExplorerAdapter(destination, [self.destination_window()])
        session = self.make_session(adapter)
        adapter.windows[20] = self.popup()
        adapter.windows[30] = WindowSnapshot(30, 300, None, 30, True)
        session.observe()

        self.assertTrue(session.request_cancel())
        self.assertEqual(adapter.closed, [20])

    def test_terminal_disposition_is_first_wins(self):
        destination = DestinationContext(
            self.destination_hwnd, self.destination_pid, None
        )
        adapter = FakeExplorerAdapter(destination, [self.destination_window()])
        session = self.make_session(adapter)

        self.assertTrue(session.record_terminal(TransferPhase.CANCELLED))
        self.assertFalse(session.record_terminal(TransferPhase.COMPLETED))
        self.assertEqual(session.terminal_disposition, TransferPhase.CANCELLED)

    def test_adapter_observation_error_fails_closed_without_cancelling(self):
        destination = DestinationContext(
            self.destination_hwnd, self.destination_pid, None
        )
        adapter = FakeExplorerAdapter(destination, [self.destination_window()])
        session = self.make_session(adapter)

        def fail_observation():
            raise OSError("window enumeration unavailable")

        adapter.list_windows = fail_observation

        with self.assertLogs(
            "app.file_transfer.explorer_session", level="WARNING"
        ):
            observed = session.observe()

        self.assertFalse(observed)
        self.assertIsNone(session.popup_hwnd)
        self.assertFalse(session.inferred_cancelled)

    def test_cleanup_removes_only_proven_new_empty_top_level_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            destination_path = Path(directory)
            existing_empty = destination_path / "existing"
            existing_empty.mkdir()
            destination = DestinationContext(
                self.destination_hwnd, self.destination_pid, destination_path
            )
            adapter = FakeExplorerAdapter(destination, [self.destination_window()])
            session = ExplorerPasteSession.capture(
                self.manifest("new", "existing", "nested/child"), adapter
            )
            new_empty = destination_path / "new"
            new_empty.mkdir()

            results = session.cleanup_cancelled_empty_directories()

            self.assertFalse(new_empty.exists())
            self.assertTrue(existing_empty.is_dir())
            self.assertNotIn("nested/child", results)

    def test_cleanup_preserves_new_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            destination_path = Path(directory)
            destination = DestinationContext(
                self.destination_hwnd, self.destination_pid, destination_path
            )
            adapter = FakeExplorerAdapter(destination, [self.destination_window()])
            session = ExplorerPasteSession.capture(self.manifest("new"), adapter)
            new_directory = destination_path / "new"
            new_directory.mkdir()
            (new_directory / "visible.txt").write_text("keep", encoding="utf-8")

            session.cleanup_cancelled_empty_directories()

            self.assertTrue(new_directory.is_dir())

    def test_shell_location_accepts_file_urls_and_direct_local_paths_only(self):
        self.assertEqual(
            WindowsExplorerAdapter._local_path("file:///C:/Users/Public"),
            Path("C:/Users/Public"),
        )
        self.assertEqual(
            WindowsExplorerAdapter._local_path("C:/Users/Public"),
            Path("C:/Users/Public"),
        )
        self.assertIsNone(
            WindowsExplorerAdapter._local_path("file://server/share/folder")
        )
        self.assertIsNone(WindowsExplorerAdapter._local_path("shell:Downloads"))


if __name__ == "__main__":
    unittest.main()
