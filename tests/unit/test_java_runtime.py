"""Selected Java folders must support the packaged bridge build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import helix_mcp.java_runtime as java_runtime


def _java_home(root: Path, name: str, version: str, modules: str) -> Path:
    home = root / name
    executable = home / "bin" / ("java.exe" if os.name == "nt" else "java")
    executable.parent.mkdir(parents=True)
    executable.write_text("java", encoding="utf-8")
    if os.name != "nt":
        executable.chmod(0o700)
    (home / "release").write_text(
        f'JAVA_VERSION="{version}"\nMODULES="{modules}"\n',
        encoding="utf-8",
    )
    return home


def test_discovery_omits_java_11_and_jre_8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_jdk = _java_home(
        tmp_path, "jdk-11", "11.0.1", "java.base jdk.compiler jdk.jartool"
    )
    legacy_jre = _java_home(tmp_path, "jre-8", "1.8.0_401", "java.base")
    supported_jdk = _java_home(
        tmp_path, "jdk-17", "17.0.12", "java.base jdk.compiler jdk.jartool"
    )
    no_compiler = _java_home(
        tmp_path, "jdk-21-runtime", "21.0.12", "java.base"
    )
    monkeypatch.setattr(java_runtime, "_KNOWN_JAVA_ROOTS", (tmp_path,))

    assert java_runtime.find_java_homes() == (supported_jdk.resolve(),)
    for invalid in (legacy_jdk, legacy_jre, no_compiler):
        assert java_runtime.jdk_executable(invalid) is None
    assert java_runtime.jdk_executable(supported_jdk) is not None


def test_inaccessible_selected_java_folder_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "blocked"
    executable = home / "bin" / ("java.exe" if os.name == "nt" else "java")
    original_is_file = Path.is_file

    def is_file_with_denied_path(path: Path) -> bool:
        if path == executable:
            raise PermissionError("access denied")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file_with_denied_path)
    assert java_runtime.jdk_executable(home) is None
