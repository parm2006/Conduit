from dataclasses import dataclass
from enum import Enum, auto
import ctypes
import os
import threading
import tkinter as tk

import customtkinter as ctk

from app.crypto import pairing_code_from_fingerprint
from app.network import PairingTimeout


PAIRING_CODE_COLOR = "white"


def enable_dark_title_bar(window, dwmapi=None, user32=None):
    if os.name != "nt" and (dwmapi is None or user32 is None):
        return False
    try:
        dwmapi = dwmapi or ctypes.windll.dwmapi
        user32 = user32 or ctypes.windll.user32
        handle = user32.GetParent(window.winfo_id()) or window.winfo_id()
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):
            if dwmapi.DwmSetWindowAttribute(
                handle,
                attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            ) == 0:
                return True
    except (AttributeError, OSError, RuntimeError):
        pass
    return False


class PairingOutcome(Enum):
    APPROVED = auto()
    DECLINED = auto()
    TIMED_OUT = auto()
    CLOSED = auto()


class PairingDecision:
    def __init__(self):
        self._lock = threading.Lock()
        self._completed = threading.Event()
        self._outcome = None

    @property
    def outcome(self):
        with self._lock:
            return self._outcome

    def complete(self, outcome):
        if not isinstance(outcome, PairingOutcome):
            raise TypeError("outcome must be a PairingOutcome")
        with self._lock:
            if self._outcome is not None:
                return False
            self._outcome = outcome
            self._completed.set()
            return True

    def wait(self, timeout):
        if not self._completed.wait(timeout):
            self.complete(PairingOutcome.TIMED_OUT)
        return self.outcome


@dataclass(frozen=True)
class PairingPrompt:
    code: str
    server: str
    fingerprint: str
    instruction: str = (
        "Approve only if this code is identical to the code shown on the server."
    )
    approve_label: str = "Codes match"
    decline_label: str = "Decline"

    @classmethod
    def from_peer(cls, fingerprint, peer):
        return cls(
            code=pairing_code_from_fingerprint(fingerprint),
            server=peer.canonical,
            fingerprint=fingerprint,
        )


class PairingDialog:
    def __init__(self, root, prompt, decision):
        self.root = root
        self.prompt = prompt
        self.decision = decision
        self.window = ctk.CTkToplevel(root)
        self.window.title("Conduit security check")
        self.window.geometry("520x360")
        self.window.minsize(360, 320)
        self.window.transient(root)
        self.window.protocol("WM_DELETE_WINDOW", self._decline)
        self.window.bind("<Escape>", self._decline)
        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_rowconfigure(3, weight=1)

        self.title = ctk.CTkLabel(
            self.window,
            text="Confirm this server",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.title.grid(row=0, column=0, padx=20, pady=(18, 5), sticky="ew")
        self.instruction = ctk.CTkLabel(
            self.window,
            text=prompt.instruction,
            wraplength=470,
            justify="left",
        )
        self.instruction.grid(row=1, column=0, padx=20, pady=5, sticky="ew")
        self.code = ctk.CTkLabel(
            self.window,
            text=prompt.code,
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=PAIRING_CODE_COLOR,
        )
        self.code.grid(row=2, column=0, padx=20, pady=6, sticky="ew")
        self.details = ctk.CTkTextbox(self.window, wrap="word", height=115)
        self.details.grid(row=3, column=0, padx=20, pady=8, sticky="nsew")
        self.details.insert(
            "1.0",
            f"Server: {prompt.server}\n\nFingerprint:\n{prompt.fingerprint}",
        )
        self.details.configure(state="disabled")

        self.actions = ctk.CTkFrame(self.window, fg_color="transparent")
        self.actions.grid(row=4, column=0, padx=20, pady=(5, 18))
        self.approve = ctk.CTkButton(
            self.actions, text=prompt.approve_label, width=120,
            command=self._approve,
        )
        self.approve.pack(side="left", padx=5)
        self.decline = ctk.CTkButton(
            self.actions, text=prompt.decline_label, width=100,
            fg_color="red", hover_color="darkred", command=self._decline,
        )
        self.decline.pack(side="left", padx=5)

        self.window.bind("<Configure>", self._resize_content, add="+")
        self.window.update_idletasks()
        enable_dark_title_bar(self.window)
        self._center_over_root()
        self.window.after_idle(self._center_over_root_if_open)
        self.window.grab_set()
        self.window.lift()
        self.window.focus_force()

    def _resize_content(self, event):
        if event.widget is self.window:
            scaling = self.window._get_window_scaling()
            logical_width = event.width / scaling
            self.instruction.configure(
                wraplength=max(260, round(logical_width - 40))
            )

    def _center_over_root(self):
        width = max(self.window.winfo_width(), self.window.winfo_reqwidth())
        height = max(self.window.winfo_height(), self.window.winfo_reqheight())
        x = self.root.winfo_x() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - height) // 2
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = max(0, min(x, screen_width - width))
        y = max(0, min(y, screen_height - height))
        # CustomTkinter has already applied DPI scaling to the mapped window's
        # dimensions. Reusing those dimensions in geometry() would scale them
        # a second time; update only the physical screen position here.
        self.window.geometry(f"+{x}+{y}")

    def _center_over_root_if_open(self):
        try:
            if self.window.winfo_exists():
                self._center_over_root()
        except (RuntimeError, tk.TclError):
            pass

    def _approve(self):
        self._finish(PairingOutcome.APPROVED)

    def _decline(self, event=None):
        self._finish(PairingOutcome.DECLINED)

    def _finish(self, outcome):
        if self.decision.complete(outcome):
            self.close()

    def close(self):
        try:
            if self.window.grab_current() is self.window:
                self.window.grab_release()
        except (RuntimeError, tk.TclError):
            pass
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except (RuntimeError, tk.TclError):
            pass


