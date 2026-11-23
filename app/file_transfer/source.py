import os
import stat
from dataclasses import dataclass
from pathlib import Path

from blake3 import blake3
import win32con
import win32file

from .compression import MAX_CHUNK_SIZE
from .hashing import new_hasher, validate_hash_algorithm


class SourceChangedError(OSError):
    pass


@dataclass(frozen=True)
class SourceFile:
    path: Path
    size: int
    modified_ns: int
    device: int
    inode: int
    digest: str
    chunk_size: int
    hash_algorithm: str = "sha256"

    @classmethod
    def snapshot(cls, path, chunk_size=MAX_CHUNK_SIZE, *, hash_algorithm="sha256"):
        validate_hash_algorithm(hash_algorithm)
        if chunk_size <= 0 or chunk_size > MAX_CHUNK_SIZE:
            raise ValueError("chunk size must be between 1 byte and 256 KiB")
        resolved = Path(path)
        before = _safe_stat(resolved)
        digest = new_hasher(hash_algorithm)
        with resolved.open("rb") as source:
            if not _same_file(before, os.fstat(source.fileno())):
                raise SourceChangedError("source changed while it was being opened")
            if hash_algorithm == "blake3":
                # Keep a deny-write/delete handle alive throughout the mapping.
                # Metadata checks alone cannot protect a mapping from truncation.
                try:
                    guard = win32file.CreateFile(str(resolved), win32con.GENERIC_READ,
                        win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING, 0, None)
                except Exception as error:
                    raise SourceChangedError("source cannot be locked for hashing") from error
                try:
                    digest = blake3(max_threads=blake3.AUTO)
                    digest.update_mmap(str(resolved))
                finally:
                    guard.Close()
            else:
                while chunk := source.read(chunk_size):
                    digest.update(chunk)
            after = os.fstat(source.fileno())
        if not _same_file(before, after) or not _same_file(before, _safe_stat(resolved)):
            raise SourceChangedError("source changed while it was being hashed")
        return cls(
            path=resolved,
            size=before.st_size,
            modified_ns=before.st_mtime_ns,
            device=before.st_dev,
            inode=before.st_ino,
            digest=digest.hexdigest(),
            chunk_size=chunk_size,
            hash_algorithm=hash_algorithm,
        )

    def iter_chunks(self):
        try:
            current = _safe_stat(self.path)
            with self.path.open("rb") as source:
                opened = os.fstat(source.fileno())
                if not self._matches(current) or not self._matches(opened):
                    raise SourceChangedError("source changed after the manifest was created")
                while chunk := source.read(self.chunk_size):
                    yield chunk
                if not self._matches(os.fstat(source.fileno())):
                    raise SourceChangedError("source changed during transfer")
        except FileNotFoundError as error:
            raise SourceChangedError("source was deleted after the manifest was created") from error

    def _matches(self, value):
        return (
            value.st_size == self.size
            and value.st_mtime_ns == self.modified_ns
            and value.st_dev == self.device
            and value.st_ino == self.inode
        )


def _same_file(left, right):
    return (
        left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


def _safe_stat(path):
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode):
        raise SourceChangedError("symbolic links are not transferable")
    if getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise SourceChangedError("reparse points are not transferable")
    if not stat.S_ISREG(value.st_mode):
        raise SourceChangedError("source must be a regular file")
    return value
