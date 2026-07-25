import logging
import threading
import os
from pathlib import Path
from app.network import NetworkClient
from app.trust import PeerTrustStore
from app.file_transfer.transport import FileLaneClient
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
from app.input_geometry import client_entry_position
from app.safe_errors import error_name, public_error_message
from app.global_hotkey import GlobalHotkeyMonitor

logger = logging.getLogger(__name__)

class DeskFlowClient:
    def __init__(
        self, password, on_transfer_status=None, fingerprint_approval=None,
        trust_store=None, lane_timeout=10.0,
    ):
        self.password = password
        self.trust_store = trust_store or PeerTrustStore()
        self.fingerprint_approval = fingerprint_approval
        self.lane_timeout = float(lane_timeout)
        self.control_network = NetworkClient(
            password, role='control', trust_store=self.trust_store,
            fingerprint_approval=fingerprint_approval,
        )
        self.data_network = None
        self.file_network = FileLaneClient()
        self.transfer_controller = TransferController()
        if on_transfer_status:
            self.transfer_controller.subscribe(on_transfer_status)
        self.file_receiver = TransferReceiver(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'DeskFlow' / 'transfers' / 'client', controller=self.transfer_controller)
        self.file_receiver.attach(self.file_network)
        self.transfer_cancellation = TransferCancellation(
            self.file_network, self.transfer_controller, self.file_receiver
        )
        self.file_network.register_callback(
            'disconnected', lambda metadata, payload: self.on_disconnected(metadata)
        )
        self.file_publisher = VirtualPastePublisher()
        self.input_handler = InputHandler()
        self.global_hotkey_monitor = GlobalHotkeyMonitor(
            on_emergency_exit=self.disconnect,
            on_reload_connection=self.reload_connection,
        )
        self.is_active = False
        self.control_connected = False
        self.data_connected = False
        self._disconnecting = False
        
        # Setup control network callbacks
        self.control_network.register_callback('layout_config', self.on_layout_config)
        self.control_network.register_callback('switch', self.on_switch)
        self.control_network.register_callback('mouse_move', self.on_mouse_move)
        self.control_network.register_callback('mouse_click', self.on_mouse_click)
        self.control_network.register_callback('mouse_scroll', self.on_mouse_scroll)
        self.control_network.register_callback('key_press', self.on_key_press)
        self.control_network.register_callback('key_release', self.on_key_release)
        self.control_network.register_callback('disconnected', self.on_disconnected)
        self.control_network.register_callback('file_lane_offer', self.on_file_lane_offer)
        self.control_network.register_callback('file_clipboard_available', self.on_remote_file_availability)
        self.control_network.register_callback('file_manifest_request', self.on_file_manifest_request)
        self.control_network.register_callback('file_manifest_response', self.on_file_manifest_response)
        self.control_network.register_callback('file_manifest_failed', self.on_file_manifest_failed)
        self.control_network.register_callback('file_manifest_ack', self.on_file_manifest_ack)
        self.control_network.register_callback('file_paste_trigger', lambda data: self.file_paste_service.request_paste())
        
        # Setup data network callbacks
        # Setup input callbacks
        self.input_handler.register_callback('client_edge_hit', self.on_client_edge_hit)

        # Setup clipboard
        self.clipboard = ClipboardHandler(
            on_clipboard_change=self.on_local_copy,
            on_file_availability=self.on_local_file_availability,
        )
        self.paste_coordinator = PasteCoordinator(self._request_remote_file_paste)
        self.hotkey_monitor = WindowsPasteHotkeyMonitor(self.paste_coordinator)
        self.file_paste_service = FilePasteService(
            self.control_network, self.file_receiver, self.file_publisher,
            TransferSender(self.file_network, controller=self.transfer_controller),
            lambda: snapshot_selection(self.clipboard.read_file_selection()),
        )
        self.clipboard_sender = LatestWinsSender(self._send_clipboard_snapshot)
        self.speed_scale_x = 1.0
        self.speed_scale_y = 1.0

    def cancel_transfer(self, job_id):
        return self.transfer_cancellation.request(job_id)

    def on_disconnected(self, data):
        logger.info("Disconnected from Server.")
        report_setup_failure = False
        internal_disconnect = False
        if hasattr(self, '_connect_lock'):
            with self._connect_lock:
                internal_disconnect = self._disconnecting
                self.control_connected = False
                self.data_connected = False
                self.file_connected = False
                if not internal_disconnect and not self._connect_callback_done:
                    self.connect_error = "secure session disconnected during setup"
                    report_setup_failure = True
        self.is_active = False
        self.clipboard.stop()
        self.clipboard_sender.stop()
        self.paste_coordinator.reset()
        self.hotkey_monitor.stop()
        self.global_hotkey_monitor.stop()
        if internal_disconnect:
            return
        if report_setup_failure:
            error = ConnectionError(self.connect_error)
            self.disconnect(preserve_failure=True, error=error)
            self._report_connect(
                False,
                "Connection was interrupted before setup finished. "
                "Check the network and try again.",
            )
        else:
            self.disconnect()

    def reload_connection(self):
        logger.info("RELOAD CONNECTION TRIGGERED on Client (Ctrl+Shift+Alt+R)! Soft-resetting and auto-reconnecting...")
        if hasattr(self, 'input_handler') and self.input_handler:
            try:
                self.input_handler.release_all_injected_keys()
            except Exception:
                pass
        host = getattr(self, 'host', None)
        port = getattr(self, 'port', None)
        callback = getattr(self, '_connect_callback', None)
        
        self.disconnect()
        
        if host and port and callback:
            def _auto_reconnect():
                import time
                time.sleep(0.5)
                logger.info("Auto-reconnecting client to %s:%d...", host, port)
                self.connect(host, port, callback)
            threading.Thread(target=_auto_reconnect, daemon=True).start()

    def set_screen_size(self, w, h):
        self.input_handler.set_screen_size(w, h)

    def connect(self, host, port, callback):
        self.host = host
        self.control_connected = False
        self.data_connected = False
        self.connect_error = None
        self.file_connected = False
        self._connect_callback = callback
        self._connect_callback_done = False
        self._connect_lock = threading.RLock()
        self._connect_deadline = None
        self._ready_started = False
        
        def _check_both_connected():
            self._maybe_finish_connect()

        def _control_callback(success, err):
            if success:
                logger.info("[1/3] Control lane (port %d): CONNECTED", port)
                self.control_connected = True
                self._connect_deadline = threading.Timer(
                    self.lane_timeout, self._on_lane_binding_timeout
                )
                self._connect_deadline.daemon = True
                self._connect_deadline.start()
                session = self.control_network.session_info
                self.data_network = NetworkClient(
                    self.password,
                    role='data',
                    trust_store=self.trust_store,
                    expected_fingerprint=self.control_network.peer_certificate_fingerprint(),
                    lane_token=session['data_token'],
                    session_id=session['session_id'],
                )
                self.data_network.register_callback('clipboard_sync', self.on_remote_copy)
                self.data_network.register_callback('disconnected', self.on_disconnected)
                logger.info("[2/3] Connecting data lane (port %d)...", port + 1)
                self.data_network.connect(host, port + 1, _data_callback)
            else:
                logger.error("[1/3] Control lane (port %d) FAILED: %s", port, err)
                self.connect_error = err
                self.disconnect(
                    preserve_failure=True,
                    error=self.control_network.last_error or ConnectionError(err),
                )
                self._report_connect(False, err)

        def _data_callback(success, err):
            if success:
                logger.info("[2/3] Data lane (port %d): CONNECTED", port + 1)
                self.data_connected = True
                _check_both_connected()
            else:
                logger.error("[2/3] Data lane (port %d) FAILED: %s", port + 1, err)
                self.connect_error = err
                self.disconnect(
                    preserve_failure=True,
                    error=self.data_network.last_error or ConnectionError(err),
                )
                self._report_connect(False, err)

        logger.info("[1/3] Connecting control lane to %s:%d...", host, port)
        self.control_network.connect(host, port, _control_callback)

    def _report_connect(self, success, error):
        with self._connect_lock:
            if self._connect_callback_done:
                return
            self._connect_callback_done = True
            callback = self._connect_callback
            deadline, self._connect_deadline = self._connect_deadline, None
        if deadline is not None:
            deadline.cancel()
        if callback:
            callback(success, error)

    def _maybe_finish_connect(self):
        with self._connect_lock:
            if (
                self.connect_error or self._connect_callback_done
                or self._ready_started
                or not (
                    self.control_connected and self.data_connected
                    and self.file_connected
                )
                or not self.control_network.connected
                or self.data_network is None
                or not self.data_network.connected
                or self.file_network.sock is None
            ):
                logger.debug(
                    "Connection check incomplete: control=%s, data=%s, file=%s",
                    self.control_connected, self.data_connected, self.file_connected
                )
                return False
            self._ready_started = True
            try:
                self.control_network.commit_peer_trust()
                if not self._all_lanes_live():
                    raise ConnectionError(
                        "secure session disconnected while becoming ready"
                    )
                self.clipboard.start()
                self.hotkey_monitor.start()
                self.global_hotkey_monitor.start()
                if not self._all_lanes_live():
                    self.clipboard.stop()
                    self.hotkey_monitor.stop()
                    self.global_hotkey_monitor.stop()
                    raise ConnectionError(
                        "secure session disconnected while starting services"
                    )
                logger.info("[ALL LANES BOUND] Control, Data, and File lanes successfully established!")
                self._report_connect(True, None)
                return True
            except Exception as error:
                logger.error("Session finish failed: %s", error, exc_info=True)
                message = public_error_message(error, "secure session setup failed")
                self.connect_error = message
                self.clipboard.stop()
                self.hotkey_monitor.stop()
                self.disconnect(preserve_failure=True, error=error)
                self._report_connect(False, message)
                return False

    def _all_lanes_live(self):
        return (
            self.control_connected and self.data_connected
            and self.file_connected and self.control_network.connected
            and self.data_network is not None
            and self.data_network.connected
            and self.file_network.sock is not None
        )

    def _on_lane_binding_timeout(self):
        timeout_val = getattr(self, 'lane_timeout', 10.0)
        control_net = getattr(self, 'control_network', None)
        data_net = getattr(self, 'data_network', None)
        file_net = getattr(self, 'file_network', None)
        logger.error(
            "Connection deadline expired after %.1fs. Lane status: control=%s (connected=%s), data=%s (connected=%s), file=%s (sock=%s). Check port %s:5002 firewall.",
            timeout_val,
            getattr(self, 'control_connected', False), getattr(control_net, 'connected', False) if control_net else False,
            getattr(self, 'data_connected', False), getattr(data_net, 'connected', False) if data_net else False,
            getattr(self, 'file_connected', False), getattr(file_net, 'sock', None) is not None if file_net else False,
            getattr(self, 'host', 'server'),
        )
        error = TimeoutError(
            f"Secondary lanes failed to bind within {timeout_val}s "
            f"(control={getattr(self, 'control_connected', False)}, data={getattr(self, 'data_connected', False)}, file={getattr(self, 'file_connected', False)})"
        )
        self.connect_error = str(error)
        self.disconnect(preserve_failure=True, error=error)
        self._report_connect(
            False,
            "Connection timed out before setup finished. "
            "Check the network and try again.",
        )

    def disconnect(self, preserve_failure=False, error=None):
        input_handler = getattr(self, 'input_handler', None)
        if input_handler is not None:
            input_handler.release_all_injected_keys()
        if hasattr(self, '_connect_lock'):
            with self._connect_lock:
                if self._disconnecting:
                    return False
                self._disconnecting = True
        elif self._disconnecting:
            return False
        else:
            self._disconnecting = True
        try:
            self.control_network.disconnect(
                preserve_failure=preserve_failure, error=error
            )
            if self.data_network is not None:
                self.data_network.disconnect(
                    preserve_failure=preserve_failure, error=error
                )
            self.file_network.close()
            return True
        finally:
            self._disconnecting = False

    def on_file_lane_offer(self, data):
        logger.info("[3/3] Received file_lane_offer for port %s (session %s)", data.get('port'), str(data.get('session_id'))[:8])
        def connect_file_lane():
            try:
                self._connect_file_lane(data)
            except Exception as error:
                logger.error(
                    "[3/3] Secure file lane connection failed (%s: %s)",
                    error_name(error), error, exc_info=True
                )
                message = public_error_message(error, "secure file connection failed")
                self.connect_error = message
                self.disconnect(preserve_failure=True, error=error)
                self._report_connect(False, message)

        threading.Thread(target=connect_file_lane, daemon=True).start()

    def _connect_file_lane(self, data):
        port = data.get('port')
        session_id = data.get('session_id')
        session = self.control_network.session_info or {}
        token = session.get('file_token')
        if (not isinstance(port, int) or not isinstance(token, str)
                or session_id != session.get('session_id')):
            logger.error("[3/3] File lane offer malformed or session mismatched: port=%s, session_id=%s, token_len=%s", port, session_id, len(token) if token else 0)
            raise ValueError("file-lane offer is malformed")
        fingerprint = self.control_network.peer_certificate_fingerprint()
        logger.info("[3/3] Connecting file lane to %s:%d...", self.host, port)
        self.file_network.connect(self.host, port, fingerprint, token, session_id=session_id)
        self.file_connected = True
        logger.info("[3/3] File lane (port %d): CONNECTED", port)
        if self.control_connected and self.data_connected:
            self._maybe_finish_connect()

    def on_layout_config(self, data):
        server_pos = data.get('position', 'right')
        server_w = data.get('server_width', 1920)
        server_h = data.get('server_height', 1080)
        
        logger.info(f"Received layout config. Client is positioned at server's {server_pos} ({server_w}x{server_h})")
        
        # Calculate resolution scaling ratios
        client_w = self.input_handler.screen_width
        client_h = self.input_handler.screen_height
        
        self.speed_scale_x = client_w / server_w
        self.speed_scale_y = client_h / server_h
        logger.info(f"Resolution scaling factor calculated: X={self.speed_scale_x:.3f}, Y={self.speed_scale_y:.3f}")
        
        # Calculate our return edge (opposite of our position relative to server)
        # If client is to the right of server, return edge is left.
        # If client is below server (bottom), return edge is top.
        opposites = {
            'right': 'left',
            'left': 'right',
            'top': 'bottom',
            'bottom': 'top'
        }
        return_edge = opposites.get(server_pos, 'left')
        self.input_handler.set_layout(server_edge=server_pos, client_edge=return_edge)

    def on_switch(self, data):
        logger.info("Server switched control to this client.")
        self.is_active = True
        direction = data.get('direction')
        ratio = data.get('ratio', 0.5)
        
        w = self.input_handler.screen_width
        h = self.input_handler.screen_height
        
        self.input_handler.inject_position(
            *client_entry_position(direction, w, h, ratio)
        )

    def on_mouse_move(self, data):
        dx = data.get('dx', 0) * self.speed_scale_x
        dy = data.get('dy', 0) * self.speed_scale_y
        self.input_handler.inject_move(dx, dy)

    def on_mouse_click(self, data):
        button_name = data.get('button')
        pressed = data.get('pressed')
        self.input_handler.inject_click(button_name, pressed)

    def on_mouse_scroll(self, data):
        dx = data.get('dx', 0)
        dy = data.get('dy', 0)
        self.input_handler.inject_scroll(dx, dy)

    def on_key_press(self, data):
        key_data = data.get('key')
        if key_data:
            self.input_handler.inject_key_press(key_data)

    def on_key_release(self, data):
        key_data = data.get('key')
        if key_data:
            self.input_handler.inject_key_release(key_data)

    def on_client_edge_hit(self, direction, ratio):
        if not self.is_active:
            return
            
        if direction == self.input_handler.client_edge:
            logger.info(f"Hit {direction} edge. Sending switch_back to server.")
            self.is_active = False
            self.input_handler.release_all_injected_keys()
            self.control_network.send_message({
                'type': 'switch_back',
                'ratio': ratio
            })

    def on_local_copy(self, snapshot):
        return self.clipboard_sender.submit({"snapshot": snapshot})

    def _send_clipboard_snapshot(self, work):
        payload = encode_clipboard_message(work["snapshot"])
        return self.data_network is not None and self.data_network.send_message(payload)

    def on_remote_copy(self, data):
        self.clipboard.inject(data)

    def on_local_file_availability(self, available):
        return self.control_network.send_message({
            'type': 'file_clipboard_available',
            'available': available is True,
        })

    def on_remote_file_availability(self, data):
        self.paste_coordinator.set_remote_files_available(data.get('available') is True)

    def _request_remote_file_paste(self):
        return self.file_paste_service.request_paste()

    def on_file_manifest_request(self, data):
        self.file_paste_service.on_manifest_request(data)

    def on_file_manifest_response(self, data):
        self.file_paste_service.on_manifest_response(data)

    def on_file_manifest_failed(self, data):
        self.file_paste_service.on_manifest_failed(data)

    def on_file_manifest_ack(self, data):
        self.file_paste_service.on_manifest_ack(data)