class PairingApprovalController:
    def __init__(
        self, root, dialog_factory=PairingDialog, timeout=60.0,
        on_status=None,
    ):
        self.root = root
        self.dialog_factory = dialog_factory
        self.timeout = float(timeout)
        self.on_status = on_status or (lambda message: None)
        self._lock = threading.Lock()
        self._active_decision = None
        self._active_dialog = None

    def request(self, fingerprint, peer):
        prompt = PairingPrompt.from_peer(fingerprint, peer)
        decision = PairingDecision()
        with self._lock:
            if self._active_decision is not None:
                raise RuntimeError("pairing approval is already active")
            self._active_decision = decision
        if not self._schedule(lambda: self._show(prompt, decision)):
            with self._lock:
                if self._active_decision is decision:
                    self._active_decision = None
            decision.complete(PairingOutcome.CLOSED)
            raise PairingTimeout(
                "pairing approval cancelled because Conduit closed"
            )

        outcome = decision.wait(self.timeout)
        self._schedule(lambda: self._dismiss(decision))
        if outcome is PairingOutcome.APPROVED:
            self._schedule(
                lambda: self.on_status(
                    "Status: Pairing approved; authenticating…"
                ),
            )
            return True
        if outcome is PairingOutcome.DECLINED:
            return False
        if outcome is PairingOutcome.TIMED_OUT:
            raise PairingTimeout(
                "Pairing approval timed out. Connect again and compare the codes."
            )
        raise PairingTimeout("pairing approval cancelled because Conduit closed")

    def _schedule(self, callback):
        try:
            self.root.after(0, callback)
            return True
        except (RuntimeError, tk.TclError):
            return False

    def _show(self, prompt, decision):
        if decision.outcome is not None:
            self._dismiss(decision)
            return
        self.on_status(
            "Status: Waiting for pairing approval — compare the server code."
        )
        dialog = self.dialog_factory(self.root, prompt, decision)
        with self._lock:
            if self._active_decision is not decision or decision.outcome is not None:
                dialog.close()
                return
            self._active_dialog = dialog

    def _dismiss(self, decision):
        dialog = None
        with self._lock:
            if self._active_decision is not decision:
                return
            dialog = self._active_dialog
            self._active_dialog = None
            self._active_decision = None
        if dialog is not None:
            dialog.close()

    def shutdown(self):
        with self._lock:
            decision = self._active_decision
            dialog = self._active_dialog
            self._active_decision = None
            self._active_dialog = None
        if decision is not None:
            decision.complete(PairingOutcome.CLOSED)
        if dialog is not None:
            dialog.close()
