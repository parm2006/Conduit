import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .compression import MAX_CHUNK_SIZE


class SourceChangedError(OSError):
    pass


@dataclass(frozen=True)
class SourceFile:
    path: Path
    size: int
    modified_ns: int
    device: int
    inode: int
    sha256: str
    chunk_size: int

    @classmethod
    def snapshot(cls, path, chunk_size=MAX_CHUNK_SIZE):
        if chunk_size <= 0 or chunk_size > MAX_CHUNK_SIZE:
            raise ValueError("chunk size must be between 1 byte and 1 MiB")
        resolved = Path(path)
        before = _safe_stat(resolved)
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            if not _same_file(before, os.fstat(source.fileno())):
                raise SourceChangedError("source changed while it was being opened")
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
            sha256=digest.hexdigest(),
            chunk_size=chunk_size,
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
