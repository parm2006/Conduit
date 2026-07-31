"""Deadline-bound TLS control and clipboard lanes."""

import hashlib
import json
import logging
import os
import select
import socket
import ssl
import struct
import threading
import time
from enum import Enum

from app.crypto import load_identity
from app.ports import DEFAULT_BASE_PORT
from app.safe_errors import error_name, public_error_message
from app.session import SessionAuthenticationError, SessionCoordinator
from app.trust import PeerTrustStore, PendingPeerTrust


logger = logging.getLogger(__name__)
_HEADER = struct.Struct(">I")
MAX_MESSAGE_SIZE = 64 * 1024 * 1024
MAX_AUTH_MESSAGE_SIZE = 4096


class NetworkProtocolError(ValueError):
    safe_for_user = True


class PairingRequired(ConnectionError):
    safe_for_user = True


class PairingDeclined(ConnectionError):
    safe_for_user = True


class PairingTimeout(ConnectionError):
    safe_for_user = True


class PeerIdentityChanged(ConnectionError):
    safe_for_user = True


class IncorrectPassword(ConnectionError):
    safe_for_user = True


class ServerUnavailable(ConnectionError):
    safe_for_user = True


class ConnectionTimedOut(TimeoutError):
    safe_for_user = True


class SecureConnectionFailed(ConnectionError):
    safe_for_user = True


class SecureLaneAuthenticationFailed(ConnectionError):
    safe_for_user = True


def _actionable_connection_error(error, role):
    if getattr(error, "safe_for_user", False):
        return error
    if isinstance(error, (socket.timeout, TimeoutError)):
        return ConnectionTimedOut(
            "Connection timed out. Check the server address and network, then try again."
        )
    if isinstance(error, ssl.SSLError):
        return SecureConnectionFailed(
            "Could not establish a secure connection. Restart DeskFlow on both computers and try again."
        )
    if isinstance(error, (ConnectionRefusedError, socket.gaierror, OSError)):
        return ServerUnavailable(
            "Could not reach the server. Check its address, port, and that DeskFlow is running."
        )
    if isinstance(error, SessionAuthenticationError) and role != "control":
        return SecureLaneAuthenticationFailed(
            "The secure session could not be completed. Reconnect and try again."
        )
    return error


class ConnectionPhase(str, Enum):
    DISCONNECTED = "disconnected"
    TLS_CANDIDATE = "tls_candidate"
    AWAITING_APPROVAL = "awaiting_approval"
    AUTHENTICATING = "authenticating"
    BINDING_LANES = "binding_lanes"
    CONNECTED = "connected"
    FAILED = "failed"


def _tls_client_context():
    # DeskFlow authenticates its self-signed peer with an explicit certificate
    # fingerprint, so loading the platform CA store is unnecessary.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _read_exact(conn, size):
    data = bytearray()
    while len(data) < size:
        packet = conn.recv(size - len(data))
        if not packet:
            raise ConnectionError("connection closed")
        data.extend(packet)
    return bytes(data)


