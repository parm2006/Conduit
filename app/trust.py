"""Persistent, canonical peer certificate trust records."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from app.dpapi import WindowsDataProtector


@dataclass(frozen=True)
class PeerId:
    host: str
    port: int

    @property
    def canonical(self):
        return f"{self.host}:{self.port}"


class PeerTrustStore:
    def __init__(self, root=None, protector=None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Conduit" / "peers"
        self.root = Path(root or base).resolve()
        self.protector = protector or WindowsDataProtector()

    def peer_id(self, host, port):
        normalized = str(host).strip().casefold()
        if not normalized or any(character in normalized for character in "\r\n\0"):
            raise ValueError("peer host is invalid")
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("peer port is invalid")
        return PeerId(normalized, port)

    def _path(self, peer):
        if not isinstance(peer, PeerId):
            raise TypeError("peer must be a PeerId")
        digest = hashlib.sha256(peer.canonical.encode("utf-8")).hexdigest()
        path = (self.root / f"{digest}.json").resolve()
        if path.parent != self.root:
            raise ValueError("peer record escaped its root")
        return path

    def _legacy_path(self, peer):
        legacy_name = f"{peer.host}.fingerprint"
        if (
            Path(legacy_name).name != legacy_name
            or not re.fullmatch(r"[A-Za-z0-9._-]+\.fingerprint", legacy_name)
        ):
            return None
        path = (self.root / legacy_name).resolve()
        return path if path.parent == self.root else None

    def load(self, peer):
        path = self._path(peer)
        if not path.exists():
            return self._migrate_legacy(peer)
        decoded = self.protector.unprotect(path.read_bytes())
        record = json.loads(decoded.decode("utf-8"))
        if record.get("peer") != peer.canonical:
            raise ValueError("peer trust record identity does not match")
        fingerprint = record.get("fingerprint")
        self._validate_fingerprint(fingerprint)
        legacy = self._legacy_path(peer)
        if legacy is not None:
            legacy.unlink(missing_ok=True)
        return fingerprint

    def _migrate_legacy(self, peer):
        legacy = self._legacy_path(peer)
        if legacy is None or not legacy.is_file():
            return None
        try:
            fingerprint = legacy.read_text(encoding="ascii").strip()
            self._validate_fingerprint(fingerprint)
            self.commit(peer, fingerprint)
            return fingerprint.lower()
        except (OSError, UnicodeError, ValueError):
            return None
        finally:
            legacy.unlink(missing_ok=True)

    def commit(self, peer, fingerprint):
        self._validate_fingerprint(fingerprint)
        path = self._path(peer)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"peer": peer.canonical, "fingerprint": fingerprint.lower()},
            separators=(",", ":"),
        ).encode("utf-8")
        protected = self.protector.protect(payload)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self, peer):
        path = self._path(peer)
        existed = path.exists()
        path.unlink(missing_ok=True)
        legacy = self._legacy_path(peer)
        if legacy is not None:
            existed = legacy.exists() or existed
            legacy.unlink(missing_ok=True)
        return existed

    @staticmethod
    def _validate_fingerprint(fingerprint):
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
            raise ValueError("fingerprint must contain 64 hexadecimal characters")


class PendingPeerTrust:
    """Keep candidate trust in memory until the complete session is usable."""

    def __init__(self, store, peer, fingerprint):
        PeerTrustStore._validate_fingerprint(fingerprint)
        self.store = store
        self.peer = peer
        self.fingerprint = fingerprint.lower()
        self._approved = False
        self._authenticated = False
        self._lanes_bound = False
        self._declined = False
        self._committed = False

    def approve(self):
        if not self._declined:
            self._approved = True

    def decline(self):
        self._declined = True
        self._approved = False

    def authenticated(self):
        self._authenticated = True

    def lanes_bound(self):
        self._lanes_bound = True

    def commit_if_ready(self):
        if self._committed or self._declined:
            return False
        if not (self._approved and self._authenticated and self._lanes_bound):
            return False
        self.store.commit(self.peer, self.fingerprint)
        self._committed = True
        return True
