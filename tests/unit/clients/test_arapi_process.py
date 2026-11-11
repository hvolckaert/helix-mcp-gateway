"""Tests for ownership of the managed Java ARAPI bridge."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from helix_mcp.clients.arapi import (
    ArapiBridgeProcess,
    ArapiBridgeProcessError,
    ArapiRuntimeInvalidError,
    ArapiRuntimeMissingError,
    validate_arapi_libraries,
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
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8090/",),
    )

    run(manager.start())
    run(manager.aclose())

    assert manager.owned is False


@pytest.mark.parametrize("pending_checks", (0, 3))
def test_exited_child_reuses_a_concurrent_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pending_checks: int,
) -> None:
    health_checks = 0
    sleeps = 0

    class ExitedProcess:
        returncode = 1

        async def wait(self) -> int:
            return self.returncode

        def terminate(self) -> None:
            raise AssertionError("the exited child must not be terminated")

    async def create(*args, **kwargs):
        return ExitedProcess()

    async def healthy(self) -> bool:
        nonlocal health_checks
        health_checks += 1
        # The first check precedes process creation. The other client may
        # still be starting its listener when our child loses the bind race.
        return health_checks > 1 + pending_checks

    async def sleep(delay: float) -> None:
        nonlocal sleeps
        sleeps += 1

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8097/",),
        bridge_token="bridge-test-token",
    )

    run(manager.start())
    assert manager.owned is False
    run(manager.aclose())
    assert health_checks == pending_checks + 2
    assert sleeps == pending_checks


class StartupChild:
    returncode: int | None = None
    terminated = False

    def terminate(self) -> None:
        assert self.returncode is None
        self.terminated = True
        self.returncode = -15

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode


def test_concurrent_readiness_checks_create_only_one_owned_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    children: list[StartupChild] = []

    async def healthy(self) -> bool:
        return bool(children)

    async def create(*args, **kwargs):
        await asyncio.sleep(0)
        child = StartupChild()
        children.append(child)
        return child

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path), ("http://127.0.0.1:8097/",)
    )

    async def scenario() -> None:
        await asyncio.gather(*(manager.start() for _ in range(8)))
        assert len(children) == 1
        assert manager.owned is True
        await manager.aclose()
        assert children[0].terminated is True
        with pytest.raises(ArapiBridgeProcessError, match="closed"):
            await manager.start()
        assert len(children) == 1

    run(scenario())


def test_exited_owned_child_can_be_replaced_without_leaking_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    children: list[StartupChild] = []

    async def healthy(self) -> bool:
        return bool(children) and children[-1].returncode is None

    async def create(*args, **kwargs):
        child = StartupChild()
        children.append(child)
        return child

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path), ("http://127.0.0.1:8097/",)
    )
    run(manager.start())
    children[0].returncode = -9
    run(manager.start())
    assert len(children) == 2
    assert manager.owned is True
    run(manager.aclose())
    assert children[0].terminated is False
    assert children[1].terminated is True


@pytest.fixture
def starting_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    child = StartupChild()

    async def create(*args, **kwargs):
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8097/",),
        bridge_token="bridge-test-token",
    )
    return manager, child


def test_running_unhealthy_child_is_not_killed_or_replaced(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, child = starting_bridge
    health_checks = iter((False, True, False))

    async def healthy(self) -> bool:
        return next(health_checks)

    async def forbidden(*args, **kwargs):
        raise AssertionError("a live child must not be replaced")

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    run(manager.start())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(ArapiBridgeProcessError, match="running ARAPI"):
        run(manager.start())
    assert manager.owned is True
    assert child.terminated is False
    run(manager.aclose())
    assert child.terminated is True


def test_dead_owned_child_is_released_when_another_bridge_is_ready(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, child = starting_bridge
    health_checks = iter((False, True, True))

    async def healthy(self) -> bool:
        return next(health_checks)

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    run(manager.start())
    child.returncode = -9
    run(manager.start())
    assert manager.owned is False
    run(manager.aclose())
    assert child.terminated is False


def test_close_waits_for_startup_and_prevents_later_recovery(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, child = starting_bridge

    async def scenario() -> None:
        polling = asyncio.Event()
        allow_ready = asyncio.Event()
        checks = 0

        async def healthy(self) -> bool:
            nonlocal checks
            checks += 1
            if checks == 1:
                return False
            polling.set()
            await allow_ready.wait()
            return True

        monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
        async with asyncio.timeout(5):
            start = asyncio.create_task(manager.start())
            await polling.wait()
            close = asyncio.create_task(manager.aclose())
            await asyncio.sleep(0)
            assert child.terminated is False
            allow_ready.set()
            await asyncio.gather(start, close)
            assert child.terminated is True
            assert manager.owned is False
            with pytest.raises(ArapiBridgeProcessError, match="closed"):
                await manager.start()

    run(scenario())


def test_child_exiting_during_health_check_is_not_owned(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, child = starting_bridge
    checks = 0

    async def healthy(self) -> bool:
        nonlocal checks
        checks += 1
        if checks == 1:
            return False
        child.returncode = 1
        return True

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    run(manager.start())
    assert manager.owned is False
    run(manager.aclose())
    assert child.terminated is False


@pytest.mark.parametrize("child_exited", (False, True))
def test_unavailable_bridge_has_bounded_retries_and_cleans_up(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
    child_exited: bool,
) -> None:
    manager, child = starting_bridge
    child.returncode = 1 if child_exited else None
    checks = 0

    async def unhealthy(self) -> bool:
        nonlocal checks
        checks += 1
        assert checks < 100, "readiness retries must be bounded"
        return False

    async def sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", unhealthy)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(ArapiBridgeProcessError) as failure:
        run(manager.start())

    assert 1 < checks < 100
    assert failure.value.code == "ARAPI_BRIDGE_PROCESS_ERROR"
    assert ("process exited" in str(failure.value)) is child_exited
    assert manager.owned is False
    assert child.terminated is (not child_exited)


@pytest.mark.parametrize("proof", ("valid", "invalid", "missing"))
def test_concurrent_bridge_still_requires_authenticated_health(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
    proof: str,
) -> None:
    manager, child = starting_bridge
    child.returncode = 1
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert requests < 100, "authentication failures must stop retrying"
        if requests == 1:
            raise httpx.ConnectError("listener is not ready", request=request)
        challenge = request.headers["X-Helix-Bridge-Challenge"]
        token = b"bridge-test-token" if proof == "valid" else b"another-token"
        signature = (
            base64.urlsafe_b64encode(
                hmac.new(token, challenge.encode(), hashlib.sha256).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        headers = (
            {} if proof == "missing" else {"X-Helix-Bridge-Proof": signature}
        )
        return httpx.Response(200, json={"status": "ok"}, headers=headers)

    async_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    async def sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    if proof == "valid":
        run(manager.start())
    else:
        with pytest.raises(ArapiBridgeProcessError):
            run(manager.start())
    run(manager.aclose())
    assert requests > 1
    assert manager.owned is False
    assert child.terminated is False


def test_cancelled_readiness_wait_cleans_up_owned_child(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, child = starting_bridge

    async def scenario() -> None:
        polling = asyncio.Event()
        checks = 0

        async def healthy(self) -> bool:
            nonlocal checks
            checks += 1
            if checks == 1:
                return False
            polling.set()
            await asyncio.Future()
            raise AssertionError("readiness wait should be cancelled")

        monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
        task = asyncio.create_task(manager.start())
        try:
            await asyncio.wait_for(polling.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert manager.owned is False
            assert child.terminated is True
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()

    run(scenario())


def test_child_exit_during_cleanup_does_not_hide_startup_failure(
    starting_bridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, child = starting_bridge

    async def unhealthy(self) -> bool:
        return False

    async def sleep(delay: float) -> None:
        pass

    def terminate() -> None:
        child.returncode = 1
        raise ProcessLookupError

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", unhealthy)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(child, "terminate", terminate)
    with pytest.raises(ArapiBridgeProcessError):
        run(manager.start())
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
    captured: dict[str, object] = {}

    async def create(*args, **kwargs):
        captured["environment"] = kwargs["env"]
        captured["cwd"] = Path(kwargs["cwd"])
        (Path(kwargs["cwd"]) / "RegKey.reg").write_text(
            "BMC runtime artifact", encoding="utf-8"
        )
        return process

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8097/",),
        bridge_token="bridge-test-token",
    )

    run(manager.start())
    assert manager.owned is True
    run(manager.aclose())

    assert manager.owned is False
    assert process.terminated is True
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["HELIX_ARAPI_BRIDGE_HOST"] == "127.0.0.1"
    assert environment["HELIX_ARAPI_BRIDGE_PORT"] == "8097"
    assert environment["HELIX_ARAPI_BRIDGE_TOKEN"] == "bridge-test-token"
    working_directory = captured["cwd"]
    assert working_directory == tmp_path / "runtime"
    assert (working_directory / "RegKey.reg").is_file()
    assert not (tmp_path / "RegKey.reg").exists()
    if os.name != "nt":
        assert stat.S_IMODE(working_directory.stat().st_mode) == 0o700


def test_bridge_rejects_a_non_directory_runtime_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "runtime").write_text("occupied", encoding="utf-8")

    async def unhealthy(self) -> bool:
        return False

    async def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not be created")

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", unhealthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8097/",),
    )

    with pytest.raises(ArapiBridgeProcessError):
        run(manager.start())


def test_owned_bridge_uses_selected_jdk_folder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    jdk = tmp_path / "jdk"
    executable = jdk / "bin" / ("java.exe" if os.name == "nt" else "java")
    executable.parent.mkdir(parents=True)
    executable.write_text("java", encoding="utf-8")
    executable.chmod(0o700)
    (jdk / "release").write_text(
        'JAVA_VERSION="17.0.1"\n'
        'MODULES="java.base jdk.compiler jdk.jartool"\n',
        encoding="utf-8",
    )
    runtime = settings(tmp_path).model_copy(update={"java_home": jdk})
    health_checks = iter((False, True))

    class FakeProcess:
        returncode = None

        def terminate(self) -> None:
            return None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    captured: list[str] = []

    async def create(*args, **kwargs):
        captured.append(args[0])
        return FakeProcess()

    async def healthy(self) -> bool:
        return next(health_checks)

    monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    manager = ArapiBridgeProcess(runtime, ("http://127.0.0.1:8097/",))

    run(manager.start())
    run(manager.aclose())

    assert captured == [str(executable)]


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
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: None,
    )
    manager = ArapiBridgeProcess(
        settings(tmp_path),
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeMissingError, match="JDK 17"):
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
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        runtime,
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeMissingError) as exc_info:
        run(manager.start())

    assert exc_info.value.code == "ARAPI_RUNTIME_MISSING"


@pytest.mark.parametrize(
    "arapi_version",
    (
        "7.6.03",
        "21.30.07-SNAPSHOT",
        "25.1.00-SNAPSHOT",
        "26.1.00",
        "99.4.00-future",
    ),
)
def test_bmc_arapi_versions_are_accepted_by_capability(
    tmp_path: Path,
    monkeypatch,
    arapi_version: str,
) -> None:
    runtime = settings(tmp_path, arapi_version=arapi_version)
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        runtime,
        ("http://127.0.0.1:8090/",),
    )

    run(manager.check_startup_requirements())
    assert runtime.arapi_lib_dir is not None
    assert validate_arapi_libraries(runtime.arapi_lib_dir).version == (
        arapi_version
    )


def test_non_bmc_arapi_manifest_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = settings(tmp_path, arapi_vendor="Unknown vendor")
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        runtime,
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeInvalidError) as exc_info:
        run(manager.check_startup_requirements())

    assert exc_info.value.code == "ARAPI_RUNTIME_INVALID"


def test_arapi_without_required_classes_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = settings(tmp_path)
    assert runtime.arapi_lib_dir is not None
    primary = runtime.arapi_lib_dir / "arapi2130_build007.jar"
    with zipfile.ZipFile(primary, mode="w") as archive:
        archive.writestr(
            "META-INF/MANIFEST.MF",
            "Manifest-Version: 1.0\n"
            "Implementation-Vendor: BMC Software\n"
            "Implementation-Version: 26.1.00\n",
        )
        archive.writestr("com/bmc/arsys/api/ARServerUser.class", b"test")
    monkeypatch.setattr(
        "helix_mcp.clients.arapi.process.jdk_executable",
        lambda executable: "/runtime/java",
    )
    manager = ArapiBridgeProcess(
        runtime,
        ("http://127.0.0.1:8090/",),
    )

    with pytest.raises(ArapiRuntimeInvalidError, match="required Java API"):
        run(manager.check_startup_requirements())


def _write_bmc_jar(path: Path, *, version: str, vendor: str) -> None:
    manifest = (
        "Manifest-Version: 1.0\n"
        f"Implementation-Vendor: {vendor}\n"
        f"Implementation-Version: {version}\n"
    )
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", manifest)
        if path.name.startswith("arapi") and not path.name.startswith(
            "arapiext"
        ):
            for name in (
                "ARException",
                "ARErrors",
                "ARServerUser",
                "Constants",
                "DataType",
                "DateInfo",
                "Entry",
                "Field",
                "OutputInteger",
                "QualifierInfo",
                "SQLResult",
                "ServerInfoMap",
                "SortInfo",
                "StatusInfo",
                "Time",
                "Timestamp",
                "Value",
            ):
                archive.writestr(f"com/bmc/arsys/api/{name}.class", b"test")
