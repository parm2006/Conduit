import struct
import secrets
import socket
import ssl
import threading
import logging

from app.crypto import load_identity
from app.ports import DEFAULT_FILE_PORT
from app.network import _tls_client_context
from app.machine_identity import windows_machine_id
from app.safe_errors import error_name
from app.session import SessionAuthenticationError, SessionRegistry

from .protocol import (
    MAX_METADATA_SIZE,
    MAX_PAYLOAD_SIZE,
    AuthenticationError,
    FrameError,
    decode_frame,
    encode_frame,
    verify_certificate_fingerprint,
    SessionAuthenticator,
)


_HEADER = struct.Struct(">II")
logger = logging.getLogger(__name__)


def _receive_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise FrameError("connection closed during a frame")
        data.extend(chunk)
    return bytes(data)


def read_frame(sock):
    header = _receive_exact(sock, _HEADER.size)
    metadata_size, payload_size = _HEADER.unpack(header)
    if metadata_size > MAX_METADATA_SIZE or payload_size > MAX_PAYLOAD_SIZE:
        raise FrameError("frame declares an oversized section")
    body = _receive_exact(sock, metadata_size + payload_size)
    return decode_frame(header + body)


def send_frame(sock, metadata, payload=b""):
    sock.sendall(encode_frame(metadata, payload))


def authenticate_server_connection(
    sock,
    authenticator,
    expected_session_id=None,
    peer_address=None,
    *,
    metadata=None,
    payload=None,
    lane=None,
):
    if metadata is None:
        metadata, payload = read_frame(sock)
    if payload or metadata.get("type") != "authenticate":
        raise FrameError("file lane must authenticate before sending data")
    session_id = metadata.get("session_id")
    if expected_session_id is not None and session_id != expected_session_id:
        raise AuthenticationError("file lane belongs to another session")
    if hasattr(authenticator, "consume_lane"):
        try:
            if isinstance(authenticator, SessionRegistry):
                authenticator.bind_lane(
                    metadata.get("token"),
                    "file",
                    session_id,
                    peer_identity=metadata.get("peer_identity"),
                    peer_address=peer_address,
                    lane=lane,
                )
            else:
                authenticator.consume_lane(
                    metadata.get("token"),
                    "file",
                    session_id,
                    peer_address=peer_address,
                )
        except SessionAuthenticationError as error:
            raise AuthenticationError("file lane authentication failed") from error
    else:
        authenticator.authenticate(metadata.get("token"))
    send_frame(sock, {"type": "authenticated", "session_id": session_id})
    return session_id


def authenticate_client_connection(
    sock,
    expected_fingerprint,
    token,
    session_id=None,
    peer_identity=None,
):
    certificate = sock.getpeercert(binary_form=True)
    verify_certificate_fingerprint(certificate, expected_fingerprint)
    send_frame(
        sock,
        {
            "type": "authenticate",
            "token": token,
            "session_id": session_id,
            "peer_identity": peer_identity,
        },
    )
    metadata, payload = read_frame(sock)
    if (
        payload or metadata.get("type") != "authenticated"
        or metadata.get("session_id") != session_id
    ):
        raise FrameError("file lane authentication was not acknowledged")


