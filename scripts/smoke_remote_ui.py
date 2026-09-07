"""Source GUI smoke test, no peer connections, pairing or input hooks."""
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gui import ConduitGUI
from app.input_router import LocalServer, RemoteClient
from app.preferences import UserPreferences


def main():
    with tempfile.TemporaryDirectory() as directory:
        server = SimpleNamespace(
            control_network=Mock(), data_network=Mock(),
            start=lambda: True, stop=Mock(), set_screen_size=Mock(),
            identity=SimpleNamespace(cert_path='', recovered=False),
            session_registry=SimpleNamespace(ready_sessions=lambda: (), active_sessions=lambda: ()),
            control_connected=False,
            on_mouse_move=Mock(), on_mouse_click=Mock(), on_mouse_scroll=Mock(),
        )
        def activate(topology):
            server.active_topology = topology
            display_id, center = topology.server_primary_center()
            server.input_router = SimpleNamespace(state=LocalServer(display_id, center), topology=topology)
        server.activate_client_topology = activate
        with (patch('app.gui.UserPreferences', return_value=UserPreferences(directory)),
              patch('app.gui.GlobalHotkeyMonitor'),
              patch.object(ConduitGUI, '_refresh_firewall_status'),
              patch.object(ConduitGUI, '_start_server_display_monitor'),
              patch('app.gui.ConduitServer', return_value=server),
              patch('app.gui.certificate_fingerprint', return_value='ab' * 32)):
            gui = ConduitGUI()
            try:
                gui.update()
                assert not gui.remote_mode_var.get(), 'Remote mode must default off'
                assert gui.remote_mode_toggle.cget('state') == 'normal'
                gui.remote_mode_var.set(True)
                gui._start_server_after_firewall(28903, 'smoke-only')
                gui.update()
                assert gui.remote_view is not None, 'Remote viewer did not initialize'
                assert gui.remote_mode_toggle.cget('state') == 'disabled'
                # Hidden GUI does not own the map window's visibility.
                gui.withdraw()
                gui.remote_view._update()
                gui.update()
                import win32gui
                assert win32gui.IsWindow(gui.remote_view.map_window.hwnd)
                # Render synthetic Client pixels while the GUI is hidden. No
                # actual desktop capture, mouse warp, grab or input hooks.
                from PIL import Image
                from app.remote_video import fit_rect
                view = gui.remote_view
                view.receiver.stop()
                local = server.input_router.state
                remote = RemoteClient('smoke', 'client', 'display', (3, 40), 'smoke')
                server.input_router.state = remote
                view.receiver.selection = remote
                view.receiver.started_at = time.monotonic()
                x, y, width, height = fit_rect(view.viewport, (600, 1000))
                view.receiver._latest = (remote, Image.new('RGB', (width, height), '#2470b0'), (x, y), (.5, .5))
                def show_without_input():
                    gui.overlay_active = True
                    gui.overlay.deiconify()
                with patch.object(gui, 'show_overlay', side_effect=show_without_input):
                    view._update()
                    gui.update()
                    gui.after(200, gui.quit)
                    gui.mainloop()
                    assert gui.state() == 'withdrawn'
                    assert (view.canvas.winfo_width(), view.canvas.winfo_height()) == view.viewport
                    assert view.canvas.find_withtag('video'), 'Client image did not reach the Canvas'
                    import win32con
                    hwnd = win32gui.GetAncestor(gui.overlay.winfo_id(), win32con.GA_ROOT)
                    assert win32gui.IsWindowVisible(hwnd), 'Video must remain visible with hidden GUI'
                    assert win32gui.GetWindowRect(hwnd) == (view.primary.left, view.primary.top,
                                                           view.primary.right, view.primary.bottom)
                server.input_router.state = local
                view._update()
                gui.stop_server()
                gui.update()
                assert gui.remote_view is None
                assert gui.remote_mode_toggle.cget('state') == 'normal'
                assert gui.remote_mode_var.get(), 'Stop should unlock, not silently change the selection'
                print('GUI smoke passed: off default, start lock, fullscreen letterboxed frame with hidden GUI, map, stop unlock.')
            finally:
                gui.on_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
