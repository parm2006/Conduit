"""One logical DeskFlow session shared by its independent network lanes."""

import logging
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
import uuid

logger = logging.getLogger(__name__)


class SessionAuthenticationError(ValueError):
    safe_for_user = True


@dataclass(frozen=True)
class SessionOffer:
    session_id: str
    data_token: str
    file_token: str


class SessionCoordinator:
    def __init__(self, password, clock=time.monotonic, token_ttl=10.0):
        self._password = str(password)
        self._clock = clock
        self._token_ttl = float(token_ttl)
        self._tokens = {}
        self._active_session = None
        self._lock = threading.Lock()

    def authenticate_control(self, candidate, peer_address=None):
        if not isinstance(candidate, str) or not hmac.compare_digest(
            candidate.encode("utf-8"), self._password.encode("utf-8")
        ):
            logger.warning("Control lane password authentication failed")
            raise SessionAuthenticationError("authentication failed")
        peer_address = self._normalize_peer_address(peer_address)
        with self._lock:
            self._tokens.clear()
            session_id = uuid.uuid4().hex
            self._active_session = session_id
            data = self._issue_locked(session_id, "data", peer_address)
            file_token = self._issue_locked(session_id, "file", peer_address)
            logger.info("Session %s created for control peer %s", session_id[:8], peer_address)
            return SessionOffer(session_id, data, file_token)

    def consume_lane(self, token, purpose, session_id, peer_address=None):
        if not isinstance(token, str) or not isinstance(session_id, str):
            logger.warning("Session lane consumption rejected: token or session_id is not a string")
            raise SessionAuthenticationError("lane token is invalid")
        peer_address = self._normalize_peer_address(peer_address)
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        with self._lock:
            record = self._tokens.get(digest)
            if record is None:
                logger.warning(
                    "Session lane consumption rejected for purpose '%s' (session %s): token is invalid or already used",
                    purpose, session_id[:8] if isinstance(session_id, str) else session_id,
                )
                raise SessionAuthenticationError("lane token is invalid or already used")
            expected_session, expected_purpose, expires, expected_peer = record
            if expected_peer is not None and peer_address != expected_peer:
                logger.warning(
                    "Session lane consumption rejected for purpose '%s': peer IP mismatch (expected '%s', got '%s')",
                    purpose, expected_peer, peer_address,
                )
                raise SessionAuthenticationError("lane token belongs to another peer")
            self._tokens.pop(digest, None)
            if self._clock() > expires:
                logger.warning("Session lane consumption rejected for purpose '%s': token expired", purpose)
                raise SessionAuthenticationError("lane token expired")
            if expected_session != session_id or expected_purpose != purpose:
                logger.warning(
                    "Session lane consumption rejected: purpose/session mismatch (expected %s/%s, got %s/%s)",
                    expected_purpose, expected_session[:8], purpose, session_id[:8],
                )
                raise SessionAuthenticationError("lane token belongs to another session or lane")
            if session_id != self._active_session:
                logger.warning("Session lane consumption rejected: session %s is no longer active", session_id[:8])
                raise SessionAuthenticationError("session is no longer active")
            logger.info("Session lane '%s' successfully bound for peer %s (session %s)", purpose, peer_address, session_id[:8])
            return True

    def close(self, session_id=None):
        with self._lock:
            if session_id is not None and session_id != self._active_session:
                return False
            self._active_session = None
            self._tokens.clear()
            return True

    @staticmethod
    def _normalize_peer_address(peer_address):
        if peer_address is None:
            return None
        try:
            return ipaddress.ip_address(str(peer_address)).compressed
        except ValueError as error:
            raise SessionAuthenticationError("peer address is invalid") from error

    def _issue_locked(self, session_id, purpose, peer_address):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        self._tokens[digest] = (
            session_id,
            purpose,
            self._clock() + self._token_ttl,
            peer_address,
        )
        return token