class _FileLane:
    supports_chunk_ack = True

    def __init__(self):
        self.sock = None
        self._callbacks = {}
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._generation = 0

    def register_callback(self, event_type, callback):
        self._callbacks.setdefault(event_type, []).append(callback)

    def _trigger_callbacks(self, event_type, metadata, payload=b""):
        for callback in tuple(self._callbacks.get(event_type, ())):
            try:
                callback(metadata, payload)
            except Exception as error:
                logger.error(
                    "File-lane callback failed for event %s; "
                    "connection remains available (%s)",
                    event_type,
                    error_name(error),
                )

    def send(self, metadata, payload=b""):
        with self._state_lock:
            sock = self.sock
            generation = self._generation
        if sock is None:
            raise ConnectionError("file lane is not connected")
        with self._send_lock:
            with self._state_lock:
                if self.sock is not sock or self._generation != generation:
                    raise ConnectionError("file lane was replaced")
            send_frame(sock, metadata, payload)

    def _attach(self, sock):
        with self._state_lock:
            previous = self.sock
            self._generation += 1
            generation = self._generation
            self.sock = sock
        if previous is not None and previous is not sock:
            self._close(previous)
        return generation

    def _receive_loop(self, sock, generation):
        try:
            while True:
                with self._state_lock:
                    if self.sock is not sock or self._generation != generation:
                        return
                metadata, payload = read_frame(sock)
                self._trigger_callbacks(metadata.get("type"), metadata, payload)
        except (ConnectionError, OSError, FrameError):
            pass
        finally:
            self._close_generation(sock, generation)

    def _close_generation(self, sock, generation):
        with self._state_lock:
            if self.sock is not sock or self._generation != generation:
                self._close(sock)
                return False
            self.sock = None
        self._close(sock)
        self._trigger_callbacks("disconnected", {"type": "disconnected"})
        return True

    def close(self):
        with self._state_lock:
            sock = self.sock
            generation = self._generation
        if sock is not None:
            return self._close_generation(sock, generation)
        return False

    @staticmethod
    def _close(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


class FileLaneClient(_FileLane):
    def __init__(self, peer_identity=None):
        super().__init__()
        self.peer_identity = peer_identity or windows_machine_id()

    def connect(self, host, port, expected_fingerprint, token, session_id=None, timeout=3):
        logger.info("[file-lane] Client connecting to %s:%d (session %s)...", host, port, session_id[:8] if session_id else None)
        raw_sock = socket.create_connection((host, port), timeout=timeout)
        secure_sock = None
        context = _tls_client_context()
        try:
            raw_sock.settimeout(timeout)
            secure_sock = context.wrap_socket(raw_sock, server_hostname=host)
            logger.info("[file-lane] TLS wrap successful; authenticating token...")
            authenticate_client_connection(
                secure_sock,
                expected_fingerprint,
                token,
                session_id=session_id,
                peer_identity=self.peer_identity,
            )
            logger.info("[file-lane] Client authenticated successfully")
        except Exception as error:
            logger.error(
                "[file-lane] Connection to %s:%d failed (%s: %s)",
                host, port, type(error).__name__, error, exc_info=True
            )
            self._close(secure_sock if secure_sock is not None else raw_sock)
            raise
        secure_sock.settimeout(None)
        generation = self._attach(secure_sock)
        self._trigger_callbacks(
            "connected",
            {"type": "connected", "session_id": session_id},
        )
        threading.Thread(
            target=self._receive_loop, args=(secure_sock, generation), daemon=True
        ).start()


class _FileServerConnection(_FileLane):
    def __init__(self, owner, session_id, peer_identity, address):
        super().__init__()
        self.owner = owner
        self.session_id = session_id
        self.peer_identity = peer_identity
        self.address = address

    def _trigger_callbacks(self, event_type, metadata, payload=b""):
        enriched = dict(metadata)
        if self.session_id is not None:
            enriched["session_id"] = self.session_id
        if self.session_id is not None and self.peer_identity is not None:
            enriched["peer_identity"] = self.peer_identity
        self.owner._trigger_callbacks(event_type, enriched, payload)

    def _close_generation(self, sock, generation):
        closed = super()._close_generation(sock, generation)
        if closed:
            self.owner._connection_closed(self)
        return closed


class FileLaneServer:
    _close = staticmethod(_FileLane._close)

    def __init__(
        self,
        cert_file=None,
        key_file=None,
        host="0.0.0.0",
        port=DEFAULT_FILE_PORT,
        *,
        key_password=None,
        identity=None,
        handshake_timeout=3.0,
        auth_timeout=10.0,
        coordinator=None,
    ):
        self.host = host
        self.port = port
        self._server_sock = None
        self._running = False
        self._server_generation = 0
        self._candidate_slots = threading.BoundedSemaphore(8)
        self._candidate_lock = threading.Lock()
        self._candidate_sockets = set()
        self._offers = {}
        self.coordinator = coordinator
        self._auth_lock = threading.Lock()
        self._callbacks = {}
        self.connections = {}
        self._connections_lock = threading.RLock()
        self.handshake_timeout = float(handshake_timeout)
        self.auth_timeout = float(auth_timeout)
        if identity is None and (cert_file is None or key_file is None):
            identity = load_identity()
        if identity is not None:
            cert_file = identity.cert_path
            key_file = identity.key_path
            key_password = identity.password
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._context.load_cert_chain(
            certfile=cert_file,
            keyfile=key_file,
            password=key_password,
        )

    @property
    def sock(self):
        with self._connections_lock:
            if len(self.connections) != 1:
                return None
            return next(iter(self.connections.values())).sock

    def register_callback(self, event_type, callback):
        self._callbacks.setdefault(event_type, []).append(callback)

    def _trigger_callbacks(self, event_type, metadata, payload=b""):
        for callback in tuple(self._callbacks.get(event_type, ())):
            try:
                callback(metadata, payload)
            except Exception as error:
                logger.error(
                    "File-lane callback failed for event %s; "
                    "connection remains available (%s)",
                    event_type,
                    error_name(error),
                )

    def connection(self, session_id):
        with self._connections_lock:
            return self.connections.get(session_id)

    def send(self, metadata, payload=b"", session_id=None):
        with self._connections_lock:
            if session_id is None:
                if len(self.connections) != 1:
                    raise ConnectionError("file destination session is required")
                connection = next(iter(self.connections.values()))
            else:
                connection = self.connections.get(session_id)
        if connection is None:
            raise ConnectionError("file lane is not connected")
        return connection.send(metadata, payload)

    def issue_session(self):
        token = secrets.token_urlsafe(32)
        self.offer_session(token)
        return token

    def offer_session(self, token, session_id=None):
        with self._auth_lock:
            authenticator = (
                self.coordinator if self.coordinator is not None
                else SessionAuthenticator(token)
            )
            self._offers[session_id] = authenticator
            logger.info("[file-lane] Offsetting/Offered file-lane session %s", session_id[:8] if session_id else None)

    def revoke_session(self, session_id=None):
        with self._auth_lock:
            if session_id is None:
                self._offers.clear()
            else:
                self._offers.pop(session_id, None)

    def close(self):
        self.revoke_session()
        with self._connections_lock:
            connections = tuple(self.connections.values())
        closed = False
        for connection in connections:
            closed = connection.close() or closed
        return closed

    def _connection_closed(self, connection):
        with self._connections_lock:
            if self.connections.get(connection.session_id) is connection:
                self.connections.pop(connection.session_id, None)

    def start(self):
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((self.host, self.port))
            server_sock.listen(1)
            server_sock.settimeout(0.2)
            self.port = server_sock.getsockname()[1]
            self._server_sock = server_sock
            self._running = True
            self._server_generation += 1
            threading.Thread(target=self._accept_loop, daemon=True).start()
            logger.info("[file-lane] Server listening on %s:%d", self.host, self.port)
            return True
        except OSError as error:
            logger.error("[file-lane] Failed to start server (%s)", error)
            self.stop()
            return False

    def _accept_loop(self):
        while self._running:
            try:
                raw_sock, address = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if not self._candidate_slots.acquire(blocking=False):
                self._close(raw_sock)
                continue
            with self._candidate_lock:
                self._candidate_sockets.add(raw_sock)
            logger.info("[file-lane] Incoming connection candidate from %s", address)
            threading.Thread(
                target=self._candidate_worker,
                args=(raw_sock, address, self._server_generation),
                daemon=True,
            ).start()

    def _candidate_worker(self, raw_sock, address, server_generation):
        try:
            self._handle_candidate(raw_sock, address, server_generation)
        finally:
            with self._candidate_lock:
                self._candidate_sockets.discard(raw_sock)
            self._candidate_slots.release()

    def _handle_candidate(self, raw_sock, address, server_generation=None):
        secure_sock = None
        try:
            raw_sock.settimeout(self.handshake_timeout)
            secure_sock = self._context.wrap_socket(raw_sock, server_side=True)
            with self._candidate_lock:
                self._candidate_sockets.discard(raw_sock)
                self._candidate_sockets.add(secure_sock)
            secure_sock.settimeout(self.auth_timeout)
            metadata, payload = read_frame(secure_sock)
            session_id = metadata.get("session_id")
            with self._auth_lock:
                if (
                    not self._running
                    or server_generation != self._server_generation
                ):
                    raise AuthenticationError("file server stopped during authentication")
                if session_id not in self._offers:
                    raise AuthenticationError("no file-lane session was offered")
                authenticator = self._offers[session_id]
            connection = _FileServerConnection(
                self,
                session_id,
                metadata.get("peer_identity"),
                address,
            )
            authenticated_session_id = authenticate_server_connection(
                secure_sock,
                authenticator,
                expected_session_id=session_id,
                peer_address=address[0],
                metadata=metadata,
                payload=payload,
                lane=connection,
            )
            with self._auth_lock:
                if (
                    not self._running
                    or server_generation != self._server_generation
                    or self._offers.get(session_id) is not authenticator
                ):
                    raise AuthenticationError("file session changed during authentication")
                self._offers.pop(session_id, None)
                secure_sock.settimeout(None)
                generation = connection._attach(secure_sock)
                with self._connections_lock:
                    previous = self.connections.get(authenticated_session_id)
                    self.connections[authenticated_session_id] = connection
                if previous is not None:
                    previous.close()
            with self._candidate_lock:
                self._candidate_sockets.discard(secure_sock)
            logger.info("[file-lane] Server authenticated candidate from %s successfully", address)
            connection._trigger_callbacks(
                "connected",
                {"type": "connected", "session_id": authenticated_session_id},
            )
            threading.Thread(
                target=connection._receive_loop,
                args=(secure_sock, generation),
                daemon=True,
            ).start()
        except Exception as error:
            logger.error("[file-lane] Candidate from %s failed authentication (%s: %s)", address, type(error).__name__, error, exc_info=True)
            if secure_sock is not None:
                self._close(secure_sock)
            else:
                self._close(raw_sock)
        finally:
            with self._candidate_lock:
                self._candidate_sockets.discard(raw_sock)
                if secure_sock is not None:
                    self._candidate_sockets.discard(secure_sock)

    def stop(self):
        with self._auth_lock:
            self._running = False
            self._server_generation += 1
            self._offers.clear()
        with self._candidate_lock:
            candidates = tuple(self._candidate_sockets)
            self._candidate_sockets.clear()
        for candidate in candidates:
            self._close(candidate)
        self.close()
        server_sock, self._server_sock = self._server_sock, None
        if server_sock is not None:
            server_sock.close()
