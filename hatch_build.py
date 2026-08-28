"""Hatch build hooks for deterministic distribution artifacts."""

from __future__ import annotations

import os
import stat
import struct
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_END_OF_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x05\x06"
_END_OF_CENTRAL_DIRECTORY = struct.Struct("<4s4H2LH")
_CENTRAL_DIRECTORY_HEADER_SIZE = 46
_ZIP64_UINT16 = 0xFFFF
_ZIP64_UINT32 = 0xFFFFFFFF


class WheelFormatError(ValueError):
    """Raised when a wheel cannot be normalized safely."""


def normalize_wheel_permissions(artifact_path: str | Path) -> None:
    """Normalize ZIP entry modes without recompressing wheel contents."""

    path = Path(artifact_path)
    original_stat = path.stat()
    archive = bytearray(path.read_bytes())
    entries = _central_directory_entries(archive)
    for offset, name in entries:
        is_directory = name.endswith(b"/")
        mode = stat.S_IFDIR | 0o755 if is_directory else stat.S_IFREG | 0o644
        archive[offset + 5] = 3  # ZIP creator system: Unix.
        attributes = (mode << 16) | (0x10 if is_directory else 0)
        struct.pack_into("<L", archive, offset + 38, attributes)
    _atomic_replace(path, archive, stat.S_IMODE(original_stat.st_mode))


def _central_directory_entries(
    archive: bytearray,
) -> tuple[tuple[int, bytes], ...]:
    eocd_offset = archive.rfind(_END_OF_CENTRAL_DIRECTORY_SIGNATURE)
    if eocd_offset < 0:
        raise WheelFormatError("wheel has no end-of-central-directory record")
    if eocd_offset + _END_OF_CENTRAL_DIRECTORY.size > len(archive):
        raise WheelFormatError("wheel has a truncated end record")
    (
        signature,
        disk_number,
        central_disk,
        entries_on_disk,
        total_entries,
        central_size,
        central_offset,
        comment_length,
    ) = _END_OF_CENTRAL_DIRECTORY.unpack_from(archive, eocd_offset)
    if signature != _END_OF_CENTRAL_DIRECTORY_SIGNATURE:
        raise WheelFormatError("wheel has an invalid end record")
    if (
        disk_number != 0
        or central_disk != 0
        or entries_on_disk != total_entries
    ):
        raise WheelFormatError("multi-disk wheels are not supported")
    if (
        total_entries == _ZIP64_UINT16
        or central_size == _ZIP64_UINT32
        or central_offset == _ZIP64_UINT32
    ):
        raise WheelFormatError("ZIP64 wheels are not supported")
    if eocd_offset + _END_OF_CENTRAL_DIRECTORY.size + comment_length != len(
        archive
    ):
        raise WheelFormatError("wheel has trailing or truncated data")
    central_end = central_offset + central_size
    if central_end != eocd_offset or central_end > len(archive):
        raise WheelFormatError("wheel has an invalid central directory")

    entries: list[tuple[int, bytes]] = []
    cursor = central_offset
    while cursor < central_end:
        if (
            cursor + _CENTRAL_DIRECTORY_HEADER_SIZE > central_end
            or archive[cursor : cursor + 4] != _CENTRAL_DIRECTORY_SIGNATURE
        ):
            raise WheelFormatError("wheel has an invalid directory entry")
        name_length, extra_length, entry_comment_length = struct.unpack_from(
            "<HHH",
            archive,
            cursor + 28,
        )
        entry_end = (
            cursor
            + _CENTRAL_DIRECTORY_HEADER_SIZE
            + name_length
            + extra_length
            + entry_comment_length
        )
        if name_length == 0 or entry_end > central_end:
            raise WheelFormatError("wheel has a truncated directory entry")
        name_start = cursor + _CENTRAL_DIRECTORY_HEADER_SIZE
        name = bytes(archive[name_start : name_start + name_length])
        entries.append((cursor, name))
        cursor = entry_end
    if cursor != central_end or len(entries) != total_entries:
        raise WheelFormatError("wheel directory entry count does not match")
    return tuple(entries)


def _atomic_replace(
    path: Path, contents: bytes | bytearray, mode: int
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


class CustomBuildHook(BuildHookInterface[Any]):
    """Normalize wheel metadata after Hatchling finishes the artifact."""

    PLUGIN_NAME = "custom"

    def finalize(
        self,
        version: str,
        build_data: dict[str, Any],
        artifact_path: str,
    ) -> None:
        del version, build_data
        if self.target_name == "wheel":
            normalize_wheel_permissions(artifact_path)
