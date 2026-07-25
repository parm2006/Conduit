import logging
import os
import threading
from pathlib import Path
from app.network import NetworkServer
from app.crypto import load_identity
from app.session import SessionCoordinator
from app.file_transfer.transport import FileLaneServer
from app.file_transfer.paste_coordinator import PasteCoordinator
from app.file_transfer.hotkey import WindowsPasteHotkeyMonitor
from app.file_transfer.paste_service import FilePasteService
from app.file_transfer.publisher import VirtualPastePublisher
from app.file_transfer.receiver import TransferReceiver
from app.file_transfer.selection import snapshot_selection
from app.file_transfer.sender import TransferSender
from app.file_transfer.controller import TransferController
from app.file_transfer.cancellation import TransferCancellation
from app.input_handler import InputHandler
from app.clipboard_handler import ClipboardHandler
from app.clipboard_formats import encode_clipboard_message
from app.latest_wins_sender import LatestWinsSender
from app.safe_errors import error_name

logger = logging.getLogger(__name__)

class DeskFlowServer:
    def __init__(self, password, port=5000, layout_position='right', on_capture_start=None, on_capture_stop=None, on_transfer_status=None):
        self.layout_position = layout_position
        self.on_capture_start = on_capture_start
        self.on_capture_stop = on_capture_stop
        
        self.identity = load_identity()
        self.session_coordinator = SessionCoordinator(password)
        self.control_network = NetworkServer(
            password, '0.0.0.0', port, role='control',
            coordinator=self.session_coordinator, identity=self.identity,
        )
        self.data_network = NetworkServer(
            password, '0.0.0.0', port + 1, role='data',
            coordinator=self.session_coordinator, identity=self.identity,
        )
        self.file_network = FileLaneServer(
            host='0.0.0.0', port=port + 2, identity=self.identity,
            coordinator=self.session_coordinator,
        )
        self.transfer_controller = TransferController()
        if on_transfer_status:
            self.transfer_controller.subscribe(on_transfer_status)
        self.file_receiver = TransferReceiver(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'DeskFlow' / 'transfers' / 'server', controller=self.transfer_controller)
        self.file_receiver.attach(self.file_network)
        self.transfer_cancellation = TransferCancellation(
            self.file_network, self.transfer_controller, self.file_receiver
        )
        self.file_publisher = VirtualPastePublisher()
        self.input_handler = InputHandler()
        
        self.control_connected = False
        self.data_connected = False
        self._client_ready = False
        self._disconnecting = False
        self._client_state_lock = threading.RLock()
        
        # Setup control network callbacks
        self.control_network.register_callback('connected', lambda d: self._on_socket_connected('control', d))
        self.control_network.register_callback('disconnected', lambda d: self._on_socket_disconnected('control'))
        self.control_network.register_callback('switch_back', self.on_switch_back)
        self.control_network.register_callback('file_clipboard_available', self.on_remote_file_availability)
        self.control_network.register_callback('file_manifest_request', self.on_file_manifest_request)
        self.control_network.register_callback('file_manifest_response', self.on_file_manifest_response)
        self.control_network.register_callback('file_manifest_failed', self.on_file_manifest_failed)
        self.control_network.register_callback('file_manifest_ack', self.on_file_manifest_ack)
        
        # Setup data network callbacks
        self.data_network.register_callback('connected', lambda d: self._on_socket_connected('data', d))
        self.data_network.register_callback('disconnected', lambda d: self._on_socket_disconnected('data'))
        self.data_network.register_callback('clipboard_sync', self.on_remote_copy)
        self.file_network.register_callback(
            'disconnected', lambda metadata, payload: self._on_socket_disconnected('file')
        )
        
        # Setup input callbacks
        self.input_handler.register_callback('edge_hit', self.on_edge_hit)
        self.input_handler.register_callback('mouse_move', self.on_mouse_move)
        self.input_handler.register_callback('mouse_click', self.on_mouse_click)
        self.input_handler.register_callback('mouse_scroll', self.on_mouse_scroll)
        self.input_handler.register_callback('key_press', self.on_key_press)
        self.input_handler.register_callback('key_release', self.on_key_release)

        # Setup clipboard
        self.clipboard = ClipboardHandler(
            on_clipboard_change=self.on_local_copy,
            on_file_availability=self.on_local_file_availability,
        )
        self.paste_coordinator = PasteCoordinator(self._request_remote_file_paste)
        self.hotkey_monitor = WindowsPasteHotkeyMonitor(self.paste_coordinator)
        self.local_files_available = False
        self.remote_files_available = False
        self.file_paste_service = FilePasteService(
            self.control_network, self.file_receiver, self.file_publisher,
            TransferSender(self.file_network, controller=self.transfer_controller),
            lambda: snapshot_selection(self.clipboard.read_file_selection()),
        )
        self.clipboard_sender = LatestWinsSender(self._send_clipboard_snapshot)
        self.switching_to_client = False
        self.pressed_keys = set()
        self.forwarded_keys = {}

    def cancel_transfer(self, job_id):
        return self.transfer_cancellation.request(job_id)

    def set_screen_size(self, w, h):
        self.input_handler.set_screen_size(w, h)

    def start(self):
        c_success = self.control_network.start()
        d_success = self.data_network.start()
        f_success = self.file_network.start()
        if c_success and d_success and f_success:
            return True
        self.stop()
        return False

    def stop(self):
        self.control_network.stop()
        self.data_network.stop()
        self.file_network.stop()
        self.input_handler.stop()
        self.clipboard.stop()
        self.clipboard_sender.stop()
        self.hotkey_monitor.stop()

    def _on_socket_connected(self, sock_type, data=None):
        with self._client_state_lock:
            if sock_type == 'control':
                self.control_connected = True
            elif sock_type == 'data':
                self.data_connected = True
            if not (
                self.control_connected and self.data_connected
                and not self._client_ready
            ):
                return
            if self.control_network.session_id != self.data_network.session_id:
                mismatched = True
            else:
                mismatched = False
                self._client_ready = True
        if mismatched:
            logger.warning("Rejecting lanes from different sessions")
            self.data_network.disconnect()
        else:
            self.on_client_connected()

    def _on_socket_disconnected(self, sock_type):
        with self._client_state_lock:
            if sock_type == 'control':
                self.control_connected = False
            elif sock_type == 'data':
                self.data_connected = False
            if self._disconnecting:
                return
            self._disconnecting = True
            was_ready = self._client_ready
            self._client_ready = False
        try:
            self.session_coordinator.close()
            self.file_network.revoke_session()
            self.control_network.disconnect()
            self.data_network.disconnect()
            if was_ready:
                self.on_client_disconnected()
        finally:
            with self._client_state_lock:
                self._disconnecting = False

    def on_client_connected(self):
        logger.info(f"Client connected on both ports, starting edge detection for layout: {self.layout_position}")
        # Send handshake layout config over control
        self.control_network.send_message({
            'type': 'layout_config',
            'position': self.layout_position,
            'server_width': self.input_handler.screen_width,
            'server_height': self.input_handler.screen_height
        })
        self.input_handler.start_edge_detection(self.layout_position)
        self.clipboard.start()
        self.hotkey_monitor.start()
        self.pressed_keys.clear()
        self._offer_file_lane()

    def _offer_file_lane(self):
        offer = self.control_network.session_offer
        if offer is None or offer.session_id != self.data_network.session_id:
            raise RuntimeError("file lane cannot be offered before session binding")
        self.file_network.offer_session(offer.file_token, offer.session_id)
        self.control_network.send_message({
            'type': 'file_lane_offer',
            'port': self.file_network.port,
            'session_id': offer.session_id,
        })

    def on_client_disconnected(self):
        logger.info("Client disconnected, stopping edge detection and wiping clipboard.")
        self.switching_to_client = False
        self.pressed_keys.clear()
        self.forwarded_keys.clear()
        if self.on_capture_stop:
            self.on_capture_stop()
        self.input_handler.stop()
        self.clipboard.stop()
        self.file_network.close()
        self.paste_coordinator.reset()
        self.hotkey_monitor.stop()

    def on_edge_hit(self, direction, ratio):
        if direction == self.layout_position:
            if self.switching_to_client:
                return
            self.switching_to_client = True
            self.paste_coordinator.set_remote_files_available(self.local_files_available)
            
            logger.info(f"Hit {direction} edge. Switching to client.")
            self.control_network.send_message({
                'type': 'switch',
                'direction': direction,
                'ratio': ratio
            })
            self.input_handler.stop() # Stop edge detection
            self.input_handler.start_keyboard_capture()
            if self.on_capture_start:
                self.on_capture_start()

    def on_switch_back(self, data):
        # Client hit its return edge
        logger.info("Client signaled switch back.")
        self._release_forwarded_keys()
        self.switching_to_client = False
        self.paste_coordinator.set_remote_files_available(self.remote_files_available)
        ratio = data.get('ratio', 0.5)
        self.input_handler.stop_keyboard_capture()
        if self.on_capture_stop:
            self.on_capture_stop()
            
        # Warp the server mouse cleanly to the boundary
        w = self.input_handler.screen_width
        h = self.input_handler.screen_height
        if self.layout_position == 'right':
            self.input_handler.inject_position(w - 2, int(h * ratio))
        elif self.layout_position == 'left':
            self.input_handler.inject_position(2, int(h * ratio))
        elif self.layout_position == 'top':
            self.input_handler.inject_position(int(w * ratio), 2)
        elif self.layout_position == 'bottom':
            self.input_handler.inject_position(int(w * ratio), h - 2)
            
        self.input_handler.start_edge_detection(self.layout_position)

    def on_mouse_move(self, dx, dy):
        self.control_network.send_message({
            'type': 'mouse_move',
            'dx': dx,
            'dy': dy
        })

    def on_mouse_click(self, button, pressed):
        self.control_network.send_message({
            'type': 'mouse_click',
            'button': button,
            'pressed': pressed
        })

    def on_mouse_scroll(self, dx, dy):
        self.control_network.send_message({
            'type': 'mouse_scroll',
            'dx': dx,
            'dy': dy
        })

    def on_key_press(self, key_data):
        val = key_data.get('value')
        if val:
            self.pressed_keys.add(val)
            if self.paste_coordinator.on_key_press(val):
                return

        # Check emergency exit (Ctrl+Alt+Shift+Escape) & Reload Connection (Ctrl+Alt+Shift+R)
        has_ctrl = any(k in self.pressed_keys for k in ('ctrl', 'ctrl_l', 'ctrl_r'))
        has_alt = any(k in self.pressed_keys for k in ('alt', 'alt_l', 'alt_r', 'alt_gr'))
        has_shift = any(k in self.pressed_keys for k in ('shift', 'shift_l', 'shift_r'))
        has_esc = val in ('esc', 'escape')
        has_r = val in ('r', 'R')
        
        if has_ctrl and has_alt and has_shift and has_esc:
            logger.warning("EMERGENCY EXIT TRIGGERED! Forcefully disconnecting client and returning control.")
            self._release_forwarded_keys()
            self.control_network.disconnect()
            self.data_network.disconnect()
            return

        if has_ctrl and has_alt and has_shift and has_r:
            logger.warning("RELOAD CONNECTION TRIGGERED (Ctrl+Shift+Alt+R)! Soft-resetting active connection and restoring local control.")
            self._release_forwarded_keys()
            self._reload_connection()
            return

        self.forwarded_keys[self._key_identity(key_data)] = dict(key_data)
        self.control_network.send_message({
            'type': 'key_press',
            'key': key_data
        })

    def on_key_release(self, key_data):
        val = key_data.get('value')
        if val and self.paste_coordinator.on_key_release(val):
            self.pressed_keys.discard(val)
            return
        self.forwarded_keys.pop(self._key_identity(key_data), None)
        if val in self.pressed_keys:
            self.pressed_keys.discard(val)
            
        self.control_network.send_message({
            'type': 'key_release',
            'key': key_data
        })

    @staticmethod
    def _key_identity(key_data):
        return (
            key_data.get('type'),
            key_data.get('value'),
            key_data.get('vk'),
            key_data.get('scan'),
            key_data.get('extended'),
        )

    def _release_forwarded_keys(self):
        forwarded = getattr(self, 'forwarded_keys', None)
        if forwarded is None:
            payloads = [
                {'type': 'special', 'value': key}
                for key in sorted(self.pressed_keys - {'esc', 'escape'})
            ]
        else:
            payloads = list(forwarded.values())
        for key_data in payloads:
            self.control_network.send_message({
                'type': 'key_release',
                'key': key_data,
            })
        self.pressed_keys.clear()
        if forwarded is not None:
            forwarded.clear()

    def _reload_connection(self):
        logger.info("Reloading active connection lanes...")
        if hasattr(self, 'input_handler') and self.input_handler:
            try:
                if hasattr(self.input_handler, 'stop_keyboard_capture'):
                    self.input_handler.stop_keyboard_capture()
                if hasattr(self.input_handler, 'stop_capture'):
                    self.input_handler.stop_capture()
            except Exception as error:
                logger.debug("Error stopping capture during reload: %s", error_name(error))
        self.switching_to_client = False
        self.pressed_keys.clear()
        if hasattr(self, 'forwarded_keys'):
            self.forwarded_keys.clear()
        if getattr(self, 'control_network', None):
            try:
                self.control_network.disconnect()
            except Exception:
                pass
        if getattr(self, 'data_network', None):
            try:
                self.data_network.disconnect()
            except Exception:
                pass

    def on_local_copy(self, snapshot):
        return self.clipboard_sender.submit({"snapshot": snapshot})

    def _send_clipboard_snapshot(self, work):
        payload = encode_clipboard_message(work["snapshot"])
        return self.data_network.send_message(payload)

    def on_remote_copy(self, data):
        self.clipboard.inject(data)

    def on_local_file_availability(self, available):
        self.local_files_available = available is True
        if getattr(self, 'switching_to_client', False):
            self.paste_coordinator.set_remote_files_available(self.local_files_available)
        return self.control_network.send_message({
            'type': 'file_clipboard_available',
            'available': available is True,
        })

    def on_remote_file_availability(self, data):
        self.remote_files_available = data.get('available') is True
        if not getattr(self, 'switching_to_client', False):
            self.paste_coordinator.set_remote_files_available(self.remote_files_available)

    def _request_remote_file_paste(self):
        if getattr(self, 'switching_to_client', False):
            return self.control_network.send_message({'type': 'file_paste_trigger'})
        return self.file_paste_service.request_paste()

    def on_file_manifest_request(self, data):
        self.file_paste_service.on_manifest_request(data)

    def on_file_manifest_response(self, data):
        self.file_paste_service.on_manifest_response(data)

    def on_file_manifest_failed(self, data):
        self.file_paste_service.on_manifest_failed(data)

    def on_file_manifest_ack(self, data):
        self.file_paste_service.on_manifest_ack(data)
