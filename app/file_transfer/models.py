import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional
from .hashing import validate_hash_algorithm


class ItemType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class FileItem:
    relative_path: str
    item_type: ItemType
    size: int
    modified_ns: int
    digest: Optional[str] = None
    local_source_path: Optional[str] = field(default=None, repr=False, compare=False)

    def to_wire(self, hash_algorithm="sha256"):
        validate_hash_algorithm(hash_algorithm)
        return {
            "relative_path": self.relative_path,
            "item_type": self.item_type.value,
            "size": self.size,
            "modified_ns": self.modified_ns,
            "sha256" if hash_algorithm == "sha256" else "digest": self.digest,
        }

    @classmethod
    def from_wire(cls, value, hash_algorithm="sha256"):
        validate_hash_algorithm(hash_algorithm)
        digest_key = "sha256" if hash_algorithm == "sha256" else "digest"
        other_key = "digest" if hash_algorithm == "sha256" else "sha256"
        if other_key in value:
            raise ValueError("ambiguous file digest fields")
        return cls(
            relative_path=value["relative_path"],
            item_type=ItemType(value["item_type"]),
            size=value["size"],
            modified_ns=value["modified_ns"],
            digest=value.get(digest_key),
        )


@dataclass(frozen=True)
class Manifest:
    job_id: str
    items: tuple[FileItem, ...]
    total_size: int
    file_count: int
    hash_algorithm: str = "sha256"

    @classmethod
    def create(cls, items: Iterable[FileItem], hash_algorithm="sha256"):
        validate_hash_algorithm(hash_algorithm)
        frozen_items = tuple(items)
        return cls(
            job_id=secrets.token_hex(16),
            items=frozen_items,
            total_size=sum(item.size for item in frozen_items if item.item_type is ItemType.FILE),
            file_count=sum(item.item_type is ItemType.FILE for item in frozen_items),
            hash_algorithm=hash_algorithm,
        )

    def to_wire(self):
        validate_hash_algorithm(self.hash_algorithm)
        value = {
            "job_id": self.job_id,
            "items": [item.to_wire(self.hash_algorithm) for item in self.items],
            "total_size": self.total_size,
            "file_count": self.file_count,
        }
        if self.hash_algorithm != "sha256":
            value["hash_algorithm"] = self.hash_algorithm
        return value

    @classmethod
    def from_wire(cls, value):
        algorithm = validate_hash_algorithm(value.get("hash_algorithm", "sha256"))
        return cls(
            job_id=value["job_id"],
            items=tuple(FileItem.from_wire(item, algorithm) for item in value["items"]),
            total_size=value["total_size"],
            file_count=value["file_count"],
            hash_algorithm=algorithm,
        )
