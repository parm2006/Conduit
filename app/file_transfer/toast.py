from dataclasses import dataclass
import logging

import customtkinter as ctk

from app.input_geometry import place_windows_window_in_work_area
from app.safe_errors import error_name
from .status import TransferPhase


TOAST_WIDTH = 360
TOAST_HEIGHT = 104
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToastView:
    title: str
    details: str
    hide_after_ms: int | None


def toast_view(status):
    titles = {
        TransferPhase.PREPARING: "Preparing files",
        TransferPhase.COMPRESSING: "Compressing files",
        TransferPhase.TRANSFERRING: "Network transfer",
        TransferPhase.VERIFYING: "Verifying transfer",
        TransferPhase.COMPLETED: "Copy complete",
        TransferPhase.FAILED: "Transfer failed",
        TransferPhase.CANCELLED: "Transfer cancelled",
        TransferPhase.WAITING_FOR_EXPLORER: "Waiting for Windows Explorer",
        TransferPhase.PASTING: "Copying in Windows Explorer",
        TransferPhase.VERIFYING_RESULT: "Confirming Windows copy",
        TransferPhase.CANCELLING: "Cancelling transfer",
    }
    hide_delays = {
        TransferPhase.COMPLETED: 3000,
        TransferPhase.CANCELLED: 0,
        TransferPhase.FAILED: 3000,
    }
    if status.phase is TransferPhase.WAITING_FOR_EXPLORER:
        details = "Choose any Windows file prompt to continue"
    elif status.phase is TransferPhase.FAILED:
        if status.error_code == "ExplorerStartTimeout":
            message = "Windows Explorer did not accept the paste."
        else:
            message = "Conduit could not finish the network transfer."
        details = f"{_safe_file_label(status.label)} · {message}"
    elif status.phase is TransferPhase.COMPLETED:
        details = f"{_size(status.bytes_done)} / {_size(status.bytes_total)} · Windows finished reading files"
    else:
        details = _progress_details(status)
    return ToastView(titles[status.phase], details[:80], hide_delays.get(status.phase))


class TransferToast:
    def __init__(self, root, on_cancel):
        self.root = root
        self.on_cancel = on_cancel
        self.job_id = None
        self._dismissed_job_id = None
        self._hide_after = None
        self.window = ctk.CTkToplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(fg_color=("#f1f1f1", "#242424"))
        self.window.geometry(f"{TOAST_WIDTH}x{TOAST_HEIGHT}")
        self.window.grid_columnconfigure(0, weight=1)
        self.title = ctk.CTkLabel(
            self.window, text="", font=ctk.CTkFont(size=14, weight="bold"), anchor="w", height=20,
        )
        self.title.grid(row=0, column=0, sticky="ew", padx=(14, 6), pady=(9, 1))
        self.cancel = ctk.CTkButton(
            self.window, text="Cancel", width=68, height=24, command=self._cancel,
        )
        self.cancel.grid(row=0, column=1, padx=(4, 12), pady=(8, 1))
        self.progress = ctk.CTkProgressBar(self.window, height=8)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=5)
        self.details = ctk.CTkLabel(self.window, text="", anchor="w", height=18, font=ctk.CTkFont(size=12))
        self.details.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(1, 8))

    def show(self, status):
        if status.job_id == self._dismissed_job_id:
            if status.is_terminal:
                self._dismissed_job_id = None
            return
        if self._hide_after is not None:
            self.root.after_cancel(self._hide_after)
            self._hide_after = None
        self.job_id = status.job_id
        view = toast_view(status)
        self.title.configure(text=view.title)
        percent = status.percent
        self.progress.configure(mode="indeterminate" if percent is None else "determinate")
        if percent is None:
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.set(percent / 100.0)
        self.details.configure(text=view.details)
        self.cancel.configure(state="disabled" if status.is_terminal else "normal")
        self.window.geometry(f"{TOAST_WIDTH}x{TOAST_HEIGHT}")
        self.window.deiconify()
        self.window.update_idletasks()
        self._schedule_hide(view.hide_after_ms)
        try:
            target = place_windows_window_in_work_area(self.window.winfo_id())
            logger.debug("Transfer toast positioned in physical work-area rectangle %s", target)
        except OSError as error:
            logger.error(
                "Could not position transfer toast in its monitor work area (%s)",
                error_name(error),
            )
        self.window.lift()

    def _schedule_hide(self, delay_ms):
        if delay_ms is not None:
            self._hide_after = self.root.after(delay_ms, self._hide)

    def raise_if_visible(self):
        if self.window.state() != "withdrawn":
            self.window.lift()

    def _hide(self):
        self._hide_after = None
        self.window.withdraw()

    def _cancel(self):
        if self.job_id:
            job_id = self.job_id
            self._dismissed_job_id = job_id
            if self.on_cancel(job_id):
                self._hide()
            else:
                self._dismissed_job_id = None


def _progress_details(status):
    done = _size(status.bytes_done)
    total = _size(status.bytes_total)
    if status.bytes_per_second > 0:
        speed = f"{_size(status.bytes_per_second)}/s"
        eta = "" if status.eta_seconds is None else f" · about {max(1, round(status.eta_seconds))}s left"
        return f"{done} / {total} · {speed}{eta}"
    return f"{done} / {total}"


def _size(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def _safe_file_label(value):
    if not isinstance(value, str):
        return "File"
    filename = value.replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(character for character in filename if character.isprintable()).strip()
    return filename or "File"
