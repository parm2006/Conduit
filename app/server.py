import logging
import os
import threading
from pathlib import Path
from app.network import NetworkServer
from app.crypto import load_identity
from app.session import SessionCoordinator
from app.file_transfer.transport import FileLaneServer
from app.file_transfer.paste_coordinator import ClipboardOfferState, PasteCoordinator
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
from app.global_hotkey import GlobalHotkeyMonitor
from app.ports import DEFAULT_BASE_PORT
from app.display_topology import NativeRect, edge_entry_point

logger = logging.getLogger(__name__)


def _message_rect(values):
    if not isinstance(values, list) or len(values) != 4:
        return None
    if any(type(value) is not int for value in values):
        return None
    left, top, right, bottom = values
    if right <= left or bottom <= top:
        return None
    return NativeRect(left, top, right, bottom)

class ConduitServer:
    def __init__(self, password, port=DEFAULT_BASE_PORT, on_capture_start=None, on_capture_stop=None, on_transfer_status=None, on_app_shutdown=None, on_topology_edit_cancel=None):
        self._active_edge_side = None
        self.on_capture_start = on_capture_start
        self.on_capture_stop = on_capture_stop
        self.on_app_shutdown = on_app_shutdown
        self.on_topology_edit_cancel = on_topology_edit_cancel
        
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
        self.file_receiver = TransferReceiver(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Conduit' / 'transfers' / 'server', controller=self.transfer_controller)
        self.file_receiver.attach(self.file_network)
        self.transfer_cancellation = TransferCancellation(
            self.file_network, self.transfer_controller, self.file_receiver
        )
        self.file_publisher = VirtualPastePublisher(
            on_clipboard_change_begin=(
                lambda: self.clipboard.begin_internal_change()
            ),
            on_clipboard_change_end=(
                lambda suppress: self.clipboard.end_internal_change(suppress)
            ),
        )
        self.input_handler = InputHandler()
        self._paste_route_lock = threading.RLock()
        self.global_hotkey_monitor = GlobalHotkeyMonitor(
            on_emergency_exit=self._request_app_shutdown,
            on_reload_connection=self._reload_connection,
        )
        
        self.control_connected = False
        self.data_connected = False
        self._client_ready = False
        self._disconnecting = False
        self._client_state_lock = threading.RLock()
        self._topology_ack_lock = threading.Lock()
        self._topology_ack_event = None
        self._topology_ack_version = None
        self._topology_commit_ack_event = None
        self._topology_commit_ack_version = None
        
        # Setup control network callbacks
        self.control_network.register_callback('connected', lambda d: self._on_socket_connected('control', d))
        self.control_network.register_callback('disconnected', lambda d: self._on_socket_disconnected('control'))
        self.control_network.register_callback('switch_back', self.on_switch_back)
        self.control_network.register_callback('topology_ack', self.on_topology_ack)
        self.control_network.register_callback(
            'topology_commit_ack',
            self.on_topology_commit_ack,
        )
        self.control_network.register_callback(
            'clipboard_offer', self.on_remote_clipboard_offer
        )
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
            on_clipboard_offer=self.on_local_clipboard_offer,
        )
        self.paste_coordinator = PasteCoordinator(self._request_remote_file_paste)
        self.paste_coordinator.before_paste = (
            self._refresh_active_destination_offer
        )
        self.hotkey_monitor = WindowsPasteHotkeyMonitor(self.paste_coordinator)
        self.clipboard_offer_state = ClipboardOfferState("server")
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

    def _get_paste_route_lock(self):
        lock = getattr(self, "_paste_route_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._paste_route_lock = lock
        return lock

    def set_screen_size(self, w, h):
        self.input_handler.set_screen_size(w, h)

    def start(self):
        c_success = self.control_network.start()
        d_success = self.data_network.start()
        f_success = self.file_network.start()
        if c_success and d_success and f_success:
            self.global_hotkey_monitor.start()
            return True
        self.stop()
        return False

    def stop(self):
        self.global_hotkey_monitor.stop()
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
        logger.info(
            "Client connected on all lanes; waiting for topology Apply before input routing."
        )
        self.control_network.send_message({'type': 'display_inventory_request'})
        self.clipboard.start()
        self.hotkey_monitor.start()
        self.pressed_keys.clear()
        self._offer_file_lane()

    def activate_client_topology(self, topology):
        if isinstance(topology, str):
            position = topology
            message = {
                'type': 'layout_config',
                'position': position,
                'server_width': self.input_handler.screen_width,
                'server_height': self.input_handler.screen_height,
            }
            self._active_edge_side = position
            self.control_network.send_message(message)
            self.input_handler.start_edge_detection(position)
            return

        self._install_topology(topology)
        message = self._topology_layout_message(topology, 'layout_config')
        if message is not None:
            self.control_network.send_message(message)

    def _install_topology(self, topology):
        self.active_topology = topology
        self.input_handler.configure_topology_edges(topology, topology.server_id)
        message = self._topology_layout_message(topology, 'layout_config')
        if message is None:
            return
        self._active_edge_side = message['position']
        if getattr(self, 'control_connected', True):
            self.input_handler.start_edge_detection()

    def _restore_topology(self, topology):
        if topology is not None:
            self._install_topology(topology)
            return
        self.active_topology = None
        self._active_edge_side = None
        self.input_handler.stop()
        self.input_handler.clear_topology_edges()

    def _topology_layout_message(self, topology, message_type):
        mappings = tuple(
            mapping
            for mapping in topology.edge_mappings
            if mapping.source_machine_id == topology.server_id
            and mapping.destination_machine_id != topology.server_id
        )
        if not mappings:
            return None
        machines = {
            placed.group.machine_id: placed.group
            for placed in topology.machines
        }
        mapping = mappings[0]
        server_display = machines[topology.server_id].display(mapping.source_display_id)
        client_display = machines[mapping.destination_machine_id].display(
            mapping.destination_display_id
        )
        return {
            'type': message_type,
            'position': mapping.source_side,
            'server_width': self.input_handler.screen_width,
            'server_height': self.input_handler.screen_height,
            'server_display_id': mapping.source_display_id,
            'server_rect': [
                server_display.rect.left,
                server_display.rect.top,
                server_display.rect.right,
                server_display.rect.bottom,
            ],
            'client_display_id': mapping.destination_display_id,
            'client_rect': [
                client_display.rect.left,
                client_display.rect.top,
                client_display.rect.right,
                client_display.rect.bottom,
            ],
            'client_edge': mapping.destination_side,
        }

    def apply_topology_candidate(
        self,
        topology,
        on_persist,
        on_complete,
        timeout=3.0,
    ):
        previous = getattr(self, 'active_topology', None)
        self._release_forwarded_keys()
        self.switching_to_client = False
        self.input_handler.release_all_injected_keys()
        self.input_handler.stop()
        if self.on_capture_stop:
            self.on_capture_stop()
        server_group = next(
            placed.group
            for placed in topology.machines
            if placed.group.machine_id == topology.server_id
        )
        primary = next(
            display
            for display in server_group.displays
            if display.enabled and display.primary
        )
        self.input_handler.inject_position(
            (primary.rect.left + primary.rect.right) // 2,
            (primary.rect.top + primary.rect.bottom) // 2,
        )
        event = threading.Event()
        with self._topology_ack_lock:
            self._topology_ack_event = event
            self._topology_ack_version = topology.version
        message = self._topology_layout_message(topology, 'topology_apply')
        if message is None:
            with self._topology_ack_lock:
                self._topology_ack_event = None
                self._topology_ack_version = None
            try:
                persisted = bool(on_persist(topology))
            except Exception:
                persisted = False
            if persisted:
                try:
                    self._install_topology(topology)
                except Exception as error:
                    logger.error(
                        "Could not install topology (%s)",
                        error_name(error),
                    )
                    persisted = False
                    self._restore_topology(previous)
            on_complete(persisted)
            return
        message['version'] = topology.version
        sent = self.control_network.send_message(message)

        def finish():
            acknowledged = bool(sent and event.wait(timeout))
            with self._topology_ack_lock:
                self._topology_ack_event = None
                self._topology_ack_version = None
            committed = False
            commit_event = threading.Event()
            if acknowledged:
                with self._topology_ack_lock:
                    self._topology_commit_ack_event = commit_event
                    self._topology_commit_ack_version = topology.version
                commit_sent = self.control_network.send_message({
                    'type': 'topology_commit',
                    'version': topology.version,
                })
                committed = bool(commit_sent and commit_event.wait(timeout))
                with self._topology_ack_lock:
                    self._topology_commit_ack_event = None
                    self._topology_commit_ack_version = None
            persisted = False
            if committed:
                try:
                    self._install_topology(topology)
                except Exception as error:
                    logger.error(
                        "Could not install acknowledged topology (%s)",
                        error_name(error),
                    )
                else:
                    try:
                        persisted = bool(on_persist(topology))
                    except Exception as error:
                        logger.error(
                            "Could not persist acknowledged topology (%s)",
                            error_name(error),
                        )
            if not persisted:
                self.control_network.send_message({
                    'type': 'topology_rollback',
                    'version': topology.version,
                })
                self._restore_topology(previous)
            else:
                self.control_network.send_message({
                    'type': 'topology_finalize',
                    'version': topology.version,
                })
            on_complete(persisted)

        threading.Thread(target=finish, daemon=True).start()

    def on_topology_ack(self, data):
        version = data.get('version')
        with self._topology_ack_lock:
            if version != self._topology_ack_version:
                return False
            event = self._topology_ack_event
            if event is None:
                return False
            event.set()
            return True

    def on_topology_commit_ack(self, data):
        version = data.get('version')
        with self._topology_ack_lock:
            if version != self._topology_commit_ack_version:
                return False
            event = self._topology_commit_ack_event
            if event is None:
                return False
            event.set()
            return True

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

    def on_edge_hit(self, direction, ratio, region=None):
        with self._get_paste_route_lock():
            if region is not None or direction == self._active_edge_side:
                paste_service = getattr(self, "file_paste_service", None)
                if (
                    paste_service is not None
                    and paste_service.destination_paste_active
                ):
                    logger.info(
                        "Ignoring screen edge while the local paste destination is active."
                    )
                    return
                if self.switching_to_client:
                    return
                self.switching_to_client = True
                self.active_edge_region = region
                cancel_edit = getattr(self, 'on_topology_edit_cancel', None)
                if cancel_edit is not None:
                    cancel_edit()
                self._apply_clipboard_offer_route()

                logger.info(f"Hit {direction} edge. Switching to client.")
                message = {
                    'type': 'switch',
                    'direction': direction,
                    'ratio': ratio
                }
                if region is not None:
                    message.update(
                        {
                            'source_display_id': region.source_display_id,
                            'source_side': region.source_side,
                            'source_rect': [
                                region.source_rect.left,
                                region.source_rect.top,
                                region.source_rect.right,
                                region.source_rect.bottom,
                            ],
                            'destination_display_id': region.destination_display_id,
                            'destination_side': region.destination_side,
                            'destination_rect': [
                                region.destination_rect.left,
                                region.destination_rect.top,
                                region.destination_rect.right,
                                region.destination_rect.bottom,
                            ],
                        }
                    )
                self.control_network.send_message(message)
                self.input_handler.stop() # Stop edge detection
                self.input_handler.start_keyboard_capture()
                if self.on_capture_start:
                    self.on_capture_start()

    def on_switch_back(self, data):
        with self._get_paste_route_lock():
            return self._on_switch_back_locked(data)

    def _on_switch_back_locked(self, data):
        # Client hit its return edge
        logger.info("Client signaled switch back.")
        self._release_forwarded_keys()
        self.switching_to_client = False
        self._apply_clipboard_offer_route()
        ratio = data.get('ratio', 0.5)
        self.input_handler.stop_keyboard_capture()
        if self.on_capture_stop:
            self.on_capture_stop()

        destination_rect = _message_rect(data.get('destination_rect'))
        destination_side = data.get('destination_side')
        if destination_rect is not None and destination_side in {
            'left', 'right', 'top', 'bottom'
        }:
            self.input_handler.inject_position(
                *edge_entry_point(destination_rect, destination_side, ratio)
            )
        else:
            # Legacy peers still return through the scalar primary-screen edge.
            w = self.input_handler.screen_width
            h = self.input_handler.screen_height
            if self._active_edge_side == 'right':
                self.input_handler.inject_position(w - 2, int(h * ratio))
            elif self._active_edge_side == 'left':
                self.input_handler.inject_position(2, int(h * ratio))
            elif self._active_edge_side == 'top':
                self.input_handler.inject_position(int(w * ratio), 2)
            elif self._active_edge_side == 'bottom':
                self.input_handler.inject_position(int(w * ratio), h - 2)

        if hasattr(self.input_handler, 'topology_edge_regions'):
            self.input_handler.start_edge_detection()
        else:
            self.input_handler.start_edge_detection(self._active_edge_side)

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
            self._request_app_shutdown()
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

    def set_screen_size(self, w, h):
        self._screen_width = w
        self._screen_height = h
        self.input_handler.set_screen_size(w, h)

    def _on_emergency_exit(self):
        with self._get_paste_route_lock():
            return self._emergency_exit_locked()

    def _request_app_shutdown(self):
        self.prepare_app_shutdown()
        callback = getattr(self, "on_app_shutdown", None)
        if callback is not None:
            callback()
            return
        self._on_emergency_exit()

    def prepare_app_shutdown(self):
        self._release_forwarded_keys()

    def _emergency_exit_locked(self):
        mouse_loc = "REMOTE CLIENT SCREEN" if getattr(self, "switching_to_client", False) else "LOCAL HOST SCREEN"
        logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+Escape triggered on Server! Cursor location: %s. Forcefully disconnecting client and returning control.", mouse_loc)
        self._release_forwarded_keys()
        self.switching_to_client = False
        self.pressed_keys.clear()
        if hasattr(self, 'forwarded_keys'):
            self.forwarded_keys.clear()
        if getattr(self, 'on_capture_stop', None):
            try:
                self.on_capture_stop()
            except Exception as error:
                logger.debug("Error calling on_capture_stop: %s", error_name(error))
        lock = getattr(self, "_client_state_lock", None)
        if lock:
            with lock:
                self._client_ready = False
                self.control_connected = False
                self.data_connected = False
        else:
            self._client_ready = False
            self.control_connected = False
            self.data_connected = False
        if hasattr(self, 'input_handler') and self.input_handler:
            try:
                self.input_handler.stop()
            except Exception:
                pass
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
        if getattr(self, 'session_coordinator', None):
            try:
                self.session_coordinator.close()
            except Exception:
                pass
        if getattr(self, 'file_network', None):
            try:
                self.file_network.revoke_session()
            except Exception:
                pass

    def _reload_connection(self):
        mouse_loc = "REMOTE CLIENT SCREEN" if getattr(self, "switching_to_client", False) else "LOCAL HOST SCREEN"
        logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+R triggered on Server! Cursor location: %s. Soft-resetting active connection and restoring local control.", mouse_loc)
        if getattr(self, 'control_network', None) and getattr(self.control_network, 'connected', False):
            try:
                self.control_network.send_message({'type': 'reload_connection'})
            except Exception as error:
                logger.debug("Could not send reload_connection message: %s", error_name(error))
        self._on_emergency_exit()

    def on_local_copy(self, snapshot):
        work = {"snapshot": snapshot}
        state = getattr(self, "clipboard_offer_state", None)
        if (
            state is not None
            and state.current_offer is not None
            and state.current_offer.source == "server"
            and state.current_offer.kind == "ordinary"
        ):
            work["offer"] = state.current_offer
        queued = self.clipboard_sender.submit(work)
        logger.info(
            "Clipboard snapshot queued (role=server formats=%s bytes=%d queued=%s)",
            ",".join(entry.kind for entry in snapshot.entries),
            sum(len(entry.data) for entry in snapshot.entries),
            queued,
        )
        return queued

    def _send_clipboard_snapshot(self, work):
        snapshot = work["snapshot"]
        payload = encode_clipboard_message(snapshot)
        offer = work.get("offer")
        if offer is not None:
            payload["offer"] = offer.to_message()
        sent = self.data_network.send_message(payload)
        logger.info(
            "Clipboard snapshot sent (role=server formats=%s bytes=%d delivered=%s)",
            ",".join(entry.kind for entry in snapshot.entries),
            sum(len(entry.data) for entry in snapshot.entries),
            sent,
        )
        return sent

    def on_remote_copy(self, data):
        payload = self._accepted_clipboard_payload(data)
        if payload is None:
            logger.info("Stale clipboard snapshot discarded (role=server)")
            return False
        logger.info("Clipboard snapshot received (role=server)")
        return self.clipboard.inject(payload)

    def _accepted_clipboard_payload(self, data):
        offer_data = data.get("offer") if isinstance(data, dict) else None
        if offer_data is None:
            return data
        state = self._get_clipboard_offer_state()
        if not state.accept_snapshot(offer_data):
            return None
        self._apply_clipboard_offer_route()
        payload = dict(data)
        payload.pop("offer", None)
        return payload

    def _clipboard_session_id(self):
        network = getattr(self, "control_network", None)
        return getattr(network, "session_id", None)

    def _get_clipboard_offer_state(self):
        state = getattr(self, "clipboard_offer_state", None)
        if state is None:
            state = ClipboardOfferState("server")
            self.clipboard_offer_state = state
        session_id = self._clipboard_session_id()
        if session_id and state.session_id != session_id:
            state.start_session(session_id)
        return state

    def _apply_clipboard_offer_route(self):
        state = self._get_clipboard_offer_state()
        destination = (
            "client" if getattr(self, "switching_to_client", False) else "server"
        )
        return self.paste_coordinator.set_route(state.current_offer, destination)

    def _refresh_active_destination_offer(self):
        if getattr(self, "switching_to_client", False):
            return None
        clipboard = getattr(self, "clipboard", None)
        refresh = getattr(clipboard, "refresh_offer", None)
        if refresh is None:
            return None
        kind = refresh()
        if kind is None:
            logger.info(
                "Paste deferred because the server clipboard could not be read"
            )
            return False
        self._apply_clipboard_offer_route()
        return kind

    def on_local_clipboard_offer(self, kind, sequence):
        state = self._get_clipboard_offer_state()
        if state.session_id is None:
            return False
        offer = state.observe_local(kind, sequence)
        self._apply_clipboard_offer_route()
        return self.control_network.send_message(offer.to_message())

    def on_remote_clipboard_offer(self, data):
        state = self._get_clipboard_offer_state()
        accepted = state.accept_remote(data)
        if accepted:
            self._apply_clipboard_offer_route()
        return accepted

    def _request_remote_file_paste(self):
        with self._get_paste_route_lock():
            destination_is_client = bool(
                getattr(self, 'switching_to_client', False)
            )
            if destination_is_client:
                return self.control_network.send_message({
                    'type': 'file_paste_intent'
                })
            return self.file_paste_service.request_paste()

    def on_file_manifest_request(self, data):
        self.file_paste_service.on_manifest_request(data)

    def on_file_manifest_response(self, data):
        self.file_paste_service.on_manifest_response(data)

    def on_file_manifest_failed(self, data):
        self.file_paste_service.on_manifest_failed(data)

    def on_file_manifest_ack(self, data):
        self.file_paste_service.on_manifest_ack(data)
