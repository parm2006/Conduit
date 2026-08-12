"""Conduit local identity generation, migration, and loading."""

from dataclasses import dataclass
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.dpapi import WindowsDataProtector
from app.safe_errors import error_name


logger = logging.getLogger(__name__)

DEFAULT_IDENTITY_ROOT = Path(
    os.environ.get("LOCALAPPDATA", Path.home())
) / "Conduit" / "identity"

# Kept as location hints for older callers. New code should use load_identity().
CERT_FILE = str(DEFAULT_IDENTITY_ROOT / "cert.pem")
KEY_FILE = str(DEFAULT_IDENTITY_ROOT / "key.pem")


class IdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class IdentityMaterial:
    cert_path: Path
    key_path: Path
    password_path: Path
    password: bytes
    fingerprint: str
    recovered: bool = False


def _fingerprint(certificate):
    return hashlib.sha256(
        certificate.public_bytes(serialization.Encoding.DER)
    ).hexdigest()


def _keys_match(private_key, certificate):
    encoding = serialization.Encoding.DER
    public_format = serialization.PublicFormat.SubjectPublicKeyInfo
    return private_key.public_key().public_bytes(encoding, public_format) == (
        certificate.public_key().public_bytes(encoding, public_format)
    )


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class IdentityStore:
    """Own versioned identity generations and an atomic active pointer."""

    def __init__(self, root=None, legacy_root=None, protector=None):
        self.root = Path(root or DEFAULT_IDENTITY_ROOT).resolve()
        self.legacy_root = (
            None
            if legacy_root is False
            else Path(legacy_root).resolve()
            if legacy_root is not None
            else Path.cwd().resolve()
        )
        self.protector = protector or WindowsDataProtector()
        self.generations = self.root / "generations"
        self.pointer = self.root / "current.json"
        self.quarantine = self.root / "quarantine"

    def load_or_create(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self._remove_temporary_files()
        if self.pointer.exists():
            try:
                return self._load_active(False)
            except Exception as error:
                logger.warning(
                    "Conduit identity is unreadable; quarantining it (%s)",
                    error_name(error),
                )
                self._quarantine_active()
                return self._create(recovered=True)
        migrated = self._migrate_legacy()
        if migrated is not None:
            return migrated
        return self._create(recovered=False)

    def _remove_temporary_files(self):
        for path in self.root.rglob("*.tmp"):
            if path.is_file():
                path.unlink(missing_ok=True)

    def _load_active(self, recovered):
        pointer = json.loads(self.pointer.read_text(encoding="utf-8"))
        generation = pointer.get("generation")
        directory = self._generation_directory(generation)
        cert_path = directory / "cert.pem"
        key_path = directory / "key.pem"
        password_path = directory / "key-password.dpapi"
        password = self.protector.unprotect(password_path.read_bytes())
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        private_key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=password
        )
        if not _keys_match(private_key, certificate):
            raise IdentityError("certificate and private key do not match")
        return IdentityMaterial(
            cert_path=cert_path,
            key_path=key_path,
            password_path=password_path,
            password=password,
            fingerprint=_fingerprint(certificate),
            recovered=recovered,
        )

    def _generation_directory(self, generation):
        if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
            raise IdentityError("identity pointer is invalid")
        directory = (self.generations / generation).resolve()
        if directory.parent != self.generations.resolve():
            raise IdentityError("identity generation escaped its root")
        return directory

    def _create(self, recovered, private_key=None, certificate=None):
        private_key = private_key or rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        certificate = certificate or self._self_signed(private_key)
        password = secrets.token_urlsafe(32).encode("ascii")
        generation = uuid.uuid4().hex
        directory = self.generations / generation
        directory.mkdir(parents=True, exist_ok=False)
        _atomic_write(
            directory / "key.pem",
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password),
            ),
        )
        _atomic_write(directory / "cert.pem", certificate.public_bytes(serialization.Encoding.PEM))
        _atomic_write(directory / "key-password.dpapi", self.protector.protect(password))
        _atomic_write(
            self.pointer,
            json.dumps({"generation": generation}, separators=(",", ":")).encode("utf-8"),
        )
        return self._load_active(recovered)

    def _self_signed(self, private_key):
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Conduit")])
        return (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("Conduit")]), False)
            .sign(private_key, hashes.SHA256())
        )

    def _migrate_legacy(self):
        if self.legacy_root is None or self.legacy_root == self.root:
            return None
        cert_path = self.legacy_root / "cert.pem"
        key_path = self.legacy_root / "key.pem"
        if not cert_path.exists() or not key_path.exists():
            return None
        try:
            certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
            raw_key = key_path.read_bytes()
            try:
                private_key = serialization.load_pem_private_key(raw_key, password=None)
            except Exception:
                private_key = serialization.load_pem_private_key(
                    self.protector.unprotect(raw_key), password=None
                )
            if not _keys_match(private_key, certificate):
                raise IdentityError("legacy certificate and key do not match")
        except Exception as error:
            logger.warning(
                "Ignoring unreadable legacy Conduit identity (%s)",
                error_name(error),
            )
            return None
        material = self._create(False, private_key=private_key, certificate=certificate)
        key_path.unlink()
        return material

    def _quarantine_active(self):
        try:
            pointer = json.loads(self.pointer.read_text(encoding="utf-8"))
            generation = pointer.get("generation")
            source = self._generation_directory(generation)
            if source.is_dir():
                destination = self.quarantine / uuid.uuid4().hex
                destination.mkdir(parents=True, exist_ok=False)
                for child in source.iterdir():
                    shutil.move(str(child), destination / child.name)
                source.rmdir()
        except Exception as error:
            logger.error(
                "Could not fully quarantine the current Conduit identity (%s)",
                error_name(error),
            )
        self.pointer.unlink(missing_ok=True)


_default_store = None


def default_identity_store():
    global _default_store
    if _default_store is None:
        _default_store = IdentityStore()
    return _default_store


def load_identity():
    return default_identity_store().load_or_create()


def certificate_fingerprint(cert_file=None):
    path = Path(cert_file) if cert_file else load_identity().cert_path
    return _fingerprint(x509.load_pem_x509_certificate(path.read_bytes()))


def pairing_code_from_fingerprint(fingerprint):
    if not isinstance(fingerprint, str):
        return ""
    compact = re.sub(r"[^0-9a-fA-F]", "", fingerprint)
    if len(compact) != 64:
        return ""
    return "-".join(compact[:12].upper()[index:index + 4] for index in range(0, 12, 4))


pairing_code = pairing_code_from_fingerprint