def _read_message(conn, max_size=MAX_MESSAGE_SIZE):
    size = _HEADER.unpack(_read_exact(conn, _HEADER.size))[0]
    if size > max_size:
        raise NetworkProtocolError("message exceeds the size limit")
    try:
        value = json.loads(_read_exact(conn, size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NetworkProtocolError("message contains invalid JSON") from error
    if not isinstance(value, dict):
        raise NetworkProtocolError("message must be a JSON object")
    return value


def _encode_message(value):
    try:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise NetworkProtocolError("message is not valid JSON") from error
    if len(payload) > MAX_MESSAGE_SIZE:
        raise NetworkProtocolError("message exceeds the size limit")
    return _HEADER.pack(len(payload)) + payload


def _write_message(conn, value):
    conn.sendall(_encode_message(value))


class NetworkNode:
    def __init__(self, heartbeat_interval=2.0, heartbeat_timeout=6.0):
        self.sock = None
        self.connected = False
        self.authenticated = False
        self.callbacks = {}
        self.receive_thread = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._generation = 0
        self._heartbeat_interval = float(heartbeat_interval)
        self._heartbeat_timeout = float(heartbeat_timeout)
        self._heartbeat_stop = threading.Event()
        self._last_received = 0.0
        self._heartbeat_thread = None

    def register_callback(self, event_type, callback):
        self.callbacks.setdefault(event_type, []).append(callback)

    def peer_certificate_fingerprint(self):
        with self._state_lock:
            sock = self.sock
        if sock is None or not hasattr(sock, "getpeercert"):
            raise RuntimeError("there is no live TLS peer certificate")
        certificate = sock.getpeercert(binary_form=True)
        if not certificate:
            raise RuntimeError("there is no live TLS peer certificate")
        return hashlib.sha256(certificate).hexdigest()

    def trigger_callbacks(self, event_type, data):
        for callback in tuple(self.callbacks.get(event_type, ())):
            try:
                callback(data)
            except Exception as error:
                logger.error(
                    "Network callback failed for event %s (%s)",
                    event_type, error_name(error),
                )

    def _attach_socket(self, conn):
        with self._state_lock:
            previous = self.sock
            previous_stop = self._heartbeat_stop
            self._generation += 1
            generation = self._generation
            self.sock = conn
            self.connected = True
            self.authenticated = True
            self._last_received = time.monotonic()
            self._heartbeat_stop = threading.Event()
            heartbeat_stop = self._heartbeat_stop
        previous_stop.set()
        if previous is not None and previous is not conn:
            self._close_socket(previous)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(conn, generation, heartbeat_stop),
            daemon=True,
        )
        self._heartbeat_thread.start()
        return generation

    def _is_current(self, conn, generation):
        with self._state_lock:
            return self.sock is conn and self._generation == generation and self.connected

    def send_message(self, message):
        try:
            frame = _encode_message(message)
        except NetworkProtocolError as error:
            logger.error("Local network message was rejected (%s)", error_name(error))
            return False
        with self._state_lock:
            conn = self.sock
            generation = self._generation
            connected = self.connected
        if not connected or conn is None:
            return False
        try:
            with self._send_lock:
                if not self._is_current(conn, generation):
                    return False
                conn.sendall(frame)
            return True
        except Exception as error:
            logger.error("Network send failed (%s)", error_name(error))
            self._disconnect_socket(conn, generation)
            return False

    def _receive_loop(self, conn, generation):
        try:
            while self._is_current(conn, generation):
                message = _read_message(conn)
                with self._state_lock:
                    if self.sock is conn and self._generation == generation:
                        self._last_received = time.monotonic()
                event_type = message.get("type")
                if event_type == "__deskflow_heartbeat__":
                    self.send_message({"type": "__deskflow_heartbeat_ack__"})
                    continue
                if event_type == "__deskflow_heartbeat_ack__":
                    continue
                if isinstance(event_type, str) and event_type:
                    self.trigger_callbacks(event_type, message)
                else:
                    raise NetworkProtocolError("message type is missing")
        except (ConnectionError, OSError, ssl.SSLError, NetworkProtocolError):
            pass
        finally:
            self._disconnect_socket(conn, generation)

    def _heartbeat_loop(self, conn, generation, stop_event):
        while not stop_event.wait(self._heartbeat_interval):
            with self._state_lock:
                if not (
                    self.sock is conn
                    and self._generation == generation
                    and self.connected
                ):
                    return
                last_received = self._last_received
            if time.monotonic() - last_received > self._heartbeat_timeout:
                self._disconnect_socket(conn, generation)
                return
            if not self.send_message({"type": "__deskflow_heartbeat__"}):
                return

    def _disconnect_socket(self, conn, generation):
        with self._state_lock:
            if self.sock is not conn or self._generation != generation:
                self._close_socket(conn)
                return False
            was_connected = self.connected
            self.sock = None
            self.connected = False
            self.authenticated = False
            self._heartbeat_stop.set()
        self._close_socket(conn)
        if was_connected:
            self.trigger_callbacks("disconnected", {})
        return True

    def disconnect(self):
        with self._state_lock:
            conn = self.sock
            generation = self._generation
        if conn is None:
            return False
        return self._disconnect_socket(conn, generation)

    @staticmethod
    def _close_socket(conn):
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        try:
            conn.close()
        except OSError:
            pass


class NetworkServer(NetworkNode):
    def __init__(
        self,
        password,
        host="0.0.0.0",
        port=DEFAULT_BASE_PORT,
        *,
        role="control",
        coordinator=None,
        identity=None,
        handshake_timeout=3.0,
        auth_timeout=120.0,
    ):
        super().__init__()
        if role not in {"control", "data"}:
            raise ValueError("network server role must be control or data")
        self.is_server = True
        self.password = password
        self.host = host
        self.port = port
        self.role = role
        self.coordinator = coordinator or SessionCoordinator(password)
        self.identity = identity or load_identity()
        self.handshake_timeout = float(handshake_timeout)
        self.auth_timeout = float(auth_timeout)
        self.server_sock = None
        self.accept_thread = None
        self._running = False
        self._server_generation = 0
        self._candidate_slots = threading.BoundedSemaphore(16)
        self._candidate_lock = threading.Lock()
        self._candidate_sockets = set()
        self.client_addr = None
        self.session_id = None
        self.session_offer = None
        self._admission_lock = threading.Lock()
        self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ssl_context.load_cert_chain(
            certfile=self.identity.cert_path,
            keyfile=self.identity.key_path,
            password=self.identity.password,
        )

    def start(self):
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((self.host, self.port))
            server_sock.listen(8)
            server_sock.settimeout(0.2)
            self.server_sock = server_sock
            self.port = server_sock.getsockname()[1]
            self._running = True
            self._server_generation += 1
            self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self.accept_thread.start()
            logger.info("%s server is listening", self.role.capitalize())
            return True
        except Exception as error:
            logger.error(
                "Failed to start %s server (%s)", self.role, error_name(error)
            )
            self.stop()
            return False

    def _accept_loop(self):
        while self._running:
            try:
                raw, address = self.server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if not self._candidate_slots.acquire(blocking=False):
                self._close_socket(raw)
                continue
            with self._candidate_lock:
                self._candidate_sockets.add(raw)
            threading.Thread(
                target=self._candidate_worker,
                args=(raw, address, self._server_generation),
                daemon=True,
            ).start()

    def _candidate_worker(self, raw, address, server_generation):
        try:
            self._handle_candidate(raw, address, server_generation)
        finally:
            with self._candidate_lock:
                self._candidate_sockets.discard(raw)
            self._candidate_slots.release()

    def _handle_candidate(self, raw, address, server_generation=None):
        secure = None
        try:
            raw.settimeout(self.handshake_timeout)
            secure = self.ssl_context.wrap_socket(raw, server_side=True)
            with self._candidate_lock:
                self._candidate_sockets.discard(raw)
                self._candidate_sockets.add(secure)
            secure.settimeout(self.auth_timeout)
            request = _read_message(secure, MAX_AUTH_MESSAGE_SIZE)
            with self._admission_lock:
                if (
                    not self._running
                    or server_generation != self._server_generation
                ):
                    raise ConnectionError("server stopped during authentication")
                if self.role == "control":
                    if request.get("type") != "auth":
                        raise SessionAuthenticationError("control authentication is required")
                    offer = self.coordinator.authenticate_control(
                        request.get("password"), peer_address=address[0]
                    )
                    response = {
                        "type": "auth_success",
                        "session_id": offer.session_id,
                        "data_token": offer.data_token,
                        "file_token": offer.file_token,
                    }
                    session_id = offer.session_id
                    self.session_offer = offer
                with self._state_lock:
                    if self.connected:
                        stale = False
                        if self.sock is not None:
                            try:
                                r, _, _ = select.select([self.sock], [], [], 0)
                                if r:
                                    peek = self.sock.recv(1, socket.MSG_PEEK)
                                    if not peek:
                                        stale = True
                            except Exception:
                                stale = True
                        else:
                            stale = True

                        if stale or self.role == "control":
                            logger.info("Server: Replacing prior control session with new incoming connection candidate.")
                            conn_to_close = self.sock
                            self.sock = None
                            self.connected = False
                            self.authenticated = False
                            self._heartbeat_stop.set()
                            if conn_to_close:
                                self._close_socket(conn_to_close)
                        else:
                            raise ConnectionError("a peer is already connected")
                if self.role != "control":
                    if request.get("type") != "lane_auth":
                        raise SessionAuthenticationError("lane authentication is required")
                    session_id = request.get("session_id")
                    self.coordinator.consume_lane(
                        request.get("token"),
                        "data",
                        session_id,
                        peer_address=address[0],
                    )
                    response = {"type": "auth_success", "session_id": session_id}
                _write_message(secure, response)
                secure.settimeout(None)
                generation = self._attach_socket(secure)
                self.client_addr = address
                self.session_id = session_id
            with self._candidate_lock:
                self._candidate_sockets.discard(secure)
            self.trigger_callbacks("connected", {"addr": address, "session_id": session_id})
            self._receive_loop(secure, generation)
        except SessionAuthenticationError:
            if secure is not None:
                try:
                    _write_message(secure, {"type": "auth_failure"})
                except Exception:
                    pass
                self._close_socket(secure)
            else:
                self._close_socket(raw)
        except (ConnectionError, OSError, ssl.SSLError, NetworkProtocolError):
            if secure is not None:
                self._close_socket(secure)
            else:
                self._close_socket(raw)
        finally:
            with self._candidate_lock:
                self._candidate_sockets.discard(raw)
                if secure is not None:
                    self._candidate_sockets.discard(secure)

    def stop(self):
        with self._admission_lock:
            self._running = False
            self._server_generation += 1
        with self._candidate_lock:
            candidates = tuple(self._candidate_sockets)
            self._candidate_sockets.clear()
        for candidate in candidates:
            self._close_socket(candidate)
        self.disconnect()
        server, self.server_sock = self.server_sock, None
        if server is not None:
            self._close_socket(server)


class NetworkClient(NetworkNode):
    def __init__(
        self,
        password,
        *,
        role="control",
        trust_store=None,
        fingerprint_approval=None,
        expected_fingerprint=None,
        lane_token=None,
        session_id=None,
        connect_timeout=3.0,
        handshake_timeout=3.0,
        auth_timeout=3.0,
        approval_timeout=120.0,
    ):
        super().__init__()
        if role not in {"control", "data"}:
            raise ValueError("network client role must be control or data")
        self.is_server = False
        self.password = password
        self.role = role
        self.trust_store = trust_store or PeerTrustStore()
        self.fingerprint_approval = fingerprint_approval
        self.expected_fingerprint = expected_fingerprint
        self.lane_token = lane_token
        self.session_id = session_id
        self.connect_timeout = float(connect_timeout)
        self.handshake_timeout = float(handshake_timeout)
        self.auth_timeout = float(auth_timeout)
        self.approval_timeout = float(approval_timeout)
        self.host = None
        self.port = None
        self.session_info = None
        self._pending_trust = None
        self.phase = ConnectionPhase.DISCONNECTED
        self.last_error = None

    def _set_phase(self, phase):
        with self._state_lock:
            self.phase = phase
        logger.info("[%s-lane] Phase changed to %s", self.role, phase.value if hasattr(phase, "value") else phase)

    def connect(self, host, port, callback):
        def worker():
            raw = None
            secure = None
            reported = False

            def report(success, error):
                nonlocal reported
                if not reported and callback is not None:
                    reported = True
                    callback(success, error)

            try:
                max_attempts = 2 if self.role == "control" else 1
                for attempt in range(max_attempts):
                    try:
                        self.last_error = None
                        self._set_phase(ConnectionPhase.TLS_CANDIDATE)
                        self.host = host
                        self.port = int(port)
                        logger.info("[%s-lane] Connecting to %s:%d...", self.role, host, self.port)
                        raw = socket.create_connection((host, port), timeout=self.connect_timeout)
                        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        raw.settimeout(self.handshake_timeout)
                        secure = _tls_client_context().wrap_socket(raw, server_hostname=host)
                        certificate = secure.getpeercert(binary_form=True)
                        if not certificate:
                            raise ssl.SSLError("server did not provide a certificate")
                        fingerprint = hashlib.sha256(certificate).hexdigest()
                        logger.info("[%s-lane] TLS wrap successful; peer fingerprint %s", self.role, fingerprint[:12])
                        if self.role == "control":
                            peer = self.trust_store.peer_id(host, port)
                            pinned = self.trust_store.load(peer)
                            if pinned is not None and pinned != fingerprint:
                                raise PeerIdentityChanged("server identity changed; re-pair is required")
                            if pinned is None:
                                self._set_phase(ConnectionPhase.AWAITING_APPROVAL)
                                pending = PendingPeerTrust(self.trust_store, peer, fingerprint)
                                if self.fingerprint_approval is None:
                                    raise PairingRequired("first connection requires pairing approval")
                                if not self._request_pairing_approval(fingerprint, peer):
                                    pending.decline()
                                    raise PairingDeclined("Pairing was declined.")
                                pending.approve()
                                self._pending_trust = pending
                            request = {"type": "auth", "password": self.password}
                        else:
                            if not self.expected_fingerprint or fingerprint != self.expected_fingerprint:
                                raise PeerIdentityChanged("secondary lane certificate does not match control")
                            request = {
                                "type": "lane_auth",
                                "token": self.lane_token,
                                "session_id": self.session_id,
                            }
                        self._set_phase(ConnectionPhase.AUTHENTICATING)
                        secure.settimeout(self.auth_timeout)
                        _write_message(secure, request)
                        response = _read_message(secure)
                        if response.get("type") == "auth_failure":
                            if self.role == "control":
                                raise IncorrectPassword(
                                    "Incorrect password. Check the password shown on the server and try again."
                                )
                            raise SecureLaneAuthenticationFailed(
                                "The secure session could not be completed. Reconnect and try again."
                            )
                        if response.get("type") != "auth_success":
                            raise SessionAuthenticationError("authentication was not acknowledged")
                        break
                    except (SessionAuthenticationError, IncorrectPassword, PairingDeclined, PeerIdentityChanged):
                        raise
                    except (ConnectionError, OSError, ssl.SSLError) as err:
                        if raw is not None:
                            self._close_socket(raw)
                        if secure is not None:
                            self._close_socket(secure)
                        raw = None
                        secure = None
                        if attempt < max_attempts - 1 and self.role == "control":
                            logger.warning("[%s-lane] Connection attempt %d failed (%s); retrying in 0.4s...", self.role, attempt + 1, err)
                            time.sleep(0.4)
                            continue
                        raise

                if self.role == "control":
                    required = ("session_id", "data_token", "file_token")
                    if not all(isinstance(response.get(key), str) for key in required):
                        raise NetworkProtocolError("control session offer is incomplete")
                    self.session_info = {key: response[key] for key in required}
                    if self._pending_trust is not None:
                        self._pending_trust.authenticated()
                    self._set_phase(ConnectionPhase.BINDING_LANES)
                else:
                    self._set_phase(ConnectionPhase.CONNECTED)
                secure.settimeout(None)
                generation = self._attach_socket(secure)
                logger.info("[%s-lane] Authenticated successfully and bound to session", self.role)
                self.trigger_callbacks("connected", {"host": host, "session_id": response.get("session_id")})
                self.receive_thread = threading.Thread(
                    target=self._receive_loop,
                    args=(secure, generation),
                    daemon=True,
                )
                self.receive_thread.start()
                report(True, None)
            except Exception as raw_error:
                logger.error(
                    "[%s-lane] Failed during phase %s: %s (%s)",
                    self.role, getattr(self, 'phase', 'UNKNOWN'),
                    type(raw_error).__name__, raw_error,
                    exc_info=True,
                )
                error = _actionable_connection_error(raw_error, self.role)
                self.last_error = error
                self._pending_trust = None
                self._set_phase(ConnectionPhase.FAILED)
                if secure is not None:
                    self._close_socket(secure)
                elif raw is not None:
                    self._close_socket(raw)
                report(False, public_error_message(error, "connection failed"))

        threading.Thread(target=worker, daemon=True).start()

    def _request_pairing_approval(self, fingerprint, peer):
        result = []
        errors = []
        finished = threading.Event()

        def approve():
            try:
                result.append(bool(self.fingerprint_approval(fingerprint, peer)))
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        threading.Thread(target=approve, daemon=True).start()
        if not finished.wait(self.approval_timeout):
            raise PairingTimeout("Pairing approval timed out. Try again.")
        if errors:
            raise errors[0]
        return result[0] if result else False

    def commit_peer_trust(self):
        with self._state_lock:
            if not (
                self.role == "control" and self.authenticated
                and self.connected and self.sock is not None
            ):
                return False
            pending = self._pending_trust
            if pending is None:
                self.phase = ConnectionPhase.CONNECTED
                return False
            pending.lanes_bound()
            committed = pending.commit_if_ready()
            if committed:
                self._pending_trust = None
                self.phase = ConnectionPhase.CONNECTED
            return committed

    def disconnect(self, preserve_failure=False, error=None):
        disconnected = super().disconnect()
        with self._state_lock:
            self._pending_trust = None
            if preserve_failure:
                if error is not None:
                    self.last_error = error
                self.phase = ConnectionPhase.FAILED
            else:
                self.phase = ConnectionPhase.DISCONNECTED
        return disconnected

    def _disconnect_socket(self, conn, generation):
        disconnected = super()._disconnect_socket(conn, generation)
        if disconnected:
            with self._state_lock:
                self._pending_trust = None
                if self.phase is not ConnectionPhase.FAILED:
                    self.phase = ConnectionPhase.DISCONNECTED
        return disconnected
