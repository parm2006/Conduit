import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


class ItemType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class FileItem:
    relative_path: str
    item_type: ItemType
    size: int
    modified_ns: int
    sha256: Optional[str] = None
    local_source_path: Optional[str] = field(default=None, repr=False, compare=False)

    def to_wire(self):
        return {
            "relative_path": self.relative_path,
            "item_type": self.item_type.value,
            "size": self.size,
            "modified_ns": self.modified_ns,
            "sha256": self.sha256,
        }

    @classmethod
    def from_wire(cls, value):
        return cls(
            relative_path=value["relative_path"],
            item_type=ItemType(value["item_type"]),
            size=value["size"],
            modified_ns=value["modified_ns"],
            sha256=value.get("sha256"),
        )


@dataclass(frozen=True)
class Manifest:
    job_id: str
    items: tuple[FileItem, ...]
    total_size: int
    file_count: int

    @classmethod
    def create(cls, items: Iterable[FileItem]):
        frozen_items = tuple(items)
        return cls(
            job_id=secrets.token_hex(16),
            items=frozen_items,
            total_size=sum(item.size for item in frozen_items if item.item_type is ItemType.FILE),
            file_count=sum(item.item_type is ItemType.FILE for item in frozen_items),
        )

    def to_wire(self):
        return {
            "job_id": self.job_id,
            "items": [item.to_wire() for item in self.items],
            "total_size": self.total_size,
            "file_count": self.file_count,
        }

    @classmethod
    def from_wire(cls, value):
        return cls(
            job_id=value["job_id"],
            items=tuple(FileItem.from_wire(item) for item in value["items"]),
            total_size=value["total_size"],
            file_count=value["file_count"],
        )
