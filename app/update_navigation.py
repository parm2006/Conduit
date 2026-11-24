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
        # CTk draws the icon without the native button's dotted focus rectangle.
        self.button = ctk.CTkButton(
            self, command=self.show, text='?', width=42, height=34,
            fg_color='#404040', hover_color='#505050', text_color='#a0a0a0',
            font=('Segoe UI', 18), corner_radius=0,
        )
        self.button._canvas.configure(takefocus=True)
        self.notification = ctk.CTkLabel(
            self, text='•', text_color='#f4cc58', width=8, height=8,
            font=('Segoe UI', 12), fg_color='#404040',
        )
        self.description = tkinter.StringVar(master=self)
        self.button.pack(side='right')
        self.button.bind('<Return>', lambda event: self.button.invoke())
        self.button.bind('<space>', lambda event: self.button.invoke())
        self.place(relx=1, rely=1, anchor='se', x=-12, y=-12)
        self.panel = UpdatePanel(
            self.page, on_close=on_close, on_available=on_available,
            on_status=self._status_changed,
        )
        self.panel.pack(fill='both', expand=True)

    def _status_changed(self, state, available):
        labels = {
            'unknown': '? Updates: not checked',
            'checking': '? Checking updates',
            'current': '✓ Updates',
            'available': '! Update available',
            'downloading': '! Downloading update',
            'downloaded': '! Update verified',
            'error': '! Update failed' if available else '? Update check failed',
        }
        self.description.set(labels[state])
        color = '#228b22' if state == 'current' else '#f4cc58' if available else '#a0a0a0'
        self.button.configure(
            text=labels[state].split(' ', 1)[0],
            text_color=color,
        )

        if available:
            self.notification.place(relx=1, x=-3, y=1, anchor='ne')
        else:
            self.notification.place_forget()

    def show(self):
        self.roles.grid_remove()
        self.page.grid()
        self.back.focus_set()

    def hide(self):
        self.page.grid_remove()
        self.roles.grid()
        self.button.focus_set()
