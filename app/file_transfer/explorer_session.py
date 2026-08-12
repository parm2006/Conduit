"""Fail-closed correlation for one Windows Explorer virtual-file paste."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from .status import TransferPhase


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowSnapshot:
    hwnd: int
    process_id: int
    owner_hwnd: int | None
    root_owner_hwnd: int | None
    visible: bool


@dataclass(frozen=True)
class DestinationContext:
    hwnd: int
    process_id: int
    folder: Path | None


@dataclass(frozen=True)
class CleanupCandidate:
    relative_path: str
    path: Path
    existed_before: bool


class ExplorerPasteSession:
    def __init__(
        self,
        manifest,
        adapter,
        destination,
        windows_before,
        cleanup_candidates,
        clock,
        resolution_grace,
    ):
        self.job_id = manifest["job_id"]
        self.manifest = manifest
        self.adapter = adapter
        self.destination = destination
        self.windows_before = frozenset(windows_before)
        self.cleanup_candidates = tuple(cleanup_candidates)
        self.clock = clock
        self.resolution_grace = float(resolution_grace)
        self.popup_hwnd = None
        self._popup_identity = None
        self._popup_visible = False
        self._popup_closed_at = None
        self._correlation_failed = False
        self._positive_evidence = False
        self._inferred_cancelled = False
        self._deskflow_cancel_requested = False
        self._terminal_disposition = None

    @classmethod
    def capture(
        cls,
        manifest,
        adapter=None,
        *,
        clock=time.monotonic,
        resolution_grace=0.25,
    ):
        adapter = adapter or WindowsExplorerAdapter()
        try:
            destination = adapter.capture_destination()
            windows = tuple(adapter.list_windows())
        except Exception as error:
            logger.warning(
                "Explorer paste context capture failed (%s)",
                type(error).__name__,
            )
            destination = None
            windows = ()
        candidates = cls._capture_cleanup_candidates(manifest, destination)
        return cls(
            manifest,
            adapter,
            destination,
            (window.hwnd for window in windows),
            candidates,
            clock,
            resolution_grace,
        )

    @staticmethod
    def _capture_cleanup_candidates(manifest, destination):
        if destination is None or destination.folder is None:
            return ()
        root = Path(destination.folder)
        candidates = []
        for item in manifest.get("items", ()):
            if item.get("item_type") != "directory":
                continue
            relative_path = item.get("relative_path")
            if not isinstance(relative_path, str):
                continue
            normalized = relative_path.replace("\\", "/")
            pure_path = PurePosixPath(normalized)
            if (
                len(pure_path.parts) != 1
                or pure_path.parts[0] in {"", ".", ".."}
                or pure_path.is_absolute()
            ):
                continue
            candidate = root / pure_path.parts[0]
            try:
                existed_before = candidate.exists()
            except OSError:
                continue
            candidates.append(
                CleanupCandidate(relative_path, candidate, existed_before)
            )
        return tuple(candidates)

    @property
    def popup_visible(self):
        return self._popup_visible

    @property
    def decision_pending(self):
        return self.popup_hwnd is not None and self._popup_visible

    @property
    def inferred_cancelled(self):
        return self._inferred_cancelled

    @property
    def terminal_disposition(self):
        return self._terminal_disposition

    def observe(self):
        if self.destination is None:
            return False
        try:
            if self.popup_hwnd is None and not self._correlation_failed:
                self._discover_popup()
            if self.popup_hwnd is not None:
                current = self.adapter.window_snapshot(self.popup_hwnd)
                if current is not None and self._matches_popup(current) and current.visible:
                    self._popup_visible = True
                    self._popup_closed_at = None
                else:
                    self._popup_visible = False
                    if self._popup_closed_at is None:
                        self._popup_closed_at = self.clock()
                        logger.info(
                            "Explorer paste popup closed (job=%s, correlated=true)",
                            self.job_id,
                        )
        except Exception as error:
            self._correlation_failed = True
            self._popup_visible = False
            self._popup_closed_at = None
            logger.warning(
                "Explorer paste popup observation disabled (job=%s, error=%s)",
                self.job_id,
                type(error).__name__,
            )
            return False
        if (
            self._popup_closed_at is not None
            and not self._positive_evidence
            and self.clock() - self._popup_closed_at >= self.resolution_grace
        ):
            self._inferred_cancelled = True
        return self._inferred_cancelled

    def _discover_popup(self):
        candidates = []
        for window in self.adapter.list_windows():
            if window.hwnd in self.windows_before or not window.visible:
                continue
            if self._is_destination_owned(window):
                candidates.append(window)
        if len(candidates) == 1:
            popup = candidates[0]
            self.popup_hwnd = popup.hwnd
            self._popup_identity = popup
            self._popup_visible = True
            logger.info(
                "Explorer paste popup correlated (job=%s, correlated=true)",
                self.job_id,
            )
        elif len(candidates) > 1:
            self._correlation_failed = True
            logger.info(
                "Explorer paste popup ambiguous (job=%s, correlated=false)",
                self.job_id,
            )

    def _is_destination_owned(self, window):
        return (
            window.process_id == self.destination.process_id
            and window.hwnd != self.destination.hwnd
            and (
                window.owner_hwnd == self.destination.hwnd
                or window.root_owner_hwnd == self.destination.hwnd
            )
        )

    def _matches_popup(self, window):
        return (
            self._popup_identity is not None
            and window.hwnd == self._popup_identity.hwnd
            and window.process_id == self._popup_identity.process_id
            and self._is_destination_owned(window)
        )

    def record_stream_open(self):
        self._positive_evidence = True
        return True

    def record_performed_effect(self, effect):
        if isinstance(effect, bool) or effect not in {0, 1}:
            return False
        if effect == 1:
            self._positive_evidence = True
        else:
            self._inferred_cancelled = True
        return True

    def request_cancel(self):
        self._deskflow_cancel_requested = True
        if self.popup_hwnd is None:
            return False
        current = self.adapter.window_snapshot(self.popup_hwnd)
        if current is None or not current.visible or not self._matches_popup(current):
            return False
        try:
            closed = self.adapter.close_window(self.popup_hwnd) is not False
        except Exception as error:
            logger.warning(
                "Explorer paste popup dismissal failed (job=%s, error=%s)",
                self.job_id,
                type(error).__name__,
            )
            return False
        logger.info(
            "Explorer paste popup dismissal (job=%s, correlated=true, closed=%s)",
            self.job_id,
            str(closed).lower(),
        )
        return closed

    def record_terminal(self, phase):
        if phase not in {
            TransferPhase.COMPLETED,
            TransferPhase.FAILED,
            TransferPhase.CANCELLED,
        }:
            return False
        if self._terminal_disposition is not None:
            return False
        self._terminal_disposition = phase
        return True

    def cleanup_cancelled_empty_directories(self):
        results = {}
        if self.destination is None or self.destination.folder is None:
            return results
        try:
            root = Path(self.destination.folder).resolve(strict=True)
        except OSError:
            return results
        for candidate in self.cleanup_candidates:
            result = self._cleanup_candidate(root, candidate)
            results[candidate.relative_path] = result
            logger.info(
                "Explorer paste folder cleanup (job=%s, result=%s)",
                self.job_id,
                result,
            )
        return results

    @staticmethod
    def _cleanup_candidate(root, candidate):
        if candidate.existed_before:
            return "preserved_existing"
        path = candidate.path
        try:
            if not path.exists():
                return "missing"
            if path.is_symlink():
                return "preserved_link"
            is_junction = getattr(path, "is_junction", None)
            if is_junction is not None and is_junction():
                return "preserved_link"
            resolved = path.resolve(strict=True)
            if resolved.parent != root or not resolved.is_dir():
                return "preserved_untrusted"
            if next(resolved.iterdir(), None) is not None:
                return "preserved_nonempty"
            resolved.rmdir()
            return "removed_empty"
        except OSError as error:
            return f"preserved_error_{type(error).__name__}"


class WindowsExplorerAdapter:
    """Small pywin32 boundary; session policy stays in ExplorerPasteSession."""

    def capture_destination(self):
        import win32con
        import win32gui

        foreground = win32gui.GetForegroundWindow()
        if not foreground:
            return None
        hwnd = win32gui.GetAncestor(foreground, win32con.GA_ROOT) or foreground
        folder = self._shell_folder(hwnd)
        if folder is None:
            return None
        window = self.window_snapshot(hwnd)
        if window is None:
            return None
        return DestinationContext(hwnd, window.process_id, folder)

    def list_windows(self):
        import win32gui

        handles = []
        win32gui.EnumWindows(lambda hwnd, output: output.append(hwnd), handles)
        windows = []
        for hwnd in handles:
            window = self.window_snapshot(hwnd)
            if window is not None:
                windows.append(window)
        return tuple(windows)

    def window_snapshot(self, hwnd):
        import win32con
        import win32gui
        import win32process

        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER) or None
        root_owner = win32gui.GetAncestor(hwnd, win32con.GA_ROOTOWNER) or None
        return WindowSnapshot(
            int(hwnd),
            int(process_id),
            int(owner) if owner else None,
            int(root_owner) if root_owner else None,
            bool(win32gui.IsWindowVisible(hwnd)),
        )

    def close_window(self, hwnd):
        import win32con
        import win32gui

        if not win32gui.IsWindow(hwnd):
            return False
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return True

    @staticmethod
    def _shell_folder(hwnd):
        import win32com.client

        try:
            windows = win32com.client.Dispatch("Shell.Application").Windows()
            for window in windows:
                try:
                    if int(window.HWND) != int(hwnd):
                        continue
                    return WindowsExplorerAdapter._local_path(window.LocationURL)
                except (AttributeError, OSError, TypeError, ValueError):
                    continue
        except Exception as error:
            logger.warning(
                "Explorer Shell window lookup failed (%s)",
                type(error).__name__,
            )
        return None

    @staticmethod
    def _local_path(location):
        if not isinstance(location, str) or not location:
            return None
        direct_path = Path(location)
        if (
            direct_path.is_absolute()
            and direct_path.drive
            and not direct_path.drive.startswith("\\\\")
        ):
            return direct_path
        parsed = urlparse(location)
        if parsed.scheme.lower() == "file":
            if parsed.netloc not in {"", "localhost"}:
                return None
            value = url2pathname(unquote(parsed.path))
        elif not parsed.scheme:
            value = location
        else:
            return None
        path = Path(value)
        if not path.is_absolute() or not path.drive:
            return None
        return path
