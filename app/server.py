import logging
import os
import threading
from pathlib import Path
from app.network import NetworkServer
from app.crypto import load_identity
from app.session import SessionRegistry
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
from app.file_transfer.cluster_router import (
    ClusterCommandBroadcaster,
    ClusterFileRouter,
    ServerClusterFileLane,
)
from app.input_handler import InputHandler
from app.clipboard_handler import ClipboardHandler
from app.clipboard_formats import decode_clipboard_message, encode_clipboard_message
from app.clipboard_hub import ClipboardHub
from app.latest_wins_sender import LatestWinsSender
from app.safe_errors import error_name
from app.global_hotkey import GlobalHotkeyMonitor
from app.ports import DEFAULT_BASE_PORT
from app.input_router import InputRouter, LocalServer
from app.machine_identity import windows_machine_id

logger = logging.getLogger(__name__)


class _ServerInputEffects:
    def __init__(self, server):
        self.server = server

    def release_local_input(self):
        self.server.pressed_keys.clear()

    def begin_remote_capture(self, session_id):
        if getattr(self.server, "routing_suspended", False):
            return False
        self.server.input_handler.stop()
        if getattr(self.server, "routing_suspended", False):
            return False
        self.server.input_handler.start_keyboard_capture()
        self._notify_capture_ui(self.server.on_capture_start, "start")
        return True

    def restore_local(self, position):
        self._restore_local(position, start_edges=True)

    def restore_paused(self, position):
        self._restore_local(position, start_edges=False)

    def _restore_local(self, position, *, start_edges):
        self.server.input_handler.stop_keyboard_capture()
        self._notify_capture_ui(self.server.on_capture_stop, "stop")
        self.server.input_handler.inject_position(*position)
        if start_edges and not getattr(self.server, "routing_suspended", False):
            self.server.input_handler.start_edge_detection()

    @staticmethod
    def _notify_capture_ui(callback, phase):
        if callback is None:
            return

        def notify():
            try:
                callback()
            except Exception as error:
                logger.debug(
                    "Could not notify capture UI during %s (%s)",
                    phase,
                    error_name(error),
                )

        # Tk callbacks can synchronously wait for the GUI thread. They are
        # cosmetic and must never hold up cursor ownership or the control
        # lane's heartbeat processing.
        threading.Thread(
            target=notify,
            name=f"capture-ui-{phase}",
            daemon=True,
        ).start()


class _ServerClusterControl:
    def __init__(self, server):
        self.server = server

    def send_message(self, message):
        return self.server._handle_cluster_file_control(
            self.server.server_machine_id,
            message,
        )


