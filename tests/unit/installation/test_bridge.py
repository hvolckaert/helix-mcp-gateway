"""Tests for atomic compilation of the packaged Java bridge."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from helix_mcp.clients.arapi import ArapiLibraries
from helix_mcp.installation import BridgeBuildError, build_bridge


def _libraries(root: Path) -> ArapiLibraries:
    return ArapiLibraries(
        arapi=root / "arapi.jar",
        arapiext=root / "arapiext.jar",
        arlogger=root / "arlogger.jar",
    )


def test_build_bridge_atomically_replaces_the_previous_jar(
    tmp_path,
    monkeypatch,
) -> None:
    output = tmp_path / "bridge" / "helix-arapi-bridge.jar"
    output.parent.mkdir()
    output.write_bytes(b"previous-bridge")
    monkeypatch.setattr(
        "helix_mcp.installation.bridge.validate_arapi_libraries",
        lambda directory: _libraries(directory),
    )
    monkeypatch.setattr(
        "helix_mcp.installation.bridge.shutil.which",
        lambda executable: "/safe/java",
    )
    monkeypatch.setattr(
        "helix_mcp.installation.bridge.version",
        lambda distribution: "0.4.0",
    )

    def fake_run(java: str, arguments: tuple[str, ...]) -> None:
        assert java == "/safe/java"
        if "jdk.compiler/com.sun.tools.javac.Main" in arguments:
            classes = Path(arguments[arguments.index("-d") + 1])
            class_file = classes / "com/example/helix/bridge/ArapiBridge.class"
            class_file.parent.mkdir(parents=True)
            class_file.write_bytes(b"compiled")
            return
        candidate = Path(arguments[arguments.index("--file") + 1])
        with zipfile.ZipFile(candidate, "w") as archive:
            archive.writestr(
                "com/example/helix/bridge/ArapiBridge.class",
                b"compiled",
            )

    monkeypatch.setattr(
        "helix_mcp.installation.bridge._run_java",
        fake_run,
    )

    result = build_bridge(tmp_path / "lib", output)

    assert result.output_path == output
    assert result.package_version == "0.4.0"
    assert len(result.source_sha256) == 64
    with zipfile.ZipFile(output) as archive:
        assert "com/example/helix/bridge/ArapiBridge.class" in (
            archive.namelist()
        )


def test_build_failure_preserves_the_previous_bridge(
    tmp_path,
    monkeypatch,
) -> None:
    output = tmp_path / "helix-arapi-bridge.jar"
    output.write_bytes(b"previous-bridge")
    monkeypatch.setattr(
        "helix_mcp.installation.bridge.validate_arapi_libraries",
        lambda directory: _libraries(directory),
    )
    monkeypatch.setattr(
        "helix_mcp.installation.bridge.shutil.which",
        lambda executable: "/safe/java",
    )
    monkeypatch.setattr(
        "helix_mcp.installation.bridge._run_java",
        lambda java, arguments: (_ for _ in ()).throw(
            BridgeBuildError("safe failure")
        ),
    )

    with pytest.raises(BridgeBuildError):
        build_bridge(tmp_path / "lib", output)

    assert output.read_bytes() == b"previous-bridge"
