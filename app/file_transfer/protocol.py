import hashlib
from app.hashing import new_hasher
import hmac
import threading
import json
import struct


MAX_METADATA_SIZE = 64 * 1024
MAX_PAYLOAD_SIZE = 1 << 20
_HEADER = struct.Struct(">II")


class AuthenticationError(ValueError):
    pass


class FrameError(ValueError):
    pass


class SessionAuthenticator:
    def __init__(self, token):
        if not isinstance(token, str) or len(token) < 8:
            raise ValueError("session token is too short")
        self._token_digest = new_hasher(token.encode("utf-8")).digest()
        self._consumed = False
        self._lock = threading.Lock()

    def authenticate(self, candidate):
        with self._lock:
            if self._consumed or not isinstance(candidate, str):
                raise AuthenticationError("session token is invalid or already used")
            candidate_digest = new_hasher(candidate.encode("utf-8")).digest()
            if not hmac.compare_digest(candidate_digest, self._token_digest):
                raise AuthenticationError("session token is invalid or already used")
            self._consumed = True


def verify_certificate_fingerprint(certificate_der, expected_fingerprint):
    if not expected_fingerprint:
        raise AuthenticationError("a paired certificate fingerprint is required")
    actual = hashlib.sha256(certificate_der).hexdigest()
    if not hmac.compare_digest(actual, expected_fingerprint.lower()):
        raise AuthenticationError("peer certificate fingerprint does not match pairing")


def encode_frame(metadata, payload=b""):
    if not isinstance(payload, bytes):
        raise TypeError("frame payload must be bytes")
    try:
        metadata_bytes = json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise FrameError("frame metadata is not JSON serializable") from error
    if len(metadata_bytes) > MAX_METADATA_SIZE:
        raise FrameError("frame metadata exceeds its size limit")
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise FrameError("frame payload exceeds its size limit")
    return _HEADER.pack(len(metadata_bytes), len(payload)) + metadata_bytes + payload


def decode_frame(frame):
    if len(frame) < _HEADER.size:
        raise FrameError("frame header is truncated")
    metadata_size, payload_size = _HEADER.unpack_from(frame)
    if metadata_size > MAX_METADATA_SIZE or payload_size > MAX_PAYLOAD_SIZE:
        raise FrameError("frame declares an oversized section")
    expected_size = _HEADER.size + metadata_size + payload_size
    if len(frame) != expected_size:
        raise FrameError("frame is truncated or contains trailing bytes")
    metadata_bytes = frame[_HEADER.size:_HEADER.size + metadata_size]
    try:
        metadata = json.loads(metadata_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrameError("frame metadata is invalid JSON") from error
    if not isinstance(metadata, dict):
        raise FrameError("frame metadata must be an object")
    return metadata, frame[_HEADER.size + metadata_size:]
