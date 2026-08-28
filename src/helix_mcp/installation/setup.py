"""Non-destructive local setup for a wheel-based installation."""

from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from helix_mcp.clients.arapi import validate_arapi_libraries
from helix_mcp.installation.bridge import BridgeBuildResult, build_bridge
from helix_mcp.installation.resources import (
    read_config_template,
    validate_packaged_resources,
)

_BRIDGE_FILENAME = "helix-arapi-bridge.jar"


class SetupError(RuntimeError):
    """Sanitized installation failure."""

    code = "INSTALLATION_ERROR"


@dataclass(frozen=True, slots=True)
class InstallPaths:
    """Platform-appropriate local paths for one user installation."""

    config_dir: Path
    data_dir: Path
    state_dir: Path


@dataclass(frozen=True, slots=True)
class SetupResult:
    """Safe setup result containing no credentials."""

    paths: InstallPaths
    dotenv_path: Path
    config_path: Path
    bridge_path: Path
    arapi_lib_dir: Path | None
    dotenv_created: bool
    config_created: bool
    bridge_built: bool
    dry_run: bool
    server_command: str


def default_install_paths() -> InstallPaths:
    """Return per-user config, data and state paths without creating them."""

    if os.name == "nt":
        local = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
        root = local / "HelixMcp"
        return InstallPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            state_dir=root / "state",
        )
    return InstallPaths(
        config_dir=Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )
        / "helix-mcp",
        data_dir=Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        / "helix-mcp",
        state_dir=Path(
            os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
        )
        / "helix-mcp",
    )


