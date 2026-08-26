from dataclasses import dataclass
import logging

import customtkinter as ctk

from app import input_geometry
from app.file_transfer.toast import TOAST_HEIGHT, TOAST_WIDTH
from app.safe_errors import error_name


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopologyToastView:
    title: str
    details: str
    color: str
    hide_after_ms: int | None = None


def topology_toast_view(group, color, connection_state):
    enabled = tuple(display for display in group.displays if display.enabled)
    resolutions = " + ".join(
        f"{display.rect.right - display.rect.left}×{display.rect.bottom - display.rect.top}"
        for display in enabled
    )
    noun = "display" if len(enabled) == 1 else "displays"
    return TopologyToastView(
        title=group.windows_name,
        details=f"{len(enabled)} {noun} · {resolutions} · {connection_state}",
        color=color,
    )


def topology_toast_rect(group, window_size):
    primary = next(
        display
        for display in group.displays
        if display.enabled and display.primary
    )
    work_rect = primary.work_rect or primary.rect
    dpi = round(96 * primary.dpi_percent / 100)
    return input_geometry.toast_rect_in_work_area(
        (work_rect.left, work_rect.top, work_rect.right, work_rect.bottom),
        window_size,
        dpi,
    )


def display_change_warning_view(group):
    enabled = tuple(display for display in group.displays if display.enabled)
    noun = "display" if len(enabled) == 1 else "displays"
    return TopologyToastView(
        title=f"{group.windows_name} displays changed",
        details=(
            f"{len(enabled)} {noun} detected · Apply to rebuild mouse routing"
        ),
        color="#D97706",
        hide_after_ms=5000,
    )


def connection_lost_warning_view(windows_name):
    return TopologyToastView(
        title=f"{windows_name} disconnected",
        details="Removed from the draft · Active routing stays unchanged",
        color="#D97706",
        hide_after_ms=5000,
    )


class TopologyIdentificationToast:
    DETAILS_WRAP_LENGTH = TOAST_WIDTH - 32

    def __init__(self, root, on_disconnect=None):
        self.root = root
        self.on_disconnect = on_disconnect
        self.group = None
        self.window = ctk.CTkToplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.geometry(f"{TOAST_WIDTH}x{TOAST_HEIGHT}")
        self.body = ctk.CTkFrame(self.window, corner_radius=12)
        self.body.pack(fill="both", expand=True)
        self.title = ctk.CTkLabel(
            self.body,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.title.pack(fill="x", padx=16, pady=(12, 0))
        self.details = ctk.CTkLabel(
            self.body,
            text="",
            anchor="w",
            justify="left",
            wraplength=self.DETAILS_WRAP_LENGTH,
        )
        self.details.pack(fill="x", padx=16, pady=(2, 12))
        if on_disconnect is not None:
            self.disconnect = ctk.CTkButton(
                self.body,
                text="Disconnect",
                width=82,
                height=24,
                command=on_disconnect,
            )
            self.disconnect.place(relx=1.0, x=-12, y=12, anchor="ne")

    def show(self, group, color, connection_state="connected"):
        self.group = group
        view = topology_toast_view(group, color, connection_state)
        self.body.configure(fg_color=view.color)
        self.title.configure(text=view.title, fg_color=view.color)
        self.details.configure(text=view.details, fg_color=view.color)
        left, top, _right, _bottom = topology_toast_rect(
            group,
            (TOAST_WIDTH, TOAST_HEIGHT),
        )
        self.window.geometry(
            f"{TOAST_WIDTH}x{TOAST_HEIGHT}{left:+d}{top:+d}"
        )
        self.window.deiconify()
        self.window.update_idletasks()
        try:
            input_geometry.place_windows_window_in_work_area(
                self.window.winfo_id()
            )
        except OSError as error:
            logger.error(
                "Could not position topology toast in its monitor work area (%s)",
                error_name(error),
            )
        self.window.lift()

    def hide(self):
        self.group = None
        self.window.withdraw()


class DisplayChangeWarningToast:
    WINDOW_SIZE = (TOAST_WIDTH, TOAST_HEIGHT)
    DETAILS_WRAP_LENGTH = TopologyIdentificationToast.DETAILS_WRAP_LENGTH

    def __init__(self, root):
        self.root = root
        self._hide_after = None
        self.window = ctk.CTkToplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.geometry(f"{TOAST_WIDTH}x{TOAST_HEIGHT}")
        self.body = ctk.CTkFrame(self.window, corner_radius=12)
        self.body.pack(fill="both", expand=True)
        self.title = ctk.CTkLabel(
            self.body,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.title.pack(fill="x", padx=16, pady=(12, 0))
        self.details = ctk.CTkLabel(
            self.body,
            text="",
            anchor="w",
            justify="left",
            wraplength=self.DETAILS_WRAP_LENGTH,
        )
        self.details.pack(fill="x", padx=16, pady=(2, 12))

    def show(self, group):
        self._show_view(display_change_warning_view(group))

    def show_connection_lost(self, windows_name):
        self._show_view(connection_lost_warning_view(windows_name))

    def _show_view(self, view):
        if self._hide_after is not None:
            self.root.after_cancel(self._hide_after)
            self._hide_after = None
        self.body.configure(fg_color=view.color)
        self.title.configure(text=view.title, fg_color=view.color)
        self.details.configure(text=view.details, fg_color=view.color)
        self.window.geometry(f"{TOAST_WIDTH}x{TOAST_HEIGHT}")
        self.window.deiconify()
        self.window.update_idletasks()
        try:
            input_geometry.place_windows_window_in_work_area(
                self.window.winfo_id()
            )
        except OSError as error:
            logger.error(
                "Could not position display warning (%s)",
                error_name(error),
            )
        self.window.lift()
        self._hide_after = self.root.after(view.hide_after_ms, self.hide)

    def hide(self):
        self._hide_after = None
        self.window.withdraw()
