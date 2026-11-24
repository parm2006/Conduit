import stat
from pathlib import Path

from .models import FileItem, ItemType, Manifest
from .source import SourceChangedError, SourceFile
from .validation import validate_manifest


def snapshot_selection(paths):
    items = []
    sources = {}
    visited_directories = set()
    for selected in paths:
        selected = Path(selected)
        if selected.is_dir():
            _snapshot_directory(
                selected, selected.name, items, sources, visited_directories
            )
        else:
            _snapshot_file(selected, selected.name, items, sources)
    manifest = Manifest.create(items)
    validate_manifest(manifest)
    return manifest, sources


def _snapshot_directory(path, relative_path, items, sources, visited_directories):
    directory_stat = _safe_directory_stat(path)
    identity = (directory_stat.st_dev, directory_stat.st_ino)
    if identity in visited_directories:
        raise SourceChangedError("directory cycle or duplicate identity detected")
    visited_directories.add(identity)
    items.append(
        FileItem(relative_path, ItemType.DIRECTORY, 0, directory_stat.st_mtime_ns)
    )
    for child in sorted(path.iterdir(), key=lambda value: value.name.casefold()):
        child_relative = f"{relative_path}/{child.name}"
        if child.is_dir():
            _snapshot_directory(
                child, child_relative, items, sources, visited_directories
            )
        else:
            _snapshot_file(child, child_relative, items, sources)
    current = _safe_directory_stat(path)
    if (current.st_dev, current.st_ino) != identity:
        raise SourceChangedError("directory changed while it was being inspected")


def _snapshot_file(path, relative_path, items, sources):
    source = SourceFile.snapshot(path)
    items.append(
        FileItem(
            relative_path,
            ItemType.FILE,
            source.size,
            source.modified_ns,
            source.sha256,
            str(path),
        )
    )
    sources[relative_path] = source


def _safe_directory_stat(path):
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode):
        raise SourceChangedError("symbolic-link directories are not transferable")
    if getattr(value, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        raise SourceChangedError("reparse-point directories are not transferable")
    if not stat.S_ISDIR(value.st_mode):
        raise SourceChangedError("source must be a real directory")
    return value
