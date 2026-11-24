import re
from pathlib import PureWindowsPath

from .models import ItemType, Manifest


MAX_FILE_COUNT = 100_000
MAX_PATH_LENGTH = 255
MAX_PATH_DEPTH = 32
MAX_FILE_SIZE = 1 << 40
MAX_TOTAL_SIZE = 1 << 40
TRANSFER_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")

_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ValidationError(ValueError):
    pass


def validate_transfer_id(value, label="job ID"):
    if not isinstance(value, str) or TRANSFER_ID_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{label} must be 32 lowercase hexadecimal characters")
    return value


def validate_relative_path(value: str):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError("path must be a non-empty string without NUL bytes")
    if len(value) > MAX_PATH_LENGTH:
        raise ValidationError("path exceeds the maximum length")

    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive or value.startswith(("/", "\\")):
        raise ValidationError("path must be relative and cannot include a drive or UNC root")

    parts = tuple(part for part in re.split(r"[\\/]", value) if part)
    if not parts or len(parts) > MAX_PATH_DEPTH:
        raise ValidationError("path has an invalid depth")
    for part in parts:
        if part in (".", ".."):
            raise ValidationError("path cannot contain traversal segments")
        if ":" in part:
            raise ValidationError("path cannot contain a drive or alternate data stream")
        if part.endswith((" ", ".")):
            raise ValidationError("path segments cannot end in a space or period")
        if part.split(".", 1)[0].upper() in _RESERVED_NAMES:
            raise ValidationError("path contains a reserved Windows name")
    return "/".join(parts)


def validate_manifest(manifest: Manifest):
    validate_transfer_id(manifest.job_id)
    if not manifest.items:
        raise ValidationError("manifest must contain at least one item")
    if len(manifest.items) > MAX_FILE_COUNT:
        raise ValidationError("manifest exceeds the file-count limit")

    seen_paths = set()
    total_size = 0
    file_count = 0
    for item in manifest.items:
        normalized = validate_relative_path(item.relative_path)
        collision_key = normalized.casefold()
        if collision_key in seen_paths:
            raise ValidationError("manifest contains a duplicate path")
        seen_paths.add(collision_key)
        if item.size < 0 or item.size > MAX_FILE_SIZE:
            raise ValidationError("item size is outside the allowed range")
        if item.item_type is ItemType.DIRECTORY and item.size != 0:
            raise ValidationError("directory size must be zero")
        if item.item_type is ItemType.FILE:
            if not isinstance(item.sha256, str) or len(item.sha256) != 64 or any(
                character not in "0123456789abcdef" for character in item.sha256.lower()
            ):
                raise ValidationError("file SHA-256 must contain 64 hexadecimal characters")
            total_size += item.size
            file_count += 1

    if total_size != manifest.total_size:
        raise ValidationError("manifest total size does not match its items")
    if file_count != manifest.file_count:
        raise ValidationError("manifest file count does not match its items")
    if total_size > MAX_TOTAL_SIZE:
        raise ValidationError("manifest exceeds the total-size limit")
    return manifest
