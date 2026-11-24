"""Persistent Updates page and a shared, keyboard-accessible status shortcut."""
import tkinter

import customtkinter as ctk

from app.update_panel import UpdatePanel


class UpdateNavigation(ctk.CTkFrame):
    def __init__(self, parent, roles, *, on_close, on_available=None):
        super().__init__(roles, fg_color='transparent')
        self.roles = roles
        self.page = ctk.CTkFrame(parent)
        self.page.grid(row=0, column=0, padx=20, pady=20, sticky='nsew')
        self.page.grid_remove()
        self.back = tkinter.Button(
            self.page, text='Back to Server / Client', command=self.hide,
            takefocus=True, relief='flat', bg='#2b2b2b', fg='white',
            activebackground='#404040', activeforeground='white', padx=8, pady=5,
        )
        self.back.bind('<Return>', lambda event: self.back.invoke())
        self.back.pack(anchor='w', padx=12, pady=12)
        # A native button supplies Tab focus, a visible focus ring, Space activation,
        # and a text label exposed to Windows accessibility tools.
        self.button = tkinter.Button(
            self, command=self.show, takefocus=True, relief='flat',
            bg='#2b2b2b', fg='#d0d0d0', activebackground='#404040',
            activeforeground='white', padx=8, pady=3,
        )
        self.description = tkinter.StringVar(master=self)
        self.tooltip = None
        self.button.pack(side='right')
        self.button.bind('<Enter>', self._show_tooltip)
        self.button.bind('<FocusIn>', self._show_tooltip)
        self.button.bind('<Leave>', self._hide_tooltip)
        self.button.bind('<FocusOut>', self._hide_tooltip)
        self.button.bind('<Return>', lambda event: self.button.invoke())
        self.place(relx=1, rely=1, anchor='se', x=-12, y=-12)
        self.panel = UpdatePanel(
            self.page, on_close=on_close, on_available=on_available,
            on_status=self._status_changed,
        )
        self.panel.pack(fill='both', expand=True)

    def _status_changed(self, state, available):
        labels = {
            'unknown': '? Updates: not checked',
            'checking': '… Checking updates',
            'current': '✓ Updates',
            'available': '! Update available',
            'downloading': '! Downloading update',
            'downloaded': '! Update verified',
            'error': '! Update failed' if available else '? Update check failed',
        }
        self.description.set(labels[state])
        self.button.configure(
            text=labels[state].split(' ', 1)[0] + ' Updates', fg='#f4cc58' if available else '#d0d0d0',
        )

    def _show_tooltip(self, event=None):
        if self.tooltip is None:
            self.tooltip = tkinter.Toplevel(self)
            self.tooltip.overrideredirect(True)
            tkinter.Label(
                self.tooltip, textvariable=self.description, bg='#404040',
                fg='white', padx=8, pady=4,
            ).pack()
            self.tooltip.geometry(
                f'+{self.button.winfo_rootx()}+{self.button.winfo_rooty() - 30}'
            )

    def _hide_tooltip(self, event=None):
        if self.tooltip is not None:
            self.tooltip.destroy()
            self.tooltip = None

    def show(self):
        self._hide_tooltip()
        self.roles.grid_remove()
        self.page.grid()
        self.back.focus_set()

    def hide(self):
        self.page.grid_remove()
        self.roles.grid()
        self.button.focus_set()
