"""Ephemeral authenticated-encryption staging for incoming file data."""

import hashlib
import bisect
import os
import secrets
import shutil
import struct
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .validation import ValidationError, validate_relative_path


_RECORD_HEADER = struct.Struct(">QII12s")


class IntegrityError(ValueError):
    pass


def cleanup_staging_root(root):
    """Discard ciphertext that cannot be resumed because its key was memory-only."""
    root = Path(root)
    for name in ("partial", "completed"):
        shutil.rmtree(root / name, ignore_errors=True)


class StagedFile:
    def __init__(
        self, root, job_id, relative_path, expected_size, expected_sha256,
        encryption_key=None,
    ):
        if not isinstance(job_id, str) or not job_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in job_id
        ):
            raise ValidationError("job ID contains unsafe characters")
        if expected_size < 0:
            raise ValidationError("expected size cannot be negative")
        if len(expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in expected_sha256.lower()
        ):
            raise ValidationError("expected SHA-256 must contain 64 hexadecimal characters")

        self.root = Path(root).resolve()
        self.job_id = job_id
        self.relative_path = validate_relative_path(relative_path)
        self.expected_size = expected_size
        self.expected_sha256 = expected_sha256.lower()
        partial_dir = self.root / "partial" / job_id
        partial_dir.mkdir(parents=True, exist_ok=True)
        self.partial_path = partial_dir / f"{secrets.token_hex(16)}.partial"
        self.storage_path = self.partial_path
        self._file = self.partial_path.open("xb")
        self._offset = 0
        self._hash = hashlib.sha256()
        self._cipher = AESGCM(
            encryption_key or AESGCM.generate_key(bit_length=256)
        )
        self._records = []
        self._record_ends = []
        self._lock = threading.RLock()
        self._finalized = False

    def _aad(self, record_index, offset, plain_size):
        identity = f"{self.job_id}\0{self.relative_path}".encode("utf-8")
        return identity + struct.pack(">IQI", record_index, offset, plain_size)

    def write(self, offset, data):
        if not isinstance(data, bytes):
            raise TypeError("chunk data must be bytes")
        with self._lock:
            if self._file.closed:
                raise ValueError("staged file is closed")
            if offset != self._offset:
                raise ValueError(
                    f"chunk offset {offset} does not match expected offset {self._offset}"
                )
            if self._offset + len(data) > self.expected_size:
                raise ValueError("chunk exceeds the declared size")
            nonce = os.urandom(12)
            record_index = len(self._records)
            ciphertext = self._cipher.encrypt(
                nonce, data, self._aad(record_index, offset, len(data))
            )
            self._file.write(_RECORD_HEADER.pack(offset, len(data), len(ciphertext), nonce))
            cipher_offset = self._file.tell()
            self._file.write(ciphertext)
            self._records.append(
                (record_index, offset, len(data), cipher_offset, len(ciphertext), nonce)
            )
            self._record_ends.append(offset + len(data))
            self._hash.update(data)
            self._offset += len(data)

    @property
    def received_size(self):
        with self._lock:
            return self._offset

    def read_available(self, offset, count):
        if offset < 0 or count < 0:
            raise ValueError("range values cannot be negative")
        if count == 0:
            return b""
        with self._lock:
            available_end = min(self._offset, offset + count)
            if offset >= available_end:
                return b""
            if not self._file.closed:
                self._file.flush()
            first_record = bisect.bisect_right(self._record_ends, offset)
            result = bytearray()
            with self.storage_path.open("rb") as source:
                for index in range(first_record, len(self._records)):
                    (
                        record_index, start, plain_size, cipher_offset,
                        cipher_size, nonce,
                    ) = self._records[index]
                    end = start + plain_size
                    if end <= offset:
                        continue
                    if start >= available_end:
                        break
                    source.seek(cipher_offset)
                    ciphertext = source.read(cipher_size)
                    if len(ciphertext) != cipher_size:
                        raise IntegrityError("encrypted staging record is truncated")
                    try:
                        plaintext = self._cipher.decrypt(
                            nonce, ciphertext,
                            self._aad(record_index, start, plain_size),
                        )
                    except InvalidTag as error:
                        raise IntegrityError("encrypted staging authentication failed") from error
                    left = max(offset, start) - start
                    right = min(available_end, end) - start
                    result.extend(plaintext[left:right])
            return bytes(result)

    def finalize(self):
        with self._lock:
            if self._offset != self.expected_size:
                self.abort()
                raise IntegrityError("received size does not match the declared size")
            if self._hash.hexdigest() != self.expected_sha256:
                self.abort()
                raise IntegrityError("received SHA-256 does not match the manifest")
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            destination = (
                self.root / "completed" / self.job_id
                / f"{secrets.token_hex(16)}.cache"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(self.partial_path, destination)
            self.partial_path.unlink()
            self.storage_path = destination
            self._finalized = True
            return self

    def abort(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()
            self.storage_path.unlink(missing_ok=True)
            if self.storage_path != self.partial_path:
                self.partial_path.unlink(missing_ok=True)
