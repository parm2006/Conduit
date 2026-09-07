"""Server-primary viewer and map lifecycle, owned by the Tk GUI thread."""
import logging
import math
import threading
import tkinter as tk
from PIL import ImageTk

from app.input_router import RemoteClient, Transitioning
from app.remote_video import ServerVideoReceiver
from app.remote_map import map_bounds, cursor_near_map, render_map
from app.topology_editor import SERVER_COLOR, CLIENT_COLORS
from app.windows_map_overlay import WindowsMapOverlay

logger = logging.getLogger(__name__)


class RemoteView:
    def __init__(self, gui, server, primary):
        self.gui, self.server = gui, server
        self._timer = None
        self._map_key = None
        self._shown_selection = None
        self._cursor = None
        self._returning = False
        self._photo = None
        self.map_window = WindowsMapOverlay()
        overlay = gui.overlay
        overlay.attributes('-alpha', 1.0)
        self.canvas = tk.Canvas(overlay, bg='black', highlightthickness=0, borderwidth=0, cursor='none')
        self.canvas.pack(fill='both', expand=True)
        # Default Tk bindtags propagate Canvas input to the existing Toplevel
        # handlers. Binding twice would duplicate clicks and wheel events.
        overlay.update_idletasks()
        self.set_primary(primary)
        self.receiver = ServerVideoReceiver(
            server, self.viewport,
            viewport_hwnd=self.canvas.winfo_id(),
        )
        self._timer = gui.after(25, self._tick)

    def set_primary(self, primary):
        self.primary = primary
        self.viewport = (primary.right - primary.left, primary.bottom - primary.top)
        self.bounds = map_bounds((primary.left, primary.top, primary.right, primary.bottom))
        self._map_key = None
        self._shown_selection = None
        self._cursor = None
        self.canvas.delete('all')
        receiver = getattr(self, 'receiver', None)
        if receiver is not None:
            receiver.viewport = self.viewport
            receiver.resize(*self.viewport)
        import win32con
        import win32gui
        hwnd = win32gui.GetAncestor(self.gui.overlay.winfo_id(), win32con.GA_ROOT)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, primary.left, primary.top,
                             *self.viewport, win32con.SWP_NOACTIVATE)
        self.gui.overlay_center_x, self.gui.overlay_center_y = self.viewport[0] // 2, self.viewport[1] // 2

    def close(self):
        if self._timer is not None:
            self.gui.after_cancel(self._timer)
            self._timer = None
        self.receiver.stop()
        self.map_window.close()
        self.canvas.destroy()
        self.gui.overlay.attributes('-alpha', .01)

    def _tick(self):
        self._timer = None
        if self.gui.server is not self.server:
            return
        try:
            self._update()
        except Exception as error:
            logger.warning('Remote viewer failed (%s)', type(error).__name__)
            self._restore_local()
        self._timer = self.gui.after(25, self._tick)

    def _restore_local(self):
        if self._returning:
            return
        self._returning = True
        self.canvas.delete('all')
        self.gui.hide_overlay()
        self.gui._set_status('Status: Remote video unavailable. Returned to Server display.', 'orange')
        threading.Thread(target=self.server._return_cursor_to_server,
                         name='remote-video-return', daemon=True).start()

    def _update(self):
        import win32gui
        topology = getattr(self.server, 'active_topology', None)
        router = getattr(self.server, 'input_router', None)
        state = None if router is None else router.state
        remote = state if isinstance(state, RemoteClient) else None
        if remote is not None:
            if self.receiver.stalled():
                self._restore_local()
                return
            if self._returning:
                return
            if remote != self._shown_selection:
                self._shown_selection = remote
                self.canvas.delete('all')
                self._photo = None
                self._cursor = None
            if not self.gui.overlay_active:
                self.gui.show_overlay()
            if not self.receiver.is_native_active():
                frame = self.receiver.take_frame()
                if frame is not None and frame[0] == remote:
                    _, image, (x, y), cursor = frame
                    self._photo = ImageTk.PhotoImage(image, master=self.canvas)
                    self.canvas.delete('video')
                    self.canvas.create_image(x, y, image=self._photo, anchor='nw', tags='video')
                    if (isinstance(cursor, (list, tuple)) and len(cursor) == 2
                            and all(type(v) in (int, float) and math.isfinite(v) for v in cursor)):
                        self._cursor = (self.primary.left + x + cursor[0] * image.width,
                                        self.primary.top + y + cursor[1] * image.height)
            active = (remote.machine_id, remote.display_id)
            cursor = self._cursor or win32gui.GetCursorPos()
        elif isinstance(state, Transitioning):
            return  # Keep the preceding image until ownership is acknowledged.
        else:
            self._returning = False
            self._shown_selection = None
            if self.gui.overlay_active:
                self.gui.hide_overlay()
            cursor = win32gui.GetCursorPos()
            active = None
            if topology is not None:
                for placed in topology.machines:
                    if placed.group.machine_id == topology.server_id:
                        for display in placed.group.displays:
                            r = display.rect
                            if r.left <= cursor[0] < r.right and r.top <= cursor[1] < r.bottom:
                                active = (topology.server_id, display.display_id)
        if topology is None:
            self.map_window.hide()
            return
        # Use only present machines; saved positions for disconnected peers stay in preferences.
        sessions = {s.peer_identity: s for s in self.server.session_registry.ready_sessions()}
        cells = []
        for placed in topology.machines:
            machine_id = placed.group.machine_id
            if machine_id == topology.server_id:
                color = SERVER_COLOR
            elif machine_id in sessions:
                color = sessions[machine_id].color or CLIENT_COLORS[0]
            else:
                continue
            for cell in placed.group.cells:
                cells.append((machine_id, cell.display_id, placed.x + cell.x, placed.y + cell.y, color))
        key = (tuple(cells), active)
        if key != self._map_key:
            image = render_map(cells, active, self.bounds[2])
            self.map_window.update(image, self.bounds[:2])
            self._map_key = key
        if cursor is not None and cursor_near_map(cursor, self.bounds):
            self.map_window.hide()
        else:
            self.map_window.show()