def discover_arapi_lib_dir(
    explicit: str | Path | None = None,
) -> Path:
    """Resolve one explicit, environment, or unambiguous known ARAPI path."""

    if explicit is not None:
        candidate = Path(explicit).expanduser()
        validate_arapi_libraries(candidate)
        return candidate.absolute()
    configured = os.environ.get("HELIX_ARAPI_LIB_DIR")
    if configured:
        candidate = Path(configured).expanduser()
        validate_arapi_libraries(candidate)
        return candidate.absolute()

    patterns = (
        Path("/mnt/c/Program Files/BMC Software/ARSystem").glob(
            "DeveloperStudio */plugins/com.bmc.arsys.studio.api_*/lib"
        ),
        Path("C:/Program Files/BMC Software/ARSystem").glob(
            "DeveloperStudio */plugins/com.bmc.arsys.studio.api_*/lib"
        ),
    )
    candidates: list[Path] = []
    for matches in patterns:
        for candidate in matches:
            try:
                validate_arapi_libraries(candidate)
            except Exception:
                continue
            candidates.append(candidate.absolute())
    unique = tuple(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise SetupError("ARAPI library directory must be specified")
    return unique[0]


def setup_installation(
    *,
    arapi_lib_dir: str | Path | None = None,
    config_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    state_dir: str | Path | None = None,
    dry_run: bool = False,
) -> SetupResult:
    """Build the bridge and initialize missing configuration files."""

    validate_packaged_resources()
    defaults = default_install_paths()
    paths = InstallPaths(
        config_dir=_absolute(config_dir or defaults.config_dir),
        data_dir=_absolute(data_dir or defaults.data_dir),
        state_dir=_absolute(state_dir or defaults.state_dir),
    )
    dotenv_path = paths.config_dir / ".env"
    config_path = paths.config_dir / "helix.yaml"
    bridge_path = paths.data_dir / "bridge" / _BRIDGE_FILENAME
    selected_libraries = (
        None
        if dry_run and arapi_lib_dir is None
        else discover_arapi_lib_dir(arapi_lib_dir)
    )
    server_command = shutil.which("helix-mcp") or "helix-mcp"
    if dry_run:
        return SetupResult(
            paths=paths,
            dotenv_path=dotenv_path,
            config_path=config_path,
            bridge_path=bridge_path,
            arapi_lib_dir=selected_libraries,
            dotenv_created=False,
            config_created=False,
            bridge_built=False,
            dry_run=True,
            server_command=server_command,
        )

    assert selected_libraries is not None
    build_result: BridgeBuildResult = build_bridge(
        selected_libraries,
        bridge_path,
    )
    if build_result.output_path != bridge_path:
        raise SetupError("bridge installation path mismatch")
    config_created = _create_file(
        config_path,
        read_config_template(),
        mode=0o600,
    )
    write_plan_key_path = paths.state_dir / "write-plans.key"
    if not dotenv_path.exists():
        _ensure_write_plan_key(write_plan_key_path)
    dotenv_created = _create_file(
        dotenv_path,
        _render_dotenv(
            config_path=config_path,
            bridge_path=bridge_path,
            arapi_lib_dir=selected_libraries,
            audit_path=paths.state_dir / "audit.jsonl",
            metrics_path=paths.state_dir / "metrics.json",
            operation_log_path=paths.state_dir / "operations.jsonl",
            write_plan_db_path=paths.state_dir / "write-plans.sqlite3",
            write_plan_key_path=write_plan_key_path,
        ),
        mode=0o600,
    )
    return SetupResult(
        paths=paths,
        dotenv_path=dotenv_path,
        config_path=config_path,
        bridge_path=bridge_path,
        arapi_lib_dir=selected_libraries,
        dotenv_created=dotenv_created,
        config_created=config_created,
        bridge_built=True,
        dry_run=False,
        server_command=server_command,
    )


def _absolute(value: str | Path) -> Path:
    return Path(value).expanduser().absolute()


def _create_file(path: Path, content: str, *, mode: int) -> bool:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise SetupError("installation target is not a regular file")
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(content)
    except OSError:
        raise SetupError("installation file could not be created") from None
    return True


def _create_binary_file(path: Path, content: bytes, *, mode: int) -> bool:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise SetupError("installation target is not a regular file")
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except OSError:
        raise SetupError("installation file could not be created") from None
    return True


def _ensure_write_plan_key(path: Path) -> None:
    if _create_binary_file(path, secrets.token_bytes(32), mode=0o600):
        return
    try:
        if path.is_symlink() or not path.is_file():
            raise SetupError("write-plan key is not a regular file")
        if len(path.read_bytes()) != 32:
            raise SetupError("write-plan key has an invalid length")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise SetupError("write-plan key permissions are too broad")
    except OSError:
        raise SetupError("write-plan key could not be validated") from None


def _render_dotenv(
    *,
    config_path: Path,
    bridge_path: Path,
    arapi_lib_dir: Path,
    audit_path: Path,
    metrics_path: Path,
    operation_log_path: Path,
    write_plan_db_path: Path,
    write_plan_key_path: Path,
) -> str:
    def quote(path: Path) -> str:
        return json.dumps(str(path), ensure_ascii=False)

    return (
        "# Local Helix MCP Gateway configuration. Do not commit.\n"
        f"HELIX_CONFIG_PATH={quote(config_path)}\n\n"
        "# Local AR API runtime.\n"
        f"HELIX_ARAPI_BRIDGE_JAR_PATH={quote(bridge_path)}\n"
        f"HELIX_ARAPI_LIB_DIR={quote(arapi_lib_dir)}\n\n"
        "# Local audit output.\n"
        f"HELIX_AUDIT_LOG_PATH={quote(audit_path)}\n"
        "HELIX_AUDIT_LOG_MAX_BYTES=10485760\n"
        "HELIX_AUDIT_LOG_BACKUP_COUNT=5\n\n"
        "# Aggregate metrics and operation events without payloads.\n"
        f"HELIX_METRICS_PATH={quote(metrics_path)}\n"
        f"HELIX_OPERATION_LOG_PATH={quote(operation_log_path)}\n"
        "HELIX_OPERATION_LOG_MAX_BYTES=10485760\n"
        "HELIX_OPERATION_LOG_BACKUP_COUNT=5\n\n"
        "# Persistent encrypted write plans.\n"
        f"HELIX_WRITE_PLAN_DB_PATH={quote(write_plan_db_path)}\n"
        f"HELIX_WRITE_PLAN_KEY_PATH={quote(write_plan_key_path)}\n\n"
        "# Per-environment Helix/ARAPI JSON credentials.\n"
        "HELIX_CREDENTIAL_DEV=\n"
        "HELIX_CREDENTIAL_QA=\n"
        "HELIX_CREDENTIAL_PROD=\n"
    )
