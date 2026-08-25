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


class TopologyIdentificationToast:
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
        self.details = ctk.CTkLabel(self.body, text="", anchor="w")
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
