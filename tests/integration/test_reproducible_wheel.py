from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUILD_INPUTS = (
    "LICENSE",
    "README.md",
    "hatch_build.py",
    "pyproject.toml",
    "src",
    "arapi-bridge/src/main/java/com/example/helix/bridge/ArapiBridge.java",
    "config/helix.example.yaml",
)


@pytest.mark.integration
def test_wheel_is_reproducible_across_source_file_modes(
    tmp_path: Path,
) -> None:
    regular_root = _copy_build_inputs(tmp_path / "regular")
    executable_root = _copy_build_inputs(tmp_path / "executable")
    _set_packaged_file_modes(executable_root, 0o755)

    regular_wheel = _build_wheel(regular_root)
    executable_wheel = _build_wheel(executable_root)

    assert regular_wheel.read_bytes() == executable_wheel.read_bytes()
    with zipfile.ZipFile(regular_wheel) as archive:
        for info in archive.infolist():
            if not info.is_dir():
                assert stat.S_IMODE(info.external_attr >> 16) == 0o644


def _copy_build_inputs(destination: Path) -> Path:
    for relative in _BUILD_INPUTS:
        source = PROJECT_ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return destination


def _set_packaged_file_modes(root: Path, mode: int) -> None:
    packaged_paths = (
        root / "src",
        root
        / "arapi-bridge/src/main/java/com/example/helix/bridge/ArapiBridge.java",
        root / "config/helix.example.yaml",
    )
    for packaged_path in packaged_paths:
        if packaged_path.is_dir():
            for path in packaged_path.rglob("*"):
                if path.is_file():
                    path.chmod(mode)
        else:
            packaged_path.chmod(mode)


def _build_wheel(root: Path) -> Path:
    output = root / "dist"
    environment = dict(os.environ)
    environment["SOURCE_DATE_EPOCH"] = "1700000000"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(output),
        ],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = tuple(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]
