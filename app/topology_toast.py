from dataclasses import dataclass

import customtkinter as ctk

from app.input_geometry import toast_rect_in_work_area


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
    return toast_rect_in_work_area(
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
        self.window.geometry("360x104")
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
        self.window.update_idletasks()
        width = max(1, self.window.winfo_width())
        height = max(1, self.window.winfo_height())
        left, top, _right, _bottom = topology_toast_rect(
            group,
            (width, height),
        )
        self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
        self.window.deiconify()
        self.window.lift()

    def hide(self):
        self.group = None
        self.window.withdraw()
