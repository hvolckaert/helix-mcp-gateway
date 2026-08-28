from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from hatch_build import WheelFormatError, normalize_wheel_permissions


def test_normalizes_regular_files_and_directories(tmp_path: Path) -> None:
    wheel = tmp_path / "example.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        directory = zipfile.ZipInfo("package/")
        directory.create_system = 3
        directory.external_attr = (stat.S_IFDIR | 0o777) << 16
        archive.writestr(directory, b"")
        source = zipfile.ZipInfo("package/module.py")
        source.create_system = 3
        source.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(source, b"VALUE = 1\n")

    normalize_wheel_permissions(wheel)

    with zipfile.ZipFile(wheel) as archive:
        directory = archive.getinfo("package/")
        source = archive.getinfo("package/module.py")
        assert stat.S_IMODE(directory.external_attr >> 16) == 0o755
        assert stat.S_IMODE(source.external_attr >> 16) == 0o644
        assert archive.read(source) == b"VALUE = 1\n"


def test_normalization_is_idempotent(tmp_path: Path) -> None:
    wheel = tmp_path / "example.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("package/module.py", b"VALUE = 1\n")

    normalize_wheel_permissions(wheel)
    first = wheel.read_bytes()
    normalize_wheel_permissions(wheel)

    assert wheel.read_bytes() == first


def test_rejects_invalid_wheel_without_replacing_it(tmp_path: Path) -> None:
    wheel = tmp_path / "invalid.whl"
    wheel.write_bytes(b"not a ZIP archive")

    with pytest.raises(WheelFormatError):
        normalize_wheel_permissions(wheel)

    assert wheel.read_bytes() == b"not a ZIP archive"
