"""Tests for ownership of the managed Java ARAPI bridge."""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

from helix_mcp.clients.arapi import (
    ArapiBridgeProcess,
    ArapiRuntimeInvalidError,
    ArapiRuntimeMissingError,
    ArapiRuntimeVersionError,
)
from helix_mcp.config import RuntimeSettings


def run(coroutine):
    return asyncio.run(coroutine)


def settings(
    tmp_path: Path,
    *,
    arapi_version: str = "21.30.07-SNAPSHOT",
    arapi_vendor: str = "BMC Software",
) -> RuntimeSettings:
    jar = tmp_path / "bridge.jar"
    jar.write_bytes(b"test")
    libraries = tmp_path / "lib"
    libraries.mkdir()
    for name in (
        "arapi2130_build007.jar",
        "arapiext2130_build007.jar",
        "arlogger-21.30.07-SNAPSHOT.jar",
    ):
        _write_bmc_jar(
            libraries / name,
            version=arapi_version,
            vendor=arapi_vendor,
        )
    return RuntimeSettings(
        config_path=tmp_path / "helix.yaml",
        arapi_bridge_jar_path=jar,
        arapi_lib_dir=libraries,
    )


def test_disabled_bridge_never_starts_a_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not be created")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    manager = ArapiBridgeProcess(settings(tmp_path), ())

    run(manager.start())

    assert manager.owned is False


def test_healthy_external_bridge_is_not_owned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def healthy(self) -> bool:
        return True

    async def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not be created")

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.shutil.which",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8090/",),
    )

    run(manager.start())
    run(manager.aclose())

    assert manager.owned is False


def test_owned_bridge_is_terminated_on_close(
    tmp_path: Path,
    monkeypatch,
) -> None:
    health_checks = iter((False, True))

    async def healthy(self) -> bool:
        return next(health_checks)

    class FakeProcess:
        returncode = None
        terminated = False

        def terminate(self) -> None:
            self.terminated = True

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    process = FakeProcess()

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.shutil.which",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8090/",),
    )

    run(manager.start())
    assert manager.owned is True
    run(manager.aclose())

    assert manager.owned is False
    assert process.terminated is True


def test_unavailable_java_fails_before_process_creation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def unhealthy(self) -> bool:
        return False

    async def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not be created")

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", unhealthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.shutil.which",
        lambda executable: None,
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeMissingError, match="Java runtime"):
        run(manager.start())

    assert manager.owned is False


def test_missing_primary_arapi_library_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def healthy(self) -> bool:
        return True

    runtime = settings(tmp_path)
    assert runtime.arapi_lib_dir is not None
    (runtime.arapi_lib_dir / "arapiext2130_build007.jar").unlink()
    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.shutil.which",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        runtime,
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeMissingError) as exc_info:
        run(manager.start())

    assert exc_info.value.code == "ARAPI_RUNTIME_MISSING"


def test_unsupported_arapi_version_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = settings(tmp_path, arapi_version="22.10.00")
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.shutil.which",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        runtime,
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeVersionError) as exc_info:
        run(manager.check_startup_requirements())

    assert exc_info.value.code == "ARAPI_RUNTIME_VERSION_UNSUPPORTED"


def test_non_bmc_arapi_manifest_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = settings(tmp_path, arapi_vendor="Unknown vendor")
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.shutil.which",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        runtime,
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeInvalidError) as exc_info:
        run(manager.check_startup_requirements())

    assert exc_info.value.code == "ARAPI_RUNTIME_INVALID"


def _write_bmc_jar(path: Path, *, version: str, vendor: str) -> None:
    manifest = (
        "Manifest-Version: 1.0\n"
        f"Implementation-Vendor: {vendor}\n"
        f"Implementation-Version: {version}\n"
    )
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", manifest)
