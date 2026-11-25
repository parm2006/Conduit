"""Tk update controls; network workers communicate through a queue."""
import logging
import queue
import threading
from tkinter import messagebox
import customtkinter as ctk

from app.keyboard_nav import make_button_keyboard_accessible
from app.updater import check_for_updates, download_update, can_install, launch_installer, verified_installer
from app.version import PRODUCT_VERSION

logger = logging.getLogger(__name__)


class UpdatePanel(ctk.CTkFrame):
    def __init__(self, parent, *, on_close, on_available=None, on_status=None):
        super().__init__(parent, fg_color='transparent')
        self.on_close = on_close
        self.on_available = on_available
        self.on_status = on_status
        self.busy = False
        self.update = None
        self.download = None
        self.results = queue.Queue()
        self.timer = None
        self._notify_status('unknown')
        ctk.CTkLabel(self, text=f'Conduit {PRODUCT_VERSION}').pack(pady=(20, 5))
        channel = 'Preview and stable releases' if '-' in PRODUCT_VERSION else 'Stable releases'
        ctk.CTkLabel(self, text=channel).pack(pady=5)
        self.status = ctk.CTkLabel(self, text='Check for a newer version.', wraplength=290)
        self.status.pack(pady=15)
        self.action = ctk.CTkButton(self, text='Check for updates', command=self.check)
        make_button_keyboard_accessible(self.action)
        self.action.pack(pady=10)
        # Check on the next Tk turn in every build; installation eligibility
        # only gates the existing install controls, never update discovery.
        self.timer = self.after(0, self.check)

    def _notify_status(self, state):
        if self.on_status is not None:
            self.on_status(state, self.update is not None)

    def _start(self, operation, work):
        if self.busy:
            return
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.busy = True
        self._notify_status('checking' if operation == 'check' else 'downloading')
        self.action.configure(state='disabled')
        self.status.configure(text='Checking GitHub…' if operation == 'check' else 'Downloading and verifying…')

        def worker():
            try:
                self.results.put((operation, work(), None))
            except Exception as error:
                logger.warning('Update %s failed (%s)', operation, type(error).__name__)
                self.results.put((operation, None, error))

        threading.Thread(target=worker, name='conduit-update', daemon=True).start()
        self.timer = self.after(100, self._poll)

    def check(self):
        self._start('check', check_for_updates)

    def _download(self):
        update = self.update
        self._start('download', lambda: download_update(update))

    def _poll(self):
        self.timer = None
        try:
            operation, result, error = self.results.get_nowait()
        except queue.Empty:
            self.timer = self.after(100, self._poll)
            return
        self.busy = False
        self.action.configure(state='normal')
        if error:
            self._notify_status('error')
            self.status.configure(text=f'Could not {operation} the update. Check your connection and try again.')
            self.action.configure(text='Check again', command=self.check)
        elif operation == 'check':
            self.update, self.download = result, None
            self._notify_status('current' if result is None else 'available')
            if result is None:
                self.status.configure(text='You have the latest version for this channel.')
                self.action.configure(text='Check again', command=self.check)
            else:
                self.status.configure(text=f'Conduit {result.version} is available. Open Updates to download it.')
                self.action.configure(text='Download update', command=self._download)
                if self.on_available is not None:
                    self.on_available(result.version)
        else:
            self.download = result
            self._notify_status('downloaded')
            if can_install():
                self.status.configure(text='Download verified. Installing will close Conduit and disconnect active sessions.')
                self.action.configure(text='Install update', command=self._install)
            else:
                self.status.configure(text=f'Download verified: {result.path}\nClose Conduit and run Setup to install. In-app installation requires an installed copy.')
                self.action.configure(text='Check again', command=self.check)

    def _install(self):
        if not messagebox.askyesno('Update Conduit', 'Close Conduit and disconnect active sessions to run the update installer?', parent=self):
            return
        try:
            verified_installer(self.download)
            launch_installer(self.download)
        except Exception as error:
            self._notify_status('error')
            logger.warning('Update installation failed (%s)', type(error).__name__)
            self.status.configure(text='Could not start the verified installer. Check for updates and download again.')
            self.action.configure(text='Check again', command=self.check)
            return
        self.on_close()

    def destroy(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        super().destroy()