class ConduitServer:
    def __init__(self, password, port=DEFAULT_BASE_PORT, on_capture_start=None, on_capture_stop=None, on_transfer_status=None, on_app_shutdown=None, on_topology_edit_cancel=None):
        self.on_capture_start = on_capture_start
        self.on_capture_stop = on_capture_stop
        self.on_app_shutdown = on_app_shutdown
        self.on_topology_edit_cancel = on_topology_edit_cancel
        
        self.identity = load_identity()
        self.session_registry = SessionRegistry(password)
        self.control_network = NetworkServer(
            password, '0.0.0.0', port, role='control',
            coordinator=self.session_registry, identity=self.identity,
        )
        self.data_network = NetworkServer(
            password, '0.0.0.0', port + 1, role='data',
            coordinator=self.session_registry, identity=self.identity,
        )
        self.file_network = FileLaneServer(
            host='0.0.0.0', port=port + 2, identity=self.identity,
            coordinator=self.session_registry,
        )
        self.server_machine_id = windows_machine_id()
        self.clipboard_hub = ClipboardHub(self.server_machine_id)
        self.cluster_file_router = ClusterFileRouter(
            self.server_machine_id,
            latest_offer=lambda: self.clipboard_hub.latest_item,
            endpoint_available=self._cluster_endpoint_available,
            send_control=self._send_cluster_file_control,
            send_file=self._send_cluster_file_frame,
        )
        self.cluster_file_lane = ServerClusterFileLane(
            self.server_machine_id,
            self.file_network,
            self.cluster_file_router,
        )
        self.transfer_controller = TransferController()
        if on_transfer_status:
            self.transfer_controller.subscribe(on_transfer_status)
        self.file_receiver = TransferReceiver(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Conduit' / 'transfers' / 'server', controller=self.transfer_controller)
        self.file_receiver.attach(self.cluster_file_lane)
        self.transfer_cancellation = TransferCancellation(
            self.cluster_file_lane,
            self.transfer_controller,
            self.file_receiver,
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
            on_return_to_server=self._return_cursor_to_server,
        )
        
        self.control_connected = False
        self.data_connected = False
        self._client_ready = False
        self._disconnecting = False
        self._disconnecting_sessions = set()
        self._ready_session_ids = set()
        self._clipboard_endpoint_ids = {}
        self._clipboard_sessions_by_endpoint = {}
        self._client_state_lock = threading.RLock()
        self._topology_ack_lock = threading.Lock()
        self._topology_ack_event = None
        self._topology_ack_version = None
        self._topology_commit_ack_event = None
        self._topology_commit_ack_version = None
        self._topology_transaction = None
        self._active_topology_session_ids = set()
        self.routing_suspended = False
        
        # Setup control network callbacks
        self.control_network.register_callback('connected', lambda d: self._on_socket_connected('control', d))
        self.control_network.register_callback('disconnected', lambda d: self._on_socket_disconnected('control', d))
        self.control_network.register_callback('switch_back', self.on_switch_back)
        self.control_network.register_callback('switch_ack', self.on_switch_ack)
        self.control_network.register_callback('topology_ack', self.on_topology_ack)
        self.control_network.register_callback(
            'topology_commit_ack',
            self.on_topology_commit_ack,
        )
        self.control_network.register_callback(
            'topology_rollback_ack',
            self.on_topology_rollback_ack,
        )
        self.control_network.register_callback(
            'clipboard_offer', self.on_remote_clipboard_offer
        )
        self.control_network.register_callback('file_manifest_request', self.on_file_manifest_request)
        self.control_network.register_callback('file_manifest_response', self.on_file_manifest_response)
        self.control_network.register_callback('file_manifest_failed', self.on_file_manifest_failed)
        self.control_network.register_callback('file_manifest_ack', self.on_file_manifest_ack)
        self.control_network.register_callback(
            'reload_connection_request',
            lambda data: self._reload_connection(),
        )
        
        # Setup data network callbacks
        self.data_network.register_callback('connected', lambda d: self._on_socket_connected('data', d))
        self.data_network.register_callback('disconnected', lambda d: self._on_socket_disconnected('data', d))
        self.data_network.register_callback('clipboard_sync', self.on_remote_copy)
        self.file_network.register_callback(
            'connected', lambda metadata, payload: self._on_socket_connected('file', metadata)
        )
        self.file_network.register_callback(
            'disconnected', lambda metadata, payload: self._on_socket_disconnected('file', metadata)
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
        self.clipboard_hub.register_endpoint(
            self.server_machine_id,
            self._deliver_clipboard_to_server,
        )
        self.paste_coordinator = PasteCoordinator(self._request_remote_file_paste)
        self.paste_coordinator.before_paste = (
            self._refresh_active_destination_offer
        )
        self.hotkey_monitor = WindowsPasteHotkeyMonitor(self.paste_coordinator)
        self.clipboard_offer_state = ClipboardOfferState("server")
        self.file_paste_service = FilePasteService(
            _ServerClusterControl(self),
            self.file_receiver,
            self.file_publisher,
            TransferSender(
                self.cluster_file_lane,
                controller=self.transfer_controller,
            ),
            lambda: snapshot_selection(self.clipboard.read_file_selection()),
        )
        self.clipboard_sender = LatestWinsSender(self._send_clipboard_snapshot)
        self.pressed_keys = set()

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
        self._abort_topology_transaction(shutdown=True)
        self.global_hotkey_monitor.stop()
        self.cluster_file_router.stop()
        self.session_registry.close()
        self.control_network.stop()
        self.data_network.stop()
        self.file_network.stop()
        self.input_handler.stop()
        self.clipboard.stop()
        self.clipboard_sender.stop()
        self.clipboard_hub.stop()
        self.hotkey_monitor.stop()

    def _on_socket_connected(self, sock_type, data=None):
        session_id = (data or {}).get('session_id')
        if session_id is None:
            return False
        session = self.session_registry.get(session_id)
        if session is None:
            return False
        if sock_type == 'data' and session.data_lane is not None and session.file_lane is None:
            self._offer_file_lane(session_id)
        return self._refresh_session_readiness(session_id)

    def _refresh_session_readiness(self, session_id):
        session = self.session_registry.get(session_id)
        became_ready = False
        with self._client_state_lock:
            self.control_connected = bool(self.session_registry.active_sessions())
            self.data_connected = any(
                item.data_lane is not None
                for item in self.session_registry.active_sessions()
            )
            if (
                session is not None
                and session.ready
                and session_id not in self._ready_session_ids
            ):
                self._ready_session_ids.add(session_id)
                became_ready = True
            self._client_ready = bool(self.session_registry.ready_sessions())
        if became_ready:
            self.on_client_connected(session_id)
        return became_ready

    def _on_socket_disconnected(self, sock_type, data=None):
        session_id = (data or {}).get('session_id')
        if session_id is None:
            return False
        with self._client_state_lock:
            if session_id in self._disconnecting_sessions:
                return False
            session = self.session_registry.get(session_id)
            if session is None:
                return False
            self._disconnecting_sessions.add(session_id)
            was_ready = session.ready or session_id in self._ready_session_ids
        try:
            self.file_network.revoke_session(session_id)
            self.session_registry.close(session_id)
            with self._client_state_lock:
                self._ready_session_ids.discard(session_id)
            if was_ready:
                self.on_client_disconnected(session_id)
            self._refresh_session_readiness(session_id)
            return True
        finally:
            with self._client_state_lock:
                self._disconnecting_sessions.discard(session_id)

    def on_client_connected(self, session_id=None):
        logger.info(
            "Client session %s connected on all lanes; waiting for topology Apply before input routing.",
            str(session_id)[:8] if session_id else "legacy",
        )
        message = {'type': 'display_inventory_request'}
        if session_id is None:
            self.control_network.send_message(message)
        else:
            self.control_network.send_message(message, session_id=session_id)
        if len(getattr(self, '_ready_session_ids', ())) <= 1:
            self.clipboard.start()
            self.hotkey_monitor.start()
        self._register_clipboard_endpoint(session_id)
        self.pressed_keys.clear()

    def activate_client_topology(self, topology):
        if not hasattr(topology, 'version') or not hasattr(topology, 'edge_mappings'):
            raise TypeError("active topology is required")
        self._install_topology(topology)
        message = self._topology_layout_message(topology, 'layout_config')
        if message is not None:
            self.control_network.send_message(message)

    def _install_topology(self, topology):
        previous_router = getattr(self, 'input_router', None)
        if previous_router is not None:
            previous_router.pause("topology changed")
        self.active_topology = topology
        self.input_handler.configure_topology_edges(topology, topology.server_id)
        self.input_router = InputRouter(
            topology,
            session_for_machine=self._session_for_machine,
            input_effects=_ServerInputEffects(self),
            handoff_failed=self._on_handoff_failed,
            ownership_changed=self._on_cursor_ownership_changed,
        )
        if getattr(self, 'routing_suspended', False):
            self.input_router.pause("topology reset required")
            self.input_handler.stop()
        elif getattr(self, 'control_connected', True):
            self.input_handler.start_edge_detection()

    def _session_for_machine(self, machine_id):
        authorized_session_ids = getattr(
            self,
            "_active_topology_session_ids",
            None,
        )
        return next(
            (
                session
                for session in self.session_registry.active_sessions()
                if session.ready
                and session.peer_identity == machine_id
                and (
                    authorized_session_ids is None
                    or session.session_id in authorized_session_ids
                )
            ),
            None,
        )

    def _restore_topology(self, topology):
        if topology is not None:
            self._install_topology(topology)
            return
        self.active_topology = None
        self.input_router = None
        self.input_handler.stop()
        self.input_handler.clear_topology_edges()

    def _topology_layout_message(self, topology, message_type, machine_id=None):
        if machine_id is None:
            mappings = tuple(
                mapping
                for mapping in topology.edge_mappings
                if mapping.source_machine_id == topology.server_id
                and mapping.destination_machine_id != topology.server_id
            )
        else:
            mappings = tuple(
                mapping
                for mapping in topology.edge_mappings
                if mapping.destination_machine_id == machine_id
            )
        if not mappings:
            return None
        machines = {
            placed.group.machine_id: placed.group
            for placed in topology.machines
        }
        mapping = mappings[0]
        source_display = machines[mapping.source_machine_id].display(
            mapping.source_display_id
        )
        client_display = machines[mapping.destination_machine_id].display(
            mapping.destination_display_id
        )
        return {
            'type': message_type,
            'topology_version': topology.version,
            'position': mapping.source_side,
            'server_width': self.input_handler.screen_width,
            'server_height': self.input_handler.screen_height,
            'server_display_id': mapping.source_display_id,
            'server_rect': [
                source_display.rect.left,
                source_display.rect.top,
                source_display.rect.right,
                source_display.rect.bottom,
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
        with self._topology_ack_lock:
            if getattr(self, "_topology_transaction", None) is not None:
                return False
        previous = getattr(self, 'active_topology', None)
        suspended_before = getattr(self, 'routing_suspended', False)
        previous_session_ids = set(
            getattr(self, "_active_topology_session_ids", ())
        )
        clipboard_hub = getattr(self, 'clipboard_hub', None)
        self._release_forwarded_keys()
        release = (
            getattr(self.input_handler, 'release_all_injected_input', None)
            or self.input_handler.release_all_injected_keys
        )
        release()
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
        if clipboard_hub is not None:
            clipboard_hub.pause_delivery()
        file_router = getattr(self, "cluster_file_router", None)
        if file_router is not None:
            file_router.pause()
        candidate_machine_ids = {
            placed.group.machine_id for placed in topology.machines
        }
        candidate_client_ids = candidate_machine_ids - {topology.server_id}
        registry = getattr(self, "session_registry", None)
        sessions = tuple(
            () if registry is None else registry.ready_sessions()
        )
        participants = {
            session.session_id: session.peer_identity for session in sessions
        }
        legacy_message = None
        if registry is None:
            legacy_message = self._topology_layout_message(
                topology, 'topology_apply'
            )
            if legacy_message is not None:
                participants = {"legacy": "legacy"}
        condition = threading.Condition(self._topology_ack_lock)
        transaction = {
            "version": topology.version,
            "participants": frozenset(participants),
            "prepare": set(),
            "commit": set(),
            "rollback": set(),
            "disconnected": set(),
            "failed": (
                registry is not None
                and {session.peer_identity for session in sessions}
                != candidate_client_ids
            ),
            "shutdown": False,
            "condition": condition,
        }
        with self._topology_ack_lock:
            self._topology_transaction = transaction

        sent_participants = set()
        for session_id, machine_id in participants.items():
            message = (
                legacy_message
                if session_id == "legacy"
                else self._topology_layout_message(
                    topology,
                    'topology_apply',
                    machine_id,
                )
            )
            if message is None:
                transaction["failed"] = True
                continue
            message = dict(message)
            message['version'] = topology.version
            sent = (
                self.control_network.send_message(message)
                if session_id == "legacy"
                else self.control_network.send_message(
                    message,
                    session_id=session_id,
                )
            )
            if sent:
                sent_participants.add(session_id)
            else:
                transaction["failed"] = True

        def finish():
            expected = transaction["participants"]
            with condition:
                prepared = condition.wait_for(
                    lambda: transaction["failed"]
                    or transaction["prepare"] == expected,
                    timeout,
                ) and not transaction["failed"]
            committed = bool(prepared)
            if committed:
                for session_id in expected:
                    message = {'type': 'topology_commit', 'version': topology.version}
                    sent = (
                        self.control_network.send_message(message)
                        if session_id == "legacy"
                        else self.control_network.send_message(
                            message, session_id=session_id
                        )
                    )
                    if not sent:
                        committed = False
                if committed:
                    with condition:
                        committed = condition.wait_for(
                            lambda: transaction["failed"]
                            or transaction["commit"] == expected,
                            timeout,
                        ) and not transaction["failed"]
            with condition:
                if transaction["failed"]:
                    committed = False
            persisted = False
            candidate_persisted = False
            if committed:
                try:
                    persisted = bool(on_persist(topology))
                    candidate_persisted = persisted
                except Exception as error:
                    logger.error(
                        "Could not persist acknowledged topology (%s)",
                        error_name(error),
                    )
                with condition:
                    if registry is not None:
                        ready_session_ids = {
                            session.session_id
                            for session in registry.ready_sessions()
                        }
                        if ready_session_ids != expected:
                            transaction["failed"] = True
                    if transaction["failed"]:
                        persisted = False
                if persisted:
                    try:
                        self._active_topology_session_ids = {
                            session_id
                            for session_id in expected
                            if session_id != "legacy"
                        }
                        self.routing_suspended = False
                        self._install_topology(topology)
                    except Exception as error:
                        logger.error(
                            "Could not install acknowledged topology (%s)",
                            error_name(error),
                        )
                        persisted = False
                        self.routing_suspended = suspended_before
                        self._active_topology_session_ids = previous_session_ids
            if not persisted and candidate_persisted:
                try:
                    if not bool(on_persist(previous)):
                        logger.error(
                            "Could not restore previous persisted topology"
                        )
                except Exception as error:
                    logger.error(
                        "Could not restore previous persisted topology (%s)",
                        error_name(error),
                    )
            shutdown = transaction["shutdown"]
            if not persisted and not shutdown:
                for session_id in sent_participants:
                    message = {'type': 'topology_rollback', 'version': topology.version}
                    if session_id == "legacy":
                        self.control_network.send_message(message)
                    else:
                        self.control_network.send_message(
                            message, session_id=session_id
                        )
                with condition:
                    condition.wait_for(
                        lambda: transaction["rollback"] >= sent_participants,
                        timeout,
                    )
                    inconsistent = sent_participants - transaction["rollback"]
                for session_id in inconsistent:
                    if session_id != "legacy":
                        self.control_network.disconnect(session_id=session_id)
                self._active_topology_session_ids = (
                    previous_session_ids
                    - inconsistent
                    - transaction["disconnected"]
                )
                self._restore_topology(previous)
            elif persisted:
                for session_id in expected:
                    message = {'type': 'topology_finalize', 'version': topology.version}
                    if session_id == "legacy":
                        self.control_network.send_message(message)
                    else:
                        self.control_network.send_message(
                            message, session_id=session_id
                        )
            if not shutdown:
                if file_router is not None:
                    file_router.resume()
                if clipboard_hub is not None:
                    clipboard_hub.resume_delivery()
            with self._topology_ack_lock:
                if self._topology_transaction is transaction:
                    self._topology_transaction = None
            on_complete(persisted)

        if participants:
            threading.Thread(target=finish, daemon=True).start()
        else:
            finish()
        return True

    def on_topology_ack(self, data):
        return self._record_topology_ack("prepare", data)

    def on_topology_commit_ack(self, data):
        return self._record_topology_ack("commit", data)

    def on_topology_rollback_ack(self, data):
        return self._record_topology_ack("rollback", data)

    def _record_topology_ack(self, phase, data):
        with self._topology_ack_lock:
            transaction = getattr(self, "_topology_transaction", None)
            if transaction is None or data.get("version") != transaction["version"]:
                return False
            session_id = data.get("session_id") or "legacy"
            if session_id not in transaction["participants"]:
                return False
            transaction[phase].add(session_id)
            transaction["condition"].notify_all()
            return True

    def _abort_topology_transaction(self, session_id=None, shutdown=False):
        lock = getattr(self, "_topology_ack_lock", None)
        if lock is None:
            return False
        with lock:
            transaction = getattr(self, "_topology_transaction", None)
            if transaction is None:
                return False
            if (
                session_id is not None
                and session_id not in transaction["participants"]
            ):
                return False
            if session_id is not None:
                transaction["disconnected"].add(session_id)
            transaction["failed"] = True
            transaction["shutdown"] = bool(
                transaction["shutdown"] or shutdown
            )
            transaction["condition"].notify_all()
            return True

    def _offer_file_lane(self, session_id):
        session = self.session_registry.get(session_id)
        if session is None or session.data_lane is None:
            raise RuntimeError("file lane cannot be offered before session binding")
        self.file_network.offer_session(None, session_id)
        return self.control_network.send_message(
            {
                'type': 'file_lane_offer',
                'port': self.file_network.port,
                'session_id': session_id,
            },
            session_id=session_id,
        )

    def on_client_disconnected(self, session_id=None):
        logger.info("Client session %s disconnected.", str(session_id)[:8] if session_id else "legacy")
        self._abort_topology_transaction(session_id=session_id)
        if session_id is not None:
            getattr(self, "_active_topology_session_ids", set()).discard(
                session_id
            )
        endpoint_ids = getattr(self, '_clipboard_endpoint_ids', {})
        endpoint_id = endpoint_ids.pop(session_id, None)
        sessions_by_endpoint = getattr(
            self,
            '_clipboard_sessions_by_endpoint',
            {},
        )
        current_session_id = sessions_by_endpoint.get(endpoint_id)
        cluster_file_router = getattr(self, 'cluster_file_router', None)
        if cluster_file_router is not None and endpoint_id is not None:
            cluster_file_router.endpoint_disconnected(endpoint_id)
        clipboard_hub = getattr(self, 'clipboard_hub', None)
        if current_session_id == session_id:
            sessions_by_endpoint.pop(endpoint_id, None)
        if (
            clipboard_hub is not None
            and endpoint_id is not None
            and current_session_id == session_id
        ):
            clipboard_hub.disconnect_endpoint(endpoint_id)
        self.suspend_input_routing("client disconnected")
        if session_id is not None and self.session_registry.ready_sessions():
            return
        logger.info("No ready Clients remain; stopping edge detection and wiping clipboard.")
        self.pressed_keys.clear()
        if self.on_capture_stop:
            self.on_capture_stop()
        self.input_handler.stop()
        self.clipboard.stop()
        if session_id is None:
            self.file_network.close()
        self.paste_coordinator.reset()
        self.hotkey_monitor.stop()

    def suspend_input_routing(self, reason="topology reset required"):
        """Idempotently stop cluster input while leaving other ready lanes alive."""
        was_suspended = getattr(self, 'routing_suspended', False)
        self.routing_suspended = True
        router = getattr(self, 'input_router', None)
        if router is not None:
            request_pause = getattr(router, "request_pause", None)
            if request_pause is not None:
                request_pause(reason)
        self.input_handler.stop()
        if router is not None:
            _display_id, center = router.topology.server_primary_center()
        else:
            center = (
                getattr(self.input_handler, "screen_width", 1920) // 2,
                getattr(self.input_handler, "screen_height", 1080) // 2,
            )
        _ServerInputEffects(self).restore_paused(center)

        def notify_survivors():
            registry = getattr(self, 'session_registry', None)
            sessions = () if registry is None else tuple(registry.ready_sessions())
            network = getattr(self, 'control_network', None)
            if network is None:
                return
            message = {'type': 'topology_suspend', 'reason': 'client_disconnected'}
            if registry is None:
                network.send_message(message)
            else:
                for session in sessions:
                    network.send_message(message, session_id=session.session_id)

        notify_survivors()
        if router is not None:
            def finish_router_pause():
                try:
                    router.pause(reason)
                except Exception as exc:
                    logger.error(
                        "[cursor] Failed to finish router suspension (%s)",
                        type(exc).__name__,
                    )
                finally:
                    if getattr(self, "routing_suspended", False):
                        self.input_handler.stop()
                        notify_survivors()

            threading.Thread(
                target=finish_router_pause,
                name="input-routing-suspend",
                daemon=True,
            ).start()
        self.pressed_keys.clear()
        return not was_suspended

    def _on_handoff_failed(self, session_id, reason):
        logger.warning(
            "[cursor] Handoff failed for session=%s reason=%s; suspending routing",
            str(session_id)[:8],
            reason,
        )
        self.suspend_input_routing("cursor handoff failed")

        def close_failed_session():
            try:
                self.control_network.disconnect(session_id=session_id)
            except Exception as exc:
                logger.error(
                    "[cursor] Failed to close handoff session (%s)",
                    type(exc).__name__,
                )

        threading.Thread(
            target=close_failed_session,
            name=f"cursor-handoff-close-{str(session_id)[:8]}",
            daemon=True,
        ).start()

    def _on_cursor_ownership_changed(self, _state):
        self._apply_clipboard_offer_route()

    def on_edge_hit(self, direction, ratio, region=None):
        if getattr(self, "routing_suspended", False):
            return False
        with self._get_paste_route_lock():
            if getattr(self, "routing_suspended", False):
                return False
            paste_service = getattr(self, "file_paste_service", None)
            if (
                paste_service is not None
                and paste_service.destination_paste_active
            ):
                logger.info(
                    "Ignoring screen edge while the local paste destination is active."
                )
                return False
            router = getattr(self, 'input_router', None)
            if router is None or region is None:
                return False
            cancel_edit = getattr(self, 'on_topology_edit_cancel', None)
            if cancel_edit is not None:
                cancel_edit()
            switched = router.handle_edge(
                region.source_machine_id,
                region.source_display_id,
                region.source_side,
                ratio,
                topology_version=router.topology.version,
            )
            return switched

    def on_switch_ack(self, data):
        if getattr(self, "routing_suspended", False):
            return False
        router = getattr(self, 'input_router', None)
        if router is None:
            return False
        return router.acknowledge_handoff(
            handoff_id=data.get('handoff_id'),
            session_id=data.get('session_id'),
            machine_id=data.get('peer_identity'),
            topology_version=data.get('topology_version'),
        )

    def on_switch_back(self, data):
        if getattr(self, "routing_suspended", False):
            return False
        with self._get_paste_route_lock():
            return self._on_switch_back_locked(data)

    def _on_switch_back_locked(self, data):
        if getattr(self, "routing_suspended", False):
            return False
        router = getattr(self, 'input_router', None)
        if router is None:
            return False
        logger.info(
            "[cursor] Switch-back received machine=%r session=%s "
            "display=%r side=%r ratio=%r topology=%r",
            data.get('peer_identity'),
            str(data.get('session_id'))[:8],
            data.get('source_display_id'),
            data.get('source_side'),
            data.get('ratio'),
            data.get('topology_version'),
        )
        switched = router.handle_edge(
            data.get('peer_identity'),
            data.get('source_display_id'),
            data.get('source_side'),
            data.get('ratio', 0.5),
            session_id=data.get('session_id'),
            topology_version=data.get('topology_version'),
        )
        router_state = getattr(router, "state", None)
        if switched and (
            router_state is None or isinstance(router_state, LocalServer)
        ):
            self._apply_clipboard_offer_route()
            logger.info("[cursor] Switch-back accepted; Server owns the cursor")
        elif switched:
            logger.info("[cursor] Client-to-Client handoff is awaiting acknowledgement")
        else:
            logger.warning("[cursor] Switch-back rejected; router state unchanged")
        return switched

    def on_mouse_move(self, dx, dy):
        if getattr(self, "routing_suspended", False):
            return False
        router = getattr(self, 'input_router', None)
        if router is not None:
            return router.forward_mouse_move(dx, dy)
        self.control_network.send_message({
            'type': 'mouse_move',
            'dx': dx,
            'dy': dy
        })

    def on_mouse_click(self, button, pressed):
        if getattr(self, "routing_suspended", False):
            return False
        router = getattr(self, 'input_router', None)
        if router is not None:
            return router.forward_button(button, pressed)
        self.control_network.send_message({
            'type': 'mouse_click',
            'button': button,
            'pressed': pressed
        })

    def on_mouse_scroll(self, dx, dy):
        if getattr(self, "routing_suspended", False):
            return False
        router = getattr(self, 'input_router', None)
        if router is not None:
            return router.forward_scroll(dx, dy)
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

        if getattr(self, "routing_suspended", False):
            return False
        router = getattr(self, 'input_router', None)
        if router is not None:
            return router.forward_key_press(key_data)
        self.control_network.send_message({
            'type': 'key_press',
            'key': key_data
        })

    def on_key_release(self, key_data):
        val = key_data.get('value')
        if val and self.paste_coordinator.on_key_release(val):
            self.pressed_keys.discard(val)
            return
        if val in self.pressed_keys:
            self.pressed_keys.discard(val)
        if getattr(self, "routing_suspended", False):
            return False
        router = getattr(self, 'input_router', None)
        if router is not None:
            return router.forward_key_release(key_data)
        self.control_network.send_message({
            'type': 'key_release',
            'key': key_data
        })

    def _release_forwarded_keys(self):
        router = getattr(self, 'input_router', None)
        if router is not None:
            router.pause("input release")
            self.pressed_keys.clear()
            return
        payloads = [
            {'type': 'special', 'value': key}
            for key in sorted(self.pressed_keys - {'esc', 'escape'})
        ]
        for key_data in payloads:
            self.control_network.send_message({
                'type': 'key_release',
                'key': key_data,
            })
        self.pressed_keys.clear()

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

    def _return_cursor_to_server(self):
        router = getattr(self, "input_router", None)
        if router is not None:
            return bool(router.return_to_server_primary("shortcut"))

        input_handler = self.input_handler
        release = getattr(input_handler, "release_all_injected_input", None)
        if release is not None:
            release()
        effects = _ServerInputEffects(self)
        effects.release_local_input()
        center = (
            getattr(input_handler, "screen_width", 1920) // 2,
            getattr(input_handler, "screen_height", 1080) // 2,
        )
        effects.restore_paused(center)
        logger.info("[cursor] Returned to Server primary (shortcut fallback)")
        return True

    def prepare_app_shutdown(self):
        self._release_forwarded_keys()

    def _emergency_exit_locked(self):
        router = getattr(self, 'input_router', None)
        mouse_loc = (
            "REMOTE CLIENT SCREEN"
            if router is not None and router.active_session_id is not None
            else "LOCAL HOST SCREEN"
        )
        logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+Escape triggered on Server! Cursor location: %s. Forcefully disconnecting client and returning control.", mouse_loc)
        self._release_forwarded_keys()
        self.pressed_keys.clear()
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
        if getattr(self, 'session_registry', None):
            try:
                self.session_registry.close()
            except Exception:
                pass
        if getattr(self, 'file_network', None):
            try:
                self.file_network.revoke_session()
            except Exception:
                pass

    def _reload_connection(self):
        router = getattr(self, 'input_router', None)
        mouse_loc = (
            "REMOTE CLIENT SCREEN"
            if router is not None and router.active_session_id is not None
            else "LOCAL HOST SCREEN"
        )
        logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+R triggered on Server! Cursor location: %s. Soft-resetting active connection and restoring local control.", mouse_loc)
        return self.broadcast_cluster_command(
            "reload_connection",
            local_cleanup=lambda _command: self._on_emergency_exit(),
        )

    def broadcast_cluster_command(
        self,
        command_type,
        payload=None,
        local_cleanup=None,
    ):
        registry = getattr(self, "session_registry", None)
        if registry is None:
            self.prepare_app_shutdown()
            message = dict(payload or {})
            message["type"] = command_type
            sent = self.control_network.send_message(message)
            if local_cleanup is not None:
                local_cleanup(message)
            return sent
        def finish(command):
            if local_cleanup is not None:
                local_cleanup(command)
            elif command_type == "set_daemon_mode":
                router = getattr(self, "input_router", None)
                if router is not None and not getattr(
                    self, "routing_suspended", False
                ):
                    router.resume()

        broadcaster = ClusterCommandBroadcaster(
            ready_sessions=registry.ready_sessions,
            send=lambda session_id, message: self.control_network.send_message(
                message,
                session_id=session_id,
            ),
            release_input=self.prepare_app_shutdown,
            local_cleanup=finish,
        )
        return broadcaster.broadcast(command_type, payload)

    def on_local_copy(self, snapshot):
        work = {"snapshot": snapshot}
        pending_sequence = getattr(
            self,
            "_pending_local_ordinary_sequence",
            None,
        )
        if pending_sequence is not None:
            work["source_sequence"] = pending_sequence
            self._pending_local_ordinary_sequence = None
        state = getattr(self, "clipboard_offer_state", None)
        if (
            not hasattr(self, "session_registry")
            and state is not None
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
        offer = work.get("offer")
        source_sequence = work.get("source_sequence")
        if source_sequence is None and offer is not None:
            source_sequence = offer.sequence
        if source_sequence is None:
            source_sequence = self._next_local_clipboard_sequence()
        item = self._get_clipboard_hub().accept_ordinary(
            self.server_machine_id,
            source_sequence,
            snapshot,
        )
        sent = item is not None
        if item is not None:
            state = self._get_clipboard_offer_state()
            state.accept_cluster(
                item.revision,
                "server",
                item.kind,
                item.source_sequence,
                session_id="cluster",
            )
            self._apply_clipboard_offer_route()
        logger.info(
            "Clipboard snapshot accepted (role=server formats=%s bytes=%d accepted=%s)",
            ",".join(entry.kind for entry in snapshot.entries),
            sum(len(entry.data) for entry in snapshot.entries),
            sent,
        )
        return sent

    def on_remote_copy(self, data):
        if not isinstance(data, dict):
            return False
        session_id = data.get("session_id")
        peer_identity = data.get("peer_identity")
        session = self.session_registry.get(session_id)
        if (
            session is None
            or not session.ready
            or session.peer_identity != peer_identity
        ):
            logger.info("Unbound clipboard snapshot discarded (role=server)")
            return False
        payload = self._clipboard_wire_payload(data)
        try:
            snapshot = decode_clipboard_message(payload)
        except Exception as error:
            logger.warning(
                "Remote clipboard snapshot rejected (%s)",
                error_name(error),
            )
            return False
        source_sequence = data.get("source_sequence")
        if type(source_sequence) is not int:
            offer = data.get("offer")
            source_sequence = (
                offer.get("sequence") if isinstance(offer, dict) else None
            )
        item = self._get_clipboard_hub().accept_ordinary(
            peer_identity,
            source_sequence,
            snapshot,
            source_domain=session_id,
        )
        if item is None:
            logger.info("Stale clipboard snapshot discarded (role=server)")
            return False
        logger.info(
            "Clipboard snapshot received (role=server source=%s revision=%d)",
            peer_identity,
            item.revision,
        )
        return True

    @staticmethod
    def _clipboard_wire_payload(data):
        return {
            key: value
            for key, value in data.items()
            if key in {"type", "version", "formats"}
        }

    def _get_clipboard_hub(self):
        hub = getattr(self, "clipboard_hub", None)
        if hub is not None:
            return hub
        self.server_machine_id = getattr(
            self,
            "server_machine_id",
            "server",
        )
        hub = ClipboardHub(self.server_machine_id)
        self.clipboard_hub = hub
        hub.register_endpoint(
            self.server_machine_id,
            self._deliver_clipboard_to_server,
        )
        return hub

    def _next_local_clipboard_sequence(self):
        sequence = getattr(self, "_local_clipboard_sequence", 0) + 1
        self._local_clipboard_sequence = sequence
        return sequence

    def _register_clipboard_endpoint(self, session_id):
        registry = getattr(self, "session_registry", None)
        session = None if registry is None else registry.get(session_id)
        if session is None or not session.ready:
            return False
        endpoint_ids = getattr(self, "_clipboard_endpoint_ids", None)
        if endpoint_ids is None:
            endpoint_ids = {}
            self._clipboard_endpoint_ids = endpoint_ids
        endpoint_ids[session_id] = session.peer_identity
        sessions_by_endpoint = getattr(
            self,
            "_clipboard_sessions_by_endpoint",
            None,
        )
        if sessions_by_endpoint is None:
            sessions_by_endpoint = {}
            self._clipboard_sessions_by_endpoint = sessions_by_endpoint
        sessions_by_endpoint[session.peer_identity] = session_id
        return self._get_clipboard_hub().register_endpoint(
            session.peer_identity,
            lambda item: self._deliver_clipboard_to_client(session_id, item),
            source_domain=session_id,
        )

    def _deliver_clipboard_to_server(self, item):
        state = self._get_clipboard_offer_state()
        state.accept_cluster(
            item.revision,
            "client",
            item.kind,
            item.source_sequence,
            session_id="cluster",
        )
        self._apply_clipboard_offer_route()
        if item.kind != "ordinary" or item.snapshot is None:
            return True
        payload = encode_clipboard_message(item.snapshot)
        return self.clipboard.inject(payload)

    def _deliver_clipboard_to_client(self, session_id, item):
        offer = {
            "type": "clipboard_offer",
            "session_id": session_id,
            "revision": item.revision,
            "source": "server",
            "kind": item.kind,
            "sequence": item.source_sequence,
            "cluster_revision": item.revision,
            "source_id": item.source_id,
        }
        if item.kind != "ordinary" or item.snapshot is None:
            return self.control_network.send_message(
                offer,
                session_id=session_id,
            )
        payload = encode_clipboard_message(item.snapshot)
        payload.update({
            "cluster_revision": item.revision,
            "source_id": item.source_id,
            "source_sequence": item.source_sequence,
            "offer": offer,
        })
        return self.data_network.send_message(payload, session_id=session_id)

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
        if getattr(self, "clipboard_hub", None) is not None:
            return "cluster"
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
        coordinator = getattr(self, "paste_coordinator", None)
        if coordinator is None:
            return False
        remote_destination = self._remote_destination_active()
        destination = "client" if remote_destination else "server"
        transfer_required = self._cluster_file_transfer_decision(
            state.current_offer,
            remote_destination=remote_destination,
        )
        if transfer_required is None:
            return coordinator.set_route(
                state.current_offer,
                destination,
            )
        return coordinator.set_route(
            state.current_offer,
            destination,
            transfer_required=transfer_required,
        )

    def _cluster_file_transfer_decision(self, offer, *, remote_destination):
        hub = getattr(self, "clipboard_hub", None)
        if hub is None or getattr(offer, "session_id", None) != "cluster":
            return None
        item = hub.latest_item
        router = getattr(self, "input_router", None)
        server_id = getattr(self, "server_machine_id", None)
        destination_id = (
            getattr(router, "active_machine_id", None)
            if remote_destination
            else server_id
        )
        route_matches = (
            item is not None
            and offer is not None
            and item.revision == offer.revision
            and item.kind == offer.kind
            and isinstance(item.source_id, str)
            and bool(item.source_id)
            and isinstance(destination_id, str)
            and bool(destination_id)
        )
        if route_matches and remote_destination:
            session = self._session_for_machine(destination_id)
            route_matches = (
                session is not None
                and session.session_id
                == getattr(router, "active_session_id", None)
            )
        if not route_matches:
            logger.info(
                "Cluster file paste route unavailable "
                "(offer_revision=%s hub_revision=%s remote=%s)",
                getattr(offer, "revision", None),
                getattr(item, "revision", None),
                remote_destination,
            )
            return False
        required = (
            item.kind == "files" and item.source_id != destination_id
        )
        logger.info(
            "Cluster file paste route selected "
            "(source=%s destination=%s transfer=%s)",
            item.source_id[-8:],
            destination_id[-8:],
            required,
        )
        return required

    def _refresh_active_destination_offer(self):
        if self._remote_destination_active():
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
        self._local_clipboard_sequence = max(
            sequence,
            getattr(self, "_local_clipboard_sequence", 0),
        )
        if not hasattr(self, "session_registry"):
            state = self._get_clipboard_offer_state()
            if state.session_id is None:
                return False
            offer = state.observe_local(kind, sequence)
            self._apply_clipboard_offer_route()
            return self.control_network.send_message(offer.to_message())
        if kind == "files":
            item = self._get_clipboard_hub().accept_offer(
                self.server_machine_id,
                sequence,
                kind,
            )
            if item is not None:
                state = self._get_clipboard_offer_state()
                state.accept_cluster(
                    item.revision,
                    "server",
                    kind,
                    sequence,
                    session_id="cluster",
                )
                self._apply_clipboard_offer_route()
            return item is not None
        else:
            self._pending_local_ordinary_sequence = sequence
            return True

    def on_remote_clipboard_offer(self, data):
        if isinstance(data, dict) and data.get("kind") == "files":
            session_id = data.get("session_id")
            peer_identity = data.get("peer_identity")
            registry = getattr(self, "session_registry", None)
            if registry is None:
                state = self._get_clipboard_offer_state()
                accepted = state.accept_remote(data)
                if accepted:
                    self._apply_clipboard_offer_route()
                return accepted
            session = registry.get(session_id)
            if (
                session is None
                or not session.ready
                or session.peer_identity != peer_identity
            ):
                return False
            item = self._get_clipboard_hub().accept_offer(
                peer_identity,
                data.get("sequence"),
                "files",
                source_domain=session_id,
            )
            return item is not None
        state = self._get_clipboard_offer_state()
        accepted = state.accept_remote(data)
        if accepted:
            self._apply_clipboard_offer_route()
        return accepted

    def _request_remote_file_paste(self):
        with self._get_paste_route_lock():
            router = getattr(self, 'input_router', None)
            active_session_id = (
                None if router is None else router.active_session_id
            )
            if active_session_id is not None:
                return self.control_network.send_message(
                    {'type': 'file_paste_intent'},
                    session_id=active_session_id,
                )
            return self.file_paste_service.request_paste()

    def _remote_destination_active(self):
        router = getattr(self, 'input_router', None)
        return router is not None and router.active_session_id is not None

    def on_file_manifest_request(self, data):
        router = getattr(self, "cluster_file_router", None)
        if router is None:
            return self.file_paste_service.on_manifest_request(data)
        return self._handle_cluster_file_control(
            data.get("peer_identity"),
            data,
        )

    def on_file_manifest_response(self, data):
        router = getattr(self, "cluster_file_router", None)
        if router is None:
            return self.file_paste_service.on_manifest_response(data)
        return self._handle_cluster_file_control(
            data.get("peer_identity"),
            data,
        )

    def on_file_manifest_failed(self, data):
        router = getattr(self, "cluster_file_router", None)
        if router is None:
            return self.file_paste_service.on_manifest_failed(data)
        return self._handle_cluster_file_control(
            data.get("peer_identity"),
            data,
        )

    def on_file_manifest_ack(self, data):
        router = getattr(self, "cluster_file_router", None)
        if router is None:
            return self.file_paste_service.on_manifest_ack(data)
        return self._handle_cluster_file_control(
            data.get("peer_identity"),
            data,
        )

    def _cluster_endpoint_available(self, endpoint_id):
        if endpoint_id == self.server_machine_id:
            return True
        return self._session_for_machine(endpoint_id) is not None

    def _send_cluster_file_control(self, endpoint_id, message):
        if endpoint_id == self.server_machine_id:
            return self._dispatch_local_file_control(message)
        session = self._session_for_machine(endpoint_id)
        if session is None:
            return False
        return self.control_network.send_message(
            message,
            session_id=session.session_id,
        )

    def _send_cluster_file_frame(self, endpoint_id, metadata, payload=b""):
        if endpoint_id == self.server_machine_id:
            return self.cluster_file_lane.deliver_local(metadata, payload)
        session = self._session_for_machine(endpoint_id)
        if session is None:
            return False
        try:
            return self.file_network.send(
                metadata,
                payload,
                session_id=session.session_id,
            )
        except (ConnectionError, OSError):
            return False

    def _dispatch_local_file_control(self, message):
        handlers = {
            "file_manifest_request": self.file_paste_service.on_manifest_request,
            "file_manifest_response": self.file_paste_service.on_manifest_response,
            "file_manifest_failed": self.file_paste_service.on_manifest_failed,
            "file_manifest_ack": self.file_paste_service.on_manifest_ack,
        }
        handler = handlers.get(message.get("type"))
        return False if handler is None else handler(message)

    def _handle_cluster_file_control(self, origin_id, message):
        if not isinstance(message, dict) or not isinstance(origin_id, str):
            return False
        if origin_id != self.server_machine_id:
            session_id = message.get("session_id")
            session = self.session_registry.get(session_id)
            if (
                session is None
                or not session.ready
                or session.peer_identity != origin_id
            ):
                return False
        message_type = message.get("type")
        if message_type == "file_manifest_request":
            job = self.cluster_file_router.request_paste(
                origin_id,
                message.get("request_id"),
            )
            if job is not None:
                return True
            return self._send_cluster_file_control(origin_id, {
                "type": "file_manifest_failed",
                "request_id": message.get("request_id"),
                "error": "FileRouteUnavailable",
            })
        if message_type == "file_manifest_response":
            return self.cluster_file_router.on_manifest_response(origin_id, message)
        if message_type == "file_manifest_failed":
            return self.cluster_file_router.on_manifest_failed(origin_id, message)
        if message_type == "file_manifest_ack":
            return self.cluster_file_router.on_manifest_ack(origin_id, message)
        return False
