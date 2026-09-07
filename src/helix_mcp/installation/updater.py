"""Verified, versioned updates for managed gateway installations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from helix_mcp.config import (
    RuntimeSettings,
    load_runtime_settings,
    load_single_instance_config,
)
from helix_mcp.installation.managed import (
    ManagedInstallation,
    activate_managed_installation,
    installation_metadata_path,
    load_managed_installation,
    stable_dashboard_launcher_path,
    stable_launcher_path,
    supports_transactional_updates,
    versioned_runtime_paths,
)
from helix_mcp.installation.openclaw import EXPOSED_TOOLS

DEFAULT_REPOSITORY = "hvolckaert/helix-mcp-gateway"
DEFAULT_OPENCLAW_SERVER_NAME = "helix"
_VERSION_PATTERN = re.compile(
    r"^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
)
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class UpdateError(RuntimeError):
    """A managed update failed without exposing command output or secrets."""

    code = "MANAGED_UPDATE_ERROR"


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    tag: str
    wheel_name: str
    sha256: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseStatus:
    status: str
    repository: str
    current_version: str
    latest_version: str | None
    update_available: bool | None
    release_url: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "repository": self.repository,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "release_url": self.release_url,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class UpdateResult:
    status: str
    current_version: str
    target_version: str
    target_runtime: Path
    wheel: Path
    backup: Path
    sha256: str
    client_integration: str
    openclaw_reloaded: bool = False
    openclaw_probed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "target_runtime": str(self.target_runtime),
            "wheel": str(self.wheel),
            "backup": str(self.backup),
            "sha256": self.sha256,
            "client_integration": self.client_integration,
            "openclaw_reloaded": self.openclaw_reloaded,
            "openclaw_probed": self.openclaw_probed,
            "previous_runtime_retained": True,
        }


def check_for_update(
    *,
    current_version: str,
    repository: str = DEFAULT_REPOSITORY,
    gh_command: str | Path = "gh",
    runner: CommandRunner = subprocess.run,
) -> ReleaseStatus:
    """Return the newest stable GitHub release visible to this user."""

    normalized_current = _normalize_version(current_version)
    try:
        release = _resolve_release(
            _resolve_command(gh_command, label="GitHub CLI"),
            repository=repository,
            requested_version=None,
            runner=runner,
        )
    except Exception as exc:
        return ReleaseStatus(
            status="error",
            repository=repository,
            current_version=normalized_current,
            latest_version=None,
            update_available=None,
            error_code=getattr(exc, "code", "MANAGED_UPDATE_ERROR"),
        )
    available = _version_tuple(release.version) > _version_tuple(
        normalized_current
    )
    return ReleaseStatus(
        status="available" if available else "current",
        repository=repository,
        current_version=normalized_current,
        latest_version=release.version,
        update_available=available,
        release_url=release.url,
    )


def update_installation(
    *,
    dotenv_path: str | Path,
    workspace: str | Path,
    target_version: str,
    repository: str = DEFAULT_REPOSITORY,
    gh_command: str | Path = "gh",
    base_python: str | Path | None = None,
    runner: CommandRunner = subprocess.run,
    clock: Callable[[], datetime] | None = None,
    post_activation_check: Callable[[ManagedInstallation], None] | None = None,
) -> UpdateResult:
    """Install, verify and atomically activate one published release."""

    resolved_dotenv = Path(dotenv_path).expanduser().absolute()
    resolved_workspace = Path(workspace).expanduser().absolute()
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise UpdateError("GitHub repository must use OWNER/REPOSITORY syntax")
    managed = load_managed_installation(resolved_workspace)
    if not supports_transactional_updates(managed):
        raise UpdateError("a managed installation is required for updates")
    assert managed is not None
    if managed.dotenv_path != resolved_dotenv:
        raise UpdateError("dashboard and managed installation do not match")
    current_version = _normalize_version(managed.active_version)
    requested = _normalize_version(target_version)
    release = _resolve_release(
        _resolve_command(gh_command, label="GitHub CLI"),
        repository=repository,
        requested_version=requested,
        runner=runner,
    )
    if _version_tuple(release.version) <= _version_tuple(current_version):
        raise UpdateError(
            "target release must be newer than the active version"
        )
    runtime = load_runtime_settings(resolved_dotenv, environ={})
    load_single_instance_config(runtime.config_path)
    if runtime.arapi_bridge_jar_path is None or runtime.arapi_lib_dir is None:
        raise UpdateError("managed AR API paths are incomplete")
    state_dir = _state_directory(runtime)
    target_root = resolved_workspace / "runtime" / release.version
    target_python, target_server, target_setup, _ = versioned_runtime_paths(
        resolved_workspace,
        release.version,
    )
    lock = _UpdateLock(resolved_workspace / "runtime" / ".update.lock")
    with lock:
        wheel = _download_release(
            _resolve_command(gh_command, label="GitHub CLI"),
            workspace=resolved_workspace,
            repository=repository,
            release=release,
            runner=runner,
        )
        selected_python = (
            Path(base_python)
            if base_python is not None
            else Path(getattr(sys, "_base_executable", sys.executable))
        )
        _install_runtime(
            target_root,
            target_python=target_python,
            wheel=wheel,
            base_python=selected_python,
            runner=runner,
        )
        _verify_installed_version(
            target_python,
            expected=release.version,
            runner=runner,
        )
        backup = _create_backup(
            workspace=resolved_workspace,
            dotenv_path=resolved_dotenv,
            runtime=runtime,
            managed=managed,
            clock=clock or (lambda: datetime.now(UTC)),
        )
        previous_openclaw = _openclaw_definition(managed, runner=runner)
        activation_started = False
        try:
            _run(
                [
                    str(target_setup),
                    "--arapi-lib-dir",
                    str(runtime.arapi_lib_dir),
                    "--config-dir",
                    str(resolved_dotenv.parent),
                    "--data-dir",
                    str(resolved_workspace),
                    "--state-dir",
                    str(state_dir),
                    "--no-dashboard",
                    "--no-managed",
                ],
                runner=runner,
                timeout=600,
                action="gateway setup",
            )
            target_check = target_python.parent / (
                "helix-mcp-check.exe" if os.name == "nt" else "helix-mcp-check"
            )
            _run(
                [str(target_check), "--dotenv", str(resolved_dotenv)],
                runner=runner,
                timeout=180,
                action="gateway smoke test",
            )
            activation_started = True
            activated = activate_managed_installation(
                workspace=resolved_workspace,
                version=release.version,
                server_command=target_server,
                dotenv_path=resolved_dotenv,
                client=managed.client,
                server_name=managed.server_name,
                openclaw_command=managed.openclaw_command,
                dashboard_port=managed.dashboard_port,
            )
            openclaw_reloaded, openclaw_probed = _switch_openclaw(
                activated,
                previous_openclaw,
                runner=runner,
            )
            if post_activation_check is not None:
                post_activation_check(activated)
        except Exception:
            _restore_backup(
                backup=backup,
                dotenv_path=resolved_dotenv,
                runtime=runtime,
            )
            if activation_started:
                _restore_managed_files(backup, resolved_workspace)
            _restore_openclaw(managed, previous_openclaw, runner=runner)
            raise
        _write_update_manifest(
            backup,
            {
                "status": "updated",
                "current_version": current_version,
                "target_version": release.version,
                "sha256": release.sha256,
            },
        )
    return UpdateResult(
        status="updated",
        current_version=current_version,
        target_version=release.version,
        target_runtime=target_root,
        wheel=wheel,
        backup=backup,
        sha256=release.sha256,
        client_integration=managed.client,
        openclaw_reloaded=openclaw_reloaded,
        openclaw_probed=openclaw_probed,
    )


def _resolve_release(
    gh_command: Path,
    *,
    repository: str,
    requested_version: str | None,
    runner: CommandRunner,
) -> Release:
    command = [str(gh_command), "release", "view"]
    if requested_version:
        command.append(f"v{requested_version}")
    command.extend(
        [
            "--repo",
            repository,
            "--json",
            "tagName,isDraft,isPrerelease,assets,url",
        ]
    )
    completed = _run(
        command,
        runner=runner,
        timeout=60,
        action="GitHub release discovery",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise UpdateError("GitHub release metadata is invalid") from None
    if not isinstance(payload, dict):
        raise UpdateError("GitHub release metadata is invalid")
    if payload.get("isDraft") or payload.get("isPrerelease"):
        raise UpdateError("only stable GitHub releases can be installed")
    tag = str(payload.get("tagName", ""))
    version = _normalize_version(tag)
    if requested_version and version != requested_version:
        raise UpdateError("GitHub returned an unexpected release")
    wheel_name = f"helix_mcp_gateway-{version}-py3-none-any.whl"
    assets = [
        item
        for item in payload.get("assets", [])
        if isinstance(item, dict) and item.get("name") == wheel_name
    ]
    if len(assets) != 1:
        raise UpdateError("release does not contain exactly one gateway wheel")
    digest = str(assets[0].get("digest", ""))
    algorithm, separator, value = digest.partition(":")
    sha256 = (
        value.casefold()
        if separator and algorithm.casefold() == "sha256"
        else ""
    )
    if not _SHA256_PATTERN.fullmatch(sha256):
        raise UpdateError("release wheel has no valid SHA-256 digest")
    return Release(
        version=version,
        tag=tag,
        wheel_name=wheel_name,
        sha256=sha256,
        url=str(payload.get("url") or "") or None,
    )


def _download_release(
    gh_command: Path,
    *,
    workspace: Path,
    repository: str,
    release: Release,
    runner: CommandRunner,
) -> Path:
    download_dir = workspace / "downloads" / release.version
    if download_dir.is_symlink():
        raise UpdateError("release download directory cannot be a link")
    download_dir.mkdir(parents=True, exist_ok=True)
    destination = download_dir / release.wheel_name
    if destination.is_symlink():
        raise UpdateError("release wheel cannot be a link")
    if destination.is_file():
        _verify_sha256(destination, release.sha256)
        return destination
    with tempfile.TemporaryDirectory(
        prefix=".download-",
        dir=download_dir,
    ) as temporary:
        _run(
            [
                str(gh_command),
                "release",
                "download",
                release.tag,
                "--repo",
                repository,
                "--pattern",
                release.wheel_name,
                "--dir",
                temporary,
            ],
            runner=runner,
            timeout=300,
            action="GitHub release download",
        )
        downloaded = Path(temporary) / release.wheel_name
        if not downloaded.is_file():
            raise UpdateError("GitHub did not download the expected wheel")
        _verify_sha256(downloaded, release.sha256)
        os.replace(downloaded, destination)
    return destination


def _install_runtime(
    target_root: Path,
    *,
    target_python: Path,
    wheel: Path,
    base_python: Path,
    runner: CommandRunner,
) -> None:
    if target_root.is_symlink():
        raise UpdateError("target runtime cannot be a link")
    resumed = target_root.exists()
    if resumed:
        if not target_root.is_dir() or not target_python.is_file():
            raise UpdateError("existing target runtime is incomplete")
    else:
        target_root.mkdir(parents=True)
        _run(
            [
                str(_resolve_command(base_python, label="base Python")),
                "-m",
                "venv",
                str(target_root / "venv"),
            ],
            runner=runner,
            timeout=300,
            action="target virtual environment creation",
        )
    install_command = [
        str(target_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]
    if resumed:
        install_command.append("--force-reinstall")
    install_command.append(str(wheel))
    _run(
        install_command,
        runner=runner,
        timeout=900,
        action="target package installation",
    )
    _run(
        [str(target_python), "-m", "pip", "check"],
        runner=runner,
        timeout=120,
        action="target dependency validation",
    )
    executable_dir = target_python.parent
    suffix = ".exe" if os.name == "nt" else ""
    for label, path in (
        ("server", executable_dir / f"helix-mcp{suffix}"),
        ("setup", executable_dir / f"helix-mcp-setup{suffix}"),
        ("check", executable_dir / f"helix-mcp-check{suffix}"),
    ):
        if not path.is_file():
            raise UpdateError(f"target {label} entry point was not installed")


def _verify_installed_version(
    python: Path,
    *,
    expected: str,
    runner: CommandRunner,
) -> None:
    completed = _run(
        [
            str(python),
            "-c",
            "from importlib.metadata import version; print(version('helix-mcp-gateway'))",
        ],
        runner=runner,
        timeout=30,
        action="target version validation",
    )
    if completed.stdout.strip() != expected:
        raise UpdateError(
            "installed gateway version does not match the release"
        )


def _create_backup(
    *,
    workspace: Path,
    dotenv_path: Path,
    runtime: RuntimeSettings,
    managed: ManagedInstallation,
    clock: Callable[[], datetime],
) -> Path:
    timestamp = clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = workspace / "backups" / f"update-{timestamp}"
    backup.mkdir(parents=True)
    if os.name != "nt":
        backup.chmod(0o700)
    files: dict[str, Path | None] = {
        "dotenv": dotenv_path,
        "config": runtime.config_path,
        "bridge": runtime.arapi_bridge_jar_path,
        "key": runtime.write_plan_key_path,
        "launcher": managed.launcher,
        "dashboard_launcher": managed.dashboard_launcher,
        "installation": installation_metadata_path(workspace),
    }
    for name, source in files.items():
        if source is not None and source.is_file():
            shutil.copy2(source, backup / name)
    if (
        runtime.write_plan_db_path is not None
        and runtime.write_plan_db_path.is_file()
    ):
        with (
            sqlite3.connect(runtime.write_plan_db_path) as database_source,
            sqlite3.connect(backup / "write-plans.sqlite3") as destination,
        ):
            database_source.backup(destination)
    _write_update_manifest(
        backup,
        {
            "status": "prepared",
            "active_version": managed.active_version,
        },
    )
    return backup


def _restore_backup(
    *,
    backup: Path,
    dotenv_path: Path,
    runtime: RuntimeSettings,
) -> None:
    targets: dict[str, Path | None] = {
        "dotenv": dotenv_path,
        "config": runtime.config_path,
        "bridge": runtime.arapi_bridge_jar_path,
        "key": runtime.write_plan_key_path,
    }
    for name, target in targets.items():
        source = backup / name
        if target is not None and source.is_file():
            _restore_file(source, target)
    database_backup = backup / "write-plans.sqlite3"
    if runtime.write_plan_db_path is not None and database_backup.is_file():
        with (
            sqlite3.connect(database_backup) as database_source,
            sqlite3.connect(runtime.write_plan_db_path) as destination,
        ):
            database_source.backup(destination)


def _restore_managed_files(backup: Path, workspace: Path) -> None:
    for name, target in (
        ("launcher", stable_launcher_path(workspace)),
        (
            "dashboard_launcher",
            stable_dashboard_launcher_path(workspace),
        ),
        ("installation", installation_metadata_path(workspace)),
    ):
        source = backup / name
        if source.is_file():
            _restore_file(source, target)


def _restore_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.restore-",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _openclaw_definition(
    managed: ManagedInstallation,
    *,
    runner: CommandRunner,
) -> dict[str, Any] | None:
    if managed.client != "openclaw":
        return None
    command = _resolve_command(
        managed.openclaw_command or "openclaw",
        label="OpenClaw",
    )
    completed = _run(
        [
            str(command),
            "mcp",
            "show",
            managed.server_name or DEFAULT_OPENCLAW_SERVER_NAME,
            "--json",
        ],
        runner=runner,
        timeout=60,
        action="OpenClaw MCP definition inspection",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise UpdateError("OpenClaw MCP definition is invalid") from None
    if not isinstance(payload, dict):
        raise UpdateError("OpenClaw MCP definition is invalid")
    return payload


def _switch_openclaw(
    managed: ManagedInstallation,
    previous: dict[str, Any] | None,
    *,
    runner: CommandRunner,
) -> tuple[bool, bool]:
    if managed.client != "openclaw":
        return False, False
    if previous is None:
        raise UpdateError("OpenClaw MCP definition is unavailable")
    _validate_openclaw_definition(previous, managed)
    command = _resolve_command(
        managed.openclaw_command or "openclaw",
        label="OpenClaw",
    )
    definition = dict(previous)
    stdio_command, stdio_arguments = _stdio_invocation(managed.launcher)
    definition["command"] = str(stdio_command)
    definition["args"] = list(stdio_arguments)
    definition["cwd"] = str(managed.launcher.parent.parent)
    definition["toolFilter"] = {"include": list(EXPOSED_TOOLS)}
    _set_openclaw_definition(
        command,
        server_name=managed.server_name or DEFAULT_OPENCLAW_SERVER_NAME,
        definition=definition,
        runner=runner,
    )
    _run(
        [str(command), "mcp", "reload"],
        runner=runner,
        timeout=90,
        action="OpenClaw MCP reload",
    )
    completed = _run(
        [
            str(command),
            "mcp",
            "probe",
            managed.server_name or DEFAULT_OPENCLAW_SERVER_NAME,
            "--json",
        ],
        runner=runner,
        timeout=180,
        action="OpenClaw MCP probe",
    )
    _validate_openclaw_probe(
        completed.stdout,
        server_name=managed.server_name or DEFAULT_OPENCLAW_SERVER_NAME,
        launcher=managed.launcher,
    )
    return True, True


def _validate_openclaw_definition(
    definition: Mapping[str, object],
    managed: ManagedInstallation,
) -> None:
    command = definition.get("command")
    arguments = definition.get("args")
    normalized_arguments = (
        tuple(arguments)
        if isinstance(arguments, list)
        and all(isinstance(item, str) for item in arguments)
        else ()
        if arguments is None
        else None
    )
    if not isinstance(command, str) or normalized_arguments is None:
        raise UpdateError("OpenClaw MCP definition is not compatible")
    configured_command = Path(command).expanduser().absolute()
    direct_legacy = (
        configured_command == managed.server_command
        and normalized_arguments == ("--dotenv", str(managed.dotenv_path))
    )
    direct_launcher = (
        configured_command == managed.launcher and normalized_arguments == ()
    )
    wrapper, wrapper_arguments = _stdio_invocation(managed.launcher)
    wrapped_launcher = (
        configured_command == wrapper
        and normalized_arguments == wrapper_arguments
    )
    if not (direct_legacy or direct_launcher or wrapped_launcher):
        raise UpdateError(
            "OpenClaw MCP definition does not belong to this installation"
        )


def _validate_openclaw_probe(
    output: str,
    *,
    server_name: str,
    launcher: Path,
) -> None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        raise UpdateError("OpenClaw MCP probe returned invalid data") from None
    if not isinstance(payload, dict) or payload.get("diagnostics"):
        raise UpdateError("OpenClaw MCP probe returned diagnostics")
    servers = payload.get("servers")
    server = servers.get(server_name) if isinstance(servers, dict) else None
    launch = server.get("launch") if isinstance(server, dict) else None
    if not isinstance(launch, str) or str(launcher) not in launch:
        raise UpdateError("OpenClaw MCP probe did not use the stable launcher")


def _stdio_invocation(command: Path) -> tuple[Path, tuple[str, ...]]:
    resolved = command.expanduser().absolute()
    if os.name != "nt" or resolved.suffix.casefold() not in {".bat", ".cmd"}:
        return resolved, ()
    command_shell = os.environ.get("COMSPEC", "").strip()
    if not command_shell:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        command_shell = str(Path(system_root) / "System32" / "cmd.exe")
    shell = _resolve_command(command_shell, label="Windows command processor")
    return shell, ("/d", "/s", "/c", str(resolved))


def _restore_openclaw(
    managed: ManagedInstallation,
    previous: dict[str, Any] | None,
    *,
    runner: CommandRunner,
) -> None:
    if managed.client != "openclaw" or previous is None:
        return
    try:
        command = _resolve_command(
            managed.openclaw_command or "openclaw",
            label="OpenClaw",
        )
        _set_openclaw_definition(
            command,
            server_name=managed.server_name or DEFAULT_OPENCLAW_SERVER_NAME,
            definition=previous,
            runner=runner,
        )
        _run(
            [str(command), "mcp", "reload"],
            runner=runner,
            timeout=90,
            action="OpenClaw rollback reload",
        )
    except Exception:
        return


def _set_openclaw_definition(
    command: Path,
    *,
    server_name: str,
    definition: Mapping[str, object],
    runner: CommandRunner,
) -> None:
    _run(
        [
            str(command),
            "mcp",
            "set",
            server_name,
            json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
        ],
        runner=runner,
        timeout=60,
        action="OpenClaw MCP switch",
    )


def _state_directory(runtime: RuntimeSettings) -> Path:
    candidates = (
        runtime.write_plan_db_path,
        runtime.audit_log_path,
        runtime.metrics_path,
    )
    for candidate in candidates:
        if candidate is not None:
            return candidate.parent
    raise UpdateError("managed state directory could not be resolved")


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise UpdateError("release wheel digest mismatch")


def _write_update_manifest(path: Path, payload: Mapping[str, object]) -> None:
    target = path / "update-result.json"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _resolve_command(candidate: str | Path, *, label: str) -> Path:
    rendered = str(candidate).strip()
    if not rendered:
        raise UpdateError(f"{label} command is empty")
    path = Path(rendered).expanduser()
    if path.parent != Path(".") or path.is_absolute():
        resolved = path.absolute()
        if resolved.is_file():
            return resolved
        raise UpdateError(f"{label} command was not found")
    discovered = shutil.which(rendered)
    if not discovered:
        raise UpdateError(f"{label} command was not found")
    return Path(discovered).absolute()


def _run(
    command: Sequence[str],
    *,
    runner: CommandRunner,
    timeout: int,
    action: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise UpdateError(f"{action} could not be completed") from None
    if completed.returncode != 0:
        raise UpdateError(
            f"{action} failed with exit code {completed.returncode}"
        )
    return completed


def _normalize_version(value: str) -> str:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise UpdateError("release version is invalid")
    return ".".join(match.groups())


def _version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in _normalize_version(value).split("."))  # type: ignore[return-value]


class _UpdateLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self) -> _UpdateLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            raise UpdateError(
                "another managed update is already running"
            ) from None
        os.write(self.descriptor, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, *_args: object) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
        self.path.unlink(missing_ok=True)
