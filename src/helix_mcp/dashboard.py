"""Local administrative dashboard for Helix MCP Gateway."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable, Coroutine, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import urlsplit

import yaml
from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
)

from helix_mcp.bootstrap import ApplicationContext, load_application
from helix_mcp.clients.arapi import (
    ArapiAdminRequiredError,
    ArapiField,
    ArapiSqlValue,
)
from helix_mcp.config import (
    ARAPI_PORT_BY_ENVIRONMENT,
    BackendKind,
    ConfigLoader,
    Environment,
    RuntimeSettings,
    SingleInstanceConfig,
    TargetPolicyConfig,
    load_runtime_settings,
    load_single_instance_config,
)
from helix_mcp.dashboard_runtime import (
    DASHBOARD_MODE_ENV,
    DashboardRuntimeManager,
    dashboard_workspace_id,
)
from helix_mcp.dashboard_update_worker import (
    DashboardUpdateWorkerLauncher,
    load_update_status,
    write_update_status,
)
from helix_mcp.installation.managed import (
    activate_managed_installation,
    load_managed_installation,
    supports_transactional_updates,
    versioned_runtime_paths,
)
from helix_mcp.installation.openclaw import reload_managed_openclaw
from helix_mcp.installation.updater import (
    DEFAULT_REPOSITORY,
    ReleaseStatus,
    check_for_update,
)
from helix_mcp.observability import public_error_code
from helix_mcp.operations.preflight import check_readiness
from helix_mcp.services.database import (
    DatabaseObjectKind,
    DatabaseObjectMetadata,
)

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8766
MAX_REQUEST_BYTES = 65_536
_CREDENTIAL_PREFIX = "HELIX_CREDENTIAL_"
_REVISION_LENGTH = 64
_WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
_WINDOWS_DETACHED_PROCESS = 0x00000008
_METADATA_OPERATION_TIMEOUT_SECONDS = 330
_METADATA_PAGE_LIMIT = 200
_MAX_FIELD_CACHE_ENTRIES = 256
_MAX_SQL_CATALOG_OBJECTS = 100_000
LOGGER = logging.getLogger(__name__)

_DASHBOARD_OBJECT_CATALOG_SQL = """
SELECT
    n.nspname AS schema_name,
    c.relname AS object_name,
    CASE c.relkind
        WHEN 'r' THEN 'table'
        WHEN 'p' THEN 'partitioned_table'
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized_view'
        WHEN 'f' THEN 'foreign_table'
    END AS object_kind
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND n.nspname <> 'pg_catalog'
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg_toast%'
  AND n.nspname NOT LIKE 'pg_temp_%'
ORDER BY n.nspname, c.relname
""".strip()

_T = TypeVar("_T")

_BoundedText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512, strip_whitespace=True),
]
_Revision = Annotated[
    str,
    StringConstraints(
        min_length=_REVISION_LENGTH,
        max_length=_REVISION_LENGTH,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class DashboardError(ValueError):
    """Expected dashboard failure with a stable public code."""

    code = "DASHBOARD_ERROR"


class DashboardConflictError(DashboardError):
    """The files changed after the browser loaded them."""

    code = "DASHBOARD_CONFIGURATION_CONFLICT"


class DashboardConfigurationError(DashboardError):
    """A candidate configuration cannot be stored safely."""

    code = "DASHBOARD_CONFIGURATION_INVALID"


class DashboardLaunchError(DashboardError):
    """The detached dashboard could not be started or reused safely."""

    code = "DASHBOARD_LAUNCH_ERROR"


class DashboardMetadataError(DashboardError):
    """Environment metadata cannot be discovered safely."""

    code = "DASHBOARD_METADATA_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class DashboardProcess:
    """Public details for a detached local dashboard process."""

    pid: int
    url: str
    reused: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DashboardProcessLauncher:
    """Start or safely reuse a dashboard independently of setup and MCP."""

    def __init__(
        self,
        *,
        dotenv_path: str | Path,
        errors_path: str | Path,
        python_executable: str | None = None,
        port: int = DEFAULT_DASHBOARD_PORT,
    ) -> None:
        if not 1 <= port <= 65_535:
            raise ValueError("dashboard port must be between 1 and 65535")
        self.dotenv_path = Path(dotenv_path).expanduser().absolute()
        self.errors_path = Path(errors_path).expanduser().absolute()
        self.python_executable = python_executable or sys.executable
        self.port = port
        self._process: subprocess.Popen[bytes] | None = None

    def start(self, *, open_browser: bool = True) -> DashboardProcess:
        """Launch a detached worker, or reuse this installation's worker."""

        if self._process is not None:
            raise DashboardLaunchError("dashboard launcher was already used")
        url = f"http://{LOOPBACK_HOST}:{self.port}/"
        existing_pid = self._probe_pid()
        if existing_pid is not None:
            result = DashboardProcess(
                pid=existing_pid,
                url=url,
                reused=True,
            )
            if open_browser:
                webbrowser.open(url)
            return result

        self._prepare_errors_path()
        log_path = self.errors_path / "dashboard.log"
        command = [
            self.python_executable,
            "-m",
            "helix_mcp.dashboard",
            "--dotenv",
            str(self.dotenv_path),
            "--port",
            str(self.port),
            "--no-browser",
        ]
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        kwargs: dict[str, Any] = {
            "cwd": self.dotenv_path.parent,
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":  # pragma: no cover - Windows installation path
            kwargs["creationflags"] = (
                _WINDOWS_CREATE_NEW_PROCESS_GROUP | _WINDOWS_DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True
        descriptor = _open_private_append(log_path)
        try:
            with os.fdopen(descriptor, "ab", buffering=0) as log:
                self._process = subprocess.Popen(
                    command,
                    stderr=log,
                    **kwargs,
                )
        except Exception:
            raise DashboardLaunchError(
                "dashboard process could not be started"
            ) from None
        process = self._process
        threading.Thread(
            target=process.wait,
            name="helix-dashboard-reaper",
            daemon=True,
        ).start()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            pid = self._probe_pid()
            if pid is not None:
                result = DashboardProcess(
                    pid=pid,
                    url=url,
                    reused=pid != process.pid,
                )
                if open_browser:
                    webbrowser.open(url)
                return result
            if process.poll() is not None:
                raise DashboardLaunchError(
                    "dashboard process stopped before becoming ready"
                )
            time.sleep(0.05)

        process.terminate()
        raise DashboardLaunchError(
            "dashboard process did not become ready in time"
        )

    def _probe_pid(self) -> int | None:
        connection = http.client.HTTPConnection(
            LOOPBACK_HOST,
            self.port,
            timeout=0.25,
        )
        try:
            connection.request("GET", "/api/identity")
            response = connection.getresponse()
            payload_bytes = response.read(MAX_REQUEST_BYTES + 1)
        except (OSError, http.client.HTTPException):
            return None
        finally:
            connection.close()
        if (
            response.status != HTTPStatus.OK
            or len(payload_bytes) > MAX_REQUEST_BYTES
        ):
            raise DashboardLaunchError(
                "dashboard port is used by another local service"
            )
        try:
            identity = json.loads(payload_bytes)
            if identity["product"] != "helix-mcp-gateway":
                raise KeyError("unexpected dashboard product")
            pid = identity["process_id"]
            installation_id = identity["installation_id"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise DashboardLaunchError(
                "dashboard port is used by another local service"
            ) from None
        if installation_id != _installation_id(self.dotenv_path):
            raise DashboardLaunchError(
                "dashboard port belongs to another Helix MCP installation"
            )
        if not isinstance(pid, int) or pid < 1:
            raise DashboardLaunchError(
                "dashboard returned an invalid process id"
            )
        return pid

    def _prepare_errors_path(self) -> None:
        if self.errors_path.exists():
            if self.errors_path.is_symlink() or not self.errors_path.is_dir():
                raise DashboardLaunchError(
                    "dashboard error path is not a regular directory"
                )
        else:
            self.errors_path.mkdir(parents=True, mode=0o700)
        if os.name != "nt":
            self.errors_path.chmod(0o700)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DashboardServerSettings(_StrictModel):
    """Editable process settings that do not change the MCP transport."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    metadata_cache_ttl_seconds: int = Field(ge=0, le=86_400)
    health_cache_ttl_seconds: int = Field(ge=0, le=300)
    write_plan_ttl_seconds: int = Field(ge=60, le=3_600)
    max_pending_write_plans: int = Field(ge=1, le=10_000)


class DashboardArapiSettings(_StrictModel):
    """Editable local bridge settings."""

    bridge_base_url: HttpUrl
    request_timeout_seconds: int = Field(ge=1, le=300)
    pool_size: int = Field(ge=1, le=50)


class DashboardCredentialUpdate(_StrictModel):
    """Write-only credential replacement for one environment."""

    username: _BoundedText
    password: SecretStr = Field(min_length=1, max_length=4_096, repr=False)
    authentication: str | None = Field(default=None, max_length=512)

    @field_validator("authentication")
    @classmethod
    def normalize_authentication(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DashboardConfiguration(_StrictModel):
    """Complete editable dashboard payload."""

    revision: _Revision
    server: DashboardServerSettings
    arapi: DashboardArapiSettings
    policies: dict[Environment, TargetPolicyConfig]
    credentials: dict[Environment, DashboardCredentialUpdate] = Field(
        default_factory=dict
    )

    @field_validator("policies")
    @classmethod
    def require_canonical_environment_policies(
        cls,
        value: dict[Environment, TargetPolicyConfig],
    ) -> dict[Environment, TargetPolicyConfig]:
        if set(value) != set(Environment):
            raise ValueError("policies must define dev, qa and prod")
        if any(
            policy.name != environment.value
            for environment, policy in value.items()
        ):
            raise ValueError("policy names must match their environments")
        if any(not policy.allow_form_reads for policy in value.values()):
            raise ValueError(
                "dashboard-managed policies must enable form reads"
            )
        if any(not policy.require_human_approval for policy in value.values()):
            raise ValueError(
                "dashboard-managed policies must require human approval"
            )
        if any(not policy.require_write_reason for policy in value.values()):
            raise ValueError(
                "dashboard-managed policies must require a write reason"
            )
        return value


class DashboardPreflightRequest(_StrictModel):
    """Read-only readiness check selected by the dashboard."""

    live: bool = False
    environments: tuple[Environment, ...] = ()

    @field_validator("environments")
    @classmethod
    def unique_environments(
        cls,
        value: tuple[Environment, ...],
    ) -> tuple[Environment, ...]:
        if len(value) != len(set(value)):
            raise ValueError("environments must be unique")
        return value


class DashboardFormCatalogRequest(_StrictModel):
    """Bounded form discovery request for one configured environment."""

    environment: Environment
    name_contains: str | None = Field(default=None, max_length=256)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=100, ge=1, le=_METADATA_PAGE_LIMIT)
    refresh: bool = False

    @field_validator("name_contains")
    @classmethod
    def normalize_name_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DashboardFieldCatalogRequest(DashboardFormCatalogRequest):
    """Bounded field discovery request for one selected Helix form."""

    form: _BoundedText


class DashboardSqlObjectCatalogRequest(DashboardFormCatalogRequest):
    """Bounded SQL object discovery request for one environment."""

    schema_name: str | None = Field(default=None, max_length=128)
    kind: DatabaseObjectKind | None = None

    @field_validator("schema_name")
    @classmethod
    def normalize_schema_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DashboardSqlCapabilityRequest(_StrictModel):
    """Request a safe SQL administrator capability check."""

    environment: Environment
    refresh: bool = False


class _DashboardMetadataSession:
    """Keep one policy-independent ARAPI metadata session per dashboard."""

    def __init__(
        self,
        dotenv_path: Path,
        process_environment: Mapping[str, str],
    ) -> None:
        self._dotenv_path = dotenv_path
        self._credential_environment = {
            key: value
            for key, value in process_environment.items()
            if key.startswith(_CREDENTIAL_PREFIX)
        }
        self._lifecycle_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._operation_lock: asyncio.Lock | None = None
        self._application: ApplicationContext | None = None
        self._revision: str | None = None
        self._cache_ttl = 0
        self._forms_cache: dict[
            Environment,
            tuple[float, tuple[str, ...]],
        ] = {}
        self._fields_cache: dict[
            tuple[Environment, str],
            tuple[float, tuple[ArapiField, ...]],
        ] = {}
        self._sql_objects_cache: dict[
            Environment,
            tuple[float, tuple[DatabaseObjectMetadata, ...], bool],
        ] = {}
        self._sql_access_cache: dict[Environment, tuple[float, bool]] = {}

    def list_forms(
        self,
        environment: Environment,
        revision: str,
        *,
        refresh: bool = False,
    ) -> tuple[str, ...]:
        """Return all forms visible to the saved environment credential."""

        loop = self._loop_or_start()
        return self._submit(
            loop,
            self._list_forms(environment, revision, refresh=refresh),
        )

    def list_fields(
        self,
        environment: Environment,
        form: str,
        revision: str,
        *,
        refresh: bool = False,
    ) -> tuple[ArapiField, ...]:
        """Return all fields visible on one form to the saved credential."""

        loop = self._loop_or_start()
        return self._submit(
            loop,
            self._list_fields(
                environment,
                form,
                revision,
                refresh=refresh,
            ),
        )

    def list_sql_objects(
        self,
        environment: Environment,
        revision: str,
        *,
        refresh: bool = False,
    ) -> tuple[tuple[DatabaseObjectMetadata, ...], bool]:
        """Return non-system SQL objects visible to an admin credential."""

        loop = self._loop_or_start()
        return self._submit(
            loop,
            self._list_sql_objects(
                environment,
                revision,
                refresh=refresh,
            ),
        )

    def check_sql_access(
        self,
        environment: Environment,
        revision: str,
        *,
        refresh: bool = False,
    ) -> bool:
        """Return whether the saved credential can execute AR API SQL."""

        loop = self._loop_or_start()
        return self._submit(
            loop,
            self._check_sql_access(
                environment,
                revision,
                refresh=refresh,
            ),
        )

    def close(self) -> None:
        """Close the client pool and any bridge process owned by the dashboard."""

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._aclose(), loop)
        try:
            future.result(timeout=10)
        except Exception:
            future.cancel()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)

    def reset(self) -> None:
        """Release owned runtime resources while keeping the session reusable."""

        with self._lifecycle_lock:
            if self._closed:
                raise DashboardMetadataError("metadata session is closed")
            loop = self._loop
        if loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._aclose(), loop)
        try:
            future.result(timeout=10)
        except Exception:
            future.cancel()
            raise DashboardMetadataError(
                "metadata resources could not be released"
            ) from None

    def _loop_or_start(self) -> asyncio.AbstractEventLoop:
        with self._lifecycle_lock:
            if self._closed:
                raise DashboardMetadataError("metadata session is closed")
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=_run_event_loop,
                    args=(loop,),
                    name="helix-dashboard-metadata",
                    daemon=True,
                )
                self._loop = loop
                self._thread = thread
                thread.start()
            return self._loop

    @staticmethod
    def _submit(
        loop: asyncio.AbstractEventLoop,
        coroutine: Coroutine[Any, Any, _T],
    ) -> _T:
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=_METADATA_OPERATION_TIMEOUT_SECONDS)
        except TimeoutError:
            future.cancel()
            raise DashboardMetadataError(
                "metadata discovery timed out"
            ) from None

    async def _list_forms(
        self,
        environment: Environment,
        revision: str,
        *,
        refresh: bool,
    ) -> tuple[str, ...]:
        lock = self._async_lock()
        async with lock:
            application = await self._application_for(revision)
            now = time.monotonic()
            cached = self._forms_cache.get(environment)
            if not refresh and cached is not None and cached[0] > now:
                return cached[1]
            target = application.target_resolver.resolve(
                environment=environment,
                backend=BackendKind.ARAPI,
            )
            names = tuple(
                await application.arapi_clients.get(target).list_forms()
            )
            if self._cache_ttl > 0:
                self._forms_cache[environment] = (
                    now + self._cache_ttl,
                    names,
                )
            return names

    async def _list_fields(
        self,
        environment: Environment,
        form: str,
        revision: str,
        *,
        refresh: bool,
    ) -> tuple[ArapiField, ...]:
        lock = self._async_lock()
        async with lock:
            application = await self._application_for(revision)
            now = time.monotonic()
            key = (environment, form)
            cached = self._fields_cache.get(key)
            if not refresh and cached is not None and cached[0] > now:
                return cached[1]
            target = application.target_resolver.resolve(
                environment=environment,
                backend=BackendKind.ARAPI,
            )
            fields = tuple(
                await application.arapi_clients.get(target).list_fields(form)
            )
            if self._cache_ttl > 0:
                if len(self._fields_cache) >= _MAX_FIELD_CACHE_ENTRIES:
                    oldest = min(
                        self._fields_cache,
                        key=lambda item: self._fields_cache[item][0],
                    )
                    self._fields_cache.pop(oldest, None)
                self._fields_cache[key] = (
                    now + self._cache_ttl,
                    fields,
                )
            return fields

    async def _list_sql_objects(
        self,
        environment: Environment,
        revision: str,
        *,
        refresh: bool,
    ) -> tuple[tuple[DatabaseObjectMetadata, ...], bool]:
        lock = self._async_lock()
        async with lock:
            application = await self._application_for(revision)
            now = time.monotonic()
            cached = self._sql_objects_cache.get(environment)
            if not refresh and cached is not None and cached[0] > now:
                return cached[1], cached[2]
            target = application.target_resolver.resolve(
                environment=environment,
                backend=BackendKind.ARAPI,
            )
            result = await application.arapi_clients.get(target).query_sql(
                sql=_DASHBOARD_OBJECT_CATALOG_SQL,
                column_count=3,
                limit=_MAX_SQL_CATALOG_OBJECTS,
                timeout_seconds=target.policy.query_timeout_seconds,
            )
            if self._cache_ttl > 0:
                self._sql_access_cache[environment] = (
                    now + self._cache_ttl,
                    True,
                )
            objects = tuple(_dashboard_sql_object(row) for row in result.rows)
            if self._cache_ttl > 0:
                self._sql_objects_cache[environment] = (
                    now + self._cache_ttl,
                    objects,
                    result.truncated,
                )
            return objects, result.truncated

    async def _check_sql_access(
        self,
        environment: Environment,
        revision: str,
        *,
        refresh: bool,
    ) -> bool:
        lock = self._async_lock()
        async with lock:
            application = await self._application_for(revision)
            now = time.monotonic()
            cached = self._sql_access_cache.get(environment)
            if not refresh and cached is not None and cached[0] > now:
                return cached[1]
            target = application.target_resolver.resolve(
                environment=environment,
                backend=BackendKind.ARAPI,
            )
            try:
                await application.arapi_clients.get(target).query_sql(
                    sql=_DASHBOARD_OBJECT_CATALOG_SQL,
                    column_count=3,
                    limit=1,
                    timeout_seconds=target.policy.query_timeout_seconds,
                )
            except ArapiAdminRequiredError:
                available = False
            else:
                available = True
            if self._cache_ttl > 0:
                self._sql_access_cache[environment] = (
                    now + self._cache_ttl,
                    available,
                )
            return available

    def _async_lock(self) -> asyncio.Lock:
        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()
        return self._operation_lock

    async def _application_for(
        self,
        revision: str,
    ) -> ApplicationContext:
        if self._application is not None and self._revision == revision:
            return self._application
        await self._close_application()
        application = load_application(
            self._dotenv_path,
            environ=self._credential_environment,
            recover_write_plans=False,
        )
        try:
            await application.astart()
        except Exception:
            await application.aclose()
            raise
        self._application = application
        self._revision = revision
        self._cache_ttl = application.settings.metadata_cache_ttl_seconds
        self._forms_cache.clear()
        self._fields_cache.clear()
        self._sql_objects_cache.clear()
        self._sql_access_cache.clear()
        return application

    async def _close_application(self) -> None:
        application = self._application
        self._application = None
        self._revision = None
        self._forms_cache.clear()
        self._fields_cache.clear()
        self._sql_objects_cache.clear()
        self._sql_access_cache.clear()
        if application is not None:
            await application.aclose()

    async def _aclose(self) -> None:
        lock = self._async_lock()
        async with lock:
            await self._close_application()


class DashboardService:
    """Validated operations shared by the browser UI and tests."""

    def __init__(
        self,
        dotenv_path: str | Path,
        *,
        process_environment: Mapping[str, str] | None = None,
        update_repository: str = DEFAULT_REPOSITORY,
        gh_command: str | Path = "gh",
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = (
            subprocess.run
        ),
        dashboard_port: int = DEFAULT_DASHBOARD_PORT,
    ) -> None:
        self.dotenv_path = Path(dotenv_path).expanduser().absolute()
        self._process_environment = dict(
            os.environ if process_environment is None else process_environment
        )
        self._mutex = threading.Lock()
        self._metadata_session = _DashboardMetadataSession(
            self.dotenv_path,
            self._process_environment,
        )
        self._sql_capabilities: dict[tuple[str, Environment], bool] = {}
        self._update_repository = update_repository
        self._gh_command = str(gh_command)
        self._command_runner = command_runner
        self._release_status: ReleaseStatus | None = None
        self.dashboard_port = dashboard_port

    def state(self) -> dict[str, object]:
        """Return configuration state without paths or secret material."""

        runtime = load_runtime_settings(self.dotenv_path, environ={})
        configuration = load_single_instance_config(runtime.config_path)
        dotenv = self._dotenv_values()
        policy_by_name = {
            policy.name: policy for policy in configuration.policies
        }
        policies: dict[str, object] = {}
        environments = []
        for environment in Environment:
            assigned_policy = policy_by_name[
                configuration.policy_by_environment[environment]
            ]
            policy_payload = assigned_policy.model_dump(mode="json")
            policy_payload["name"] = environment.value
            policies[environment.value] = policy_payload
            variable = _credential_variable(environment)
            process_managed = bool(self._process_environment.get(variable))
            file_configured = bool(dotenv.get(variable))
            environments.append(
                {
                    "environment": environment.value,
                    "gateway_port": ARAPI_PORT_BY_ENVIRONMENT[environment],
                    "policy": environment.value,
                    "credential": {
                        "configured": process_managed or file_configured,
                        "source": (
                            "process_environment"
                            if process_managed
                            else "dotenv"
                            if file_configured
                            else "none"
                        ),
                        "editable": not process_managed,
                    },
                }
            )
        return {
            "server_version": _package_version(),
            "dashboard": self.identity(),
            "revision": self._revision(runtime.config_path),
            "configuration": {
                "server": {
                    "transport": configuration.server.transport.value,
                    "log_level": configuration.server.log_level,
                    "metadata_cache_ttl_seconds": (
                        configuration.server.metadata_cache_ttl_seconds
                    ),
                    "health_cache_ttl_seconds": (
                        configuration.server.health_cache_ttl_seconds
                    ),
                    "write_plan_ttl_seconds": (
                        configuration.server.write_plan_ttl_seconds
                    ),
                    "max_pending_write_plans": (
                        configuration.server.max_pending_write_plans
                    ),
                },
                "arapi": {
                    "bridge_base_url": str(
                        configuration.arapi.bridge_base_url
                    ),
                    "request_timeout_seconds": (
                        configuration.arapi.request_timeout_seconds
                    ),
                    "pool_size": configuration.arapi.pool_size,
                },
                "environments": environments,
                "policies": policies,
            },
            "files": {
                "configuration": runtime.config_path.name,
                "dotenv": self.dotenv_path.name,
            },
            "restart_required": False,
            "update": self._update_state(runtime),
            "dashboard_runtime": self._dashboard_runtime(runtime),
        }

    def health(self) -> dict[str, str]:
        """Return the runtime identity used by lifecycle health checks."""

        runtime = load_runtime_settings(self.dotenv_path, environ={})
        return {
            "status": "ok",
            "server_version": _package_version(),
            "workspace_id": dashboard_workspace_id(self._workspace(runtime)),
        }

    def check_update(self) -> dict[str, object]:
        """Check the configured GitHub repository for a newer stable release."""

        runtime = load_runtime_settings(self.dotenv_path, environ={})
        workspace = self._workspace(runtime)
        managed = load_managed_installation(workspace)
        if not supports_transactional_updates(managed):
            return self._update_state(runtime)
        assert managed is not None
        self._release_status = check_for_update(
            current_version=managed.active_version,
            repository=self._update_repository,
            gh_command=self._gh_command,
        )
        return self._update_state(runtime)

    def start_update(
        self,
        *,
        dashboard_port: int,
        dashboard_token: str,
    ) -> dict[str, object]:
        """Start the detached transactional updater for the checked release."""

        runtime = load_runtime_settings(self.dotenv_path, environ={})
        workspace = self._workspace(runtime)
        managed = load_managed_installation(workspace)
        if not supports_transactional_updates(managed):
            raise DashboardConfigurationError(
                "run setup once to enable managed updates"
            )
        assert managed is not None
        release = self._release_status
        if (
            release is None
            or release.status != "available"
            or not release.update_available
            or release.latest_version is None
        ):
            raise DashboardConfigurationError(
                "check for an available update before installing"
            )
        operation = load_update_status(workspace)
        if operation and operation.get("status") in {"preparing", "running"}:
            raise DashboardConflictError("a managed update is already running")
        state_dir = self._state_directory(runtime)
        write_update_status(
            workspace,
            {
                "status": "queued",
                "current_version": managed.active_version,
                "target_version": release.latest_version,
            },
        )
        try:
            process = DashboardUpdateWorkerLauncher(
                dotenv_path=self.dotenv_path,
                workspace=workspace,
                errors_path=state_dir / "errors",
                repository=self._update_repository,
                target_version=release.latest_version,
                gh_command=self._gh_command,
                dashboard_port=dashboard_port,
                dashboard_token=dashboard_token,
            ).start()
        except Exception:
            write_update_status(
                workspace,
                {
                    "status": "error",
                    "current_version": managed.active_version,
                    "target_version": release.latest_version,
                    "error_code": "DASHBOARD_UPDATE_LAUNCH_ERROR",
                },
            )
            raise DashboardError(
                "update worker could not be started"
            ) from None
        return {
            "status": "started",
            "target_version": release.latest_version,
            "process_id": process.pid,
        }

    def _update_state(self, runtime: RuntimeSettings) -> dict[str, object]:
        workspace = self._workspace(runtime)
        managed = load_managed_installation(workspace)
        supported = supports_transactional_updates(managed)
        checked = self._release_status
        current_version = (
            managed.active_version
            if managed is not None
            else _package_version()
        )
        if checked is None:
            release: dict[str, object] = {
                "status": "unknown" if supported else "unavailable",
                "repository": self._update_repository,
                "current_version": current_version,
                "latest_version": None,
                "update_available": None,
                "release_url": None,
                "error_code": None,
            }
        else:
            release = checked.to_dict()
        return {
            "managed": supported,
            "client": managed.client if managed is not None else None,
            "release": release,
            "operation": load_update_status(workspace),
        }

    def _workspace(self, runtime: RuntimeSettings) -> Path:
        bridge = runtime.arapi_bridge_jar_path
        if bridge is None:
            return self.dotenv_path.parent
        return bridge.parent.parent

    def _dashboard_runtime(
        self,
        runtime: RuntimeSettings,
    ) -> dict[str, object]:
        workspace = self._workspace(runtime)
        managed = load_managed_installation(workspace)
        if managed is None:
            return {
                "manager": "unmanaged",
                "installed": False,
                "enabled": False,
                "active": True,
                "status": "running",
                "restart_policy": "none",
                "service_name": None,
                "launcher": None,
                "port": self.dashboard_port,
                "process_managed": False,
                "error": None,
            }
        payload = (
            DashboardRuntimeManager(
                managed,
                gh_command=self._gh_command,
            )
            .status()
            .to_dict()
        )
        payload["launcher"] = Path(str(payload["launcher"])).name
        return payload

    @staticmethod
    def _state_directory(runtime: RuntimeSettings) -> Path:
        for candidate in (
            runtime.write_plan_db_path,
            runtime.audit_log_path,
            runtime.metrics_path,
        ):
            if candidate is not None:
                return candidate.parent
        return runtime.config_path.parent / "state"

    def identity(self) -> dict[str, object]:
        """Identify the local process without loading configuration files."""

        return {
            "product": "helix-mcp-gateway",
            "process_id": os.getpid(),
            "installation_id": _installation_id(self.dotenv_path),
        }

    def configure(self, raw_payload: object) -> dict[str, object]:
        """Validate and atomically persist a dashboard configuration."""

        request = _validate_request(DashboardConfiguration, raw_payload)
        with self._mutex:
            runtime = load_runtime_settings(self.dotenv_path, environ={})
            config_path = runtime.config_path
            current_revision = self._revision(config_path)
            if request.revision != current_revision:
                raise DashboardConflictError(
                    "configuration changed; reload the dashboard before saving"
                )
            unavailable_sql = [
                environment.value
                for environment, policy in request.policies.items()
                if policy.allow_sql
                and self._sql_capabilities.get((current_revision, environment))
                is False
            ]
            if unavailable_sql:
                raise DashboardConfigurationError(
                    "SQL reads require AR System administrator permission for: "
                    + ", ".join(unavailable_sql)
                )
            for environment in request.credentials:
                variable = _credential_variable(environment)
                if self._process_environment.get(variable):
                    raise DashboardConfigurationError(
                        f"credential.{environment.value} is managed by the "
                        "process environment"
                    )

            original_config = config_path.read_bytes()
            original_dotenv = self.dotenv_path.read_bytes()
            candidate: dict[str, Any] = deepcopy(
                dict(ConfigLoader().load_mapping(config_path))
            )
            candidate_server = candidate.setdefault("server", {})
            if not isinstance(candidate_server, dict):
                raise DashboardConfigurationError(
                    "server configuration is not editable"
                )
            candidate_server.update(request.server.model_dump(mode="json"))
            candidate_arapi = candidate.setdefault("arapi", {})
            if not isinstance(candidate_arapi, dict):
                raise DashboardConfigurationError(
                    "AR API configuration is not editable"
                )
            candidate_arapi.update(request.arapi.model_dump(mode="json"))
            candidate["policies"] = [
                request.policies[environment].model_dump(mode="json")
                for environment in Environment
            ]
            candidate["policy_by_environment"] = {
                environment.value: environment.value
                for environment in Environment
            }
            try:
                SingleInstanceConfig.model_validate(candidate)
            except ValidationError as exc:
                raise DashboardConfigurationError(
                    _validation_message("configuration", exc)
                ) from None

            serialized_config = yaml.safe_dump(
                candidate,
                sort_keys=False,
                allow_unicode=True,
            ).encode()
            serialized_dotenv = self._updated_dotenv(
                original_dotenv,
                request.credentials,
            )
            try:
                _atomic_write(config_path, serialized_config)
                _atomic_write(
                    self.dotenv_path,
                    serialized_dotenv,
                    force_private=True,
                )
                verified_runtime = load_runtime_settings(
                    self.dotenv_path,
                    environ={},
                )
                if verified_runtime.config_path != config_path:
                    raise DashboardConfigurationError(
                        "configuration path changed during save"
                    )
                load_single_instance_config(config_path)
            except Exception as exc:
                try:
                    _atomic_write(config_path, original_config)
                    _atomic_write(
                        self.dotenv_path,
                        original_dotenv,
                        force_private=True,
                    )
                except Exception:
                    raise DashboardConfigurationError(
                        "configuration save failed and rollback could not be "
                        "confirmed"
                    ) from None
                if isinstance(exc, DashboardError):
                    raise
                raise DashboardConfigurationError(
                    "configuration save failed; previous files were restored"
                ) from None
            self._sql_capabilities.clear()

        application = self._apply_saved_configuration()
        response = self.state()
        response["application"] = application
        response["restart_required"] = application["status"] != "applied"
        return response

    def _apply_saved_configuration(self) -> dict[str, object]:
        runtime = load_runtime_settings(self.dotenv_path, environ={})
        managed = load_managed_installation(self._workspace(runtime))
        if managed is None or managed.client != "openclaw":
            return {
                "client": managed.client
                if managed is not None
                else "unmanaged",
                "status": "restart_required",
                "error_code": None,
            }
        try:
            reload_managed_openclaw(
                managed,
                runner=self._command_runner,
            )
        except Exception as exc:
            return {
                "client": "openclaw",
                "status": "reload_failed",
                "error_code": public_error_code(exc),
            }
        return {
            "client": "openclaw",
            "status": "applied",
            "error_code": None,
        }

    def preflight(self, raw_payload: object) -> dict[str, object]:
        """Run a sanitized, read-only readiness check."""

        request = _validate_request(DashboardPreflightRequest, raw_payload)
        environments = request.environments or tuple(Environment)
        with self._mutex:
            self._metadata_session.reset()
            report = asyncio.run(
                check_readiness(
                    self.dotenv_path,
                    environ={
                        key: value
                        for key, value in self._process_environment.items()
                        if key.startswith(_CREDENTIAL_PREFIX)
                    },
                    live=request.live,
                    environments=environments,
                )
            )
        return report.model_dump(mode="json")

    def form_catalog(self, raw_payload: object) -> dict[str, object]:
        """List forms visible to the saved credential, independent of policy."""

        request = _validate_request(DashboardFormCatalogRequest, raw_payload)
        with self._mutex:
            runtime = load_runtime_settings(self.dotenv_path, environ={})
            revision = self._revision(runtime.config_path)
            try:
                names = self._metadata_session.list_forms(
                    request.environment,
                    revision,
                    refresh=request.refresh,
                )
            except DashboardMetadataError:
                raise
            except Exception as exc:
                raise DashboardMetadataError(
                    f"form metadata is unavailable ({public_error_code(exc)})"
                ) from None
        filtered = _filter_metadata_names(names, request.name_contains)
        page = filtered[request.offset : request.offset + request.limit]
        return {
            "environment": request.environment.value,
            "items": [{"name": name} for name in page],
            "offset": request.offset,
            "limit": request.limit,
            "total": len(filtered),
        }

    def field_catalog(self, raw_payload: object) -> dict[str, object]:
        """List fields for one form independent of the active access policy."""

        request = _validate_request(DashboardFieldCatalogRequest, raw_payload)
        with self._mutex:
            runtime = load_runtime_settings(self.dotenv_path, environ={})
            revision = self._revision(runtime.config_path)
            try:
                fields = self._metadata_session.list_fields(
                    request.environment,
                    request.form,
                    revision,
                    refresh=request.refresh,
                )
            except DashboardMetadataError:
                raise
            except Exception as exc:
                raise DashboardMetadataError(
                    f"field metadata is unavailable ({public_error_code(exc)})"
                ) from None
        filtered = _filter_metadata_fields(fields, request.name_contains)
        page = filtered[request.offset : request.offset + request.limit]
        return {
            "environment": request.environment.value,
            "form": request.form,
            "items": [
                {
                    "id": field.id,
                    "name": field.name,
                    "datatype": field.datatype,
                }
                for field in page
            ],
            "offset": request.offset,
            "limit": request.limit,
            "total": len(filtered),
        }

    def sql_object_catalog(self, raw_payload: object) -> dict[str, object]:
        """List non-system SQL objects independently of the active policy."""

        request = _validate_request(
            DashboardSqlObjectCatalogRequest,
            raw_payload,
        )
        with self._mutex:
            runtime = load_runtime_settings(self.dotenv_path, environ={})
            revision = self._revision(runtime.config_path)
            try:
                objects, truncated = self._metadata_session.list_sql_objects(
                    request.environment,
                    revision,
                    refresh=request.refresh,
                )
            except DashboardMetadataError:
                raise
            except Exception as exc:
                raise DashboardMetadataError(
                    "SQL object metadata is unavailable "
                    f"({public_error_code(exc)})"
                ) from None
        filtered = _filter_sql_objects(
            objects,
            name_contains=request.name_contains,
            schema_name=request.schema_name,
            kind=request.kind,
        )
        page = filtered[request.offset : request.offset + request.limit]
        return {
            "environment": request.environment.value,
            "items": [
                {
                    "schema": item.schema_name,
                    "name": item.name,
                    "qualified_name": f"{item.schema_name}.{item.name}",
                    "kind": item.kind.value,
                }
                for item in page
            ],
            "offset": request.offset,
            "limit": request.limit,
            "total": len(filtered),
            "truncated": truncated,
        }

    def sql_capability(self, raw_payload: object) -> dict[str, object]:
        """Check whether one saved credential can execute AR API SQL."""

        request = _validate_request(
            DashboardSqlCapabilityRequest,
            raw_payload,
        )
        with self._mutex:
            runtime = load_runtime_settings(self.dotenv_path, environ={})
            revision = self._revision(runtime.config_path)
            try:
                available = self._metadata_session.check_sql_access(
                    request.environment,
                    revision,
                    refresh=request.refresh,
                )
            except DashboardMetadataError:
                raise
            except Exception as exc:
                raise DashboardMetadataError(
                    "SQL administrator access could not be verified "
                    f"({public_error_code(exc)})"
                ) from None
            self._sql_capabilities[(revision, request.environment)] = available
        return {
            "environment": request.environment.value,
            "status": "available" if available else "administrator_required",
        }

    def close(self) -> None:
        """Release any metadata bridge resources owned by the dashboard."""

        self._metadata_session.close()

    def _dotenv_values(self) -> Mapping[str, str | None]:
        values = dotenv_values(
            self.dotenv_path,
            interpolate=False,
            encoding="utf-8",
        )
        return {str(key): value for key, value in values.items()}

    def _revision(self, config_path: Path) -> str:
        digest = hashlib.sha256()
        digest.update(config_path.read_bytes())
        digest.update(b"\0")
        digest.update(self.dotenv_path.read_bytes())
        return digest.hexdigest()

    def _updated_dotenv(
        self,
        original: bytes,
        credentials: Mapping[Environment, DashboardCredentialUpdate],
    ) -> bytes:
        if not credentials:
            return original
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError:
            raise DashboardConfigurationError(
                "dotenv file is not valid UTF-8"
            ) from None
        lines = text.splitlines()
        for environment, credential in credentials.items():
            variable = _credential_variable(environment)
            matching = [
                index
                for index, line in enumerate(lines)
                if _dotenv_key(line) == variable
            ]
            if len(matching) > 1:
                raise DashboardConfigurationError(
                    f"credential.{environment.value} is defined more than once"
                )
            payload = {
                "username": credential.username,
                "password": credential.password.get_secret_value(),
            }
            if credential.authentication is not None:
                payload["authentication"] = credential.authentication
            encoded_secret = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            rendered = (
                f"{variable}={json.dumps(encoded_secret, ensure_ascii=False)}"
            )
            if matching:
                lines[matching[0]] = rendered
            else:
                if lines and lines[-1]:
                    lines.append("")
                lines.append(rendered)
        return (
            ("\n".join(lines).rstrip("\n") + "\n") if lines else ""
        ).encode()


class DashboardHTTPServer(ThreadingHTTPServer):
    """Loopback HTTP server carrying a process-local mutation token."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        service: DashboardService,
        token: str,
    ) -> None:
        if address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("dashboard must listen on loopback")
        super().__init__(address, DashboardRequestHandler)
        self.service = service
        self.dashboard_token = token

    def server_close(self) -> None:
        """Close metadata resources before releasing the HTTP listener."""

        try:
            self.service.close()
        finally:
            super().server_close()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Minimal dashboard HTTP surface with bounded JSON requests."""

    server: DashboardHTTPServer

    def do_GET(self) -> None:
        if not self._trusted_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid request host"})
            return
        path = urlsplit(self.path).path
        if path == "/":
            template = (
                files("helix_mcp.resources")
                .joinpath("dashboard", "index.html")
                .read_text(encoding="utf-8")
            )
            html = template.replace(
                "__DASHBOARD_TOKEN__",
                self.server.dashboard_token,
            )
            self._send(
                HTTPStatus.OK,
                html.encode(),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/state":
            try:
                self._json(HTTPStatus.OK, self.server.service.state())
            except Exception as exc:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "error": "dashboard state unavailable",
                        "error_code": public_error_code(exc),
                    },
                )
            return
        if path == "/api/identity":
            self._json(HTTPStatus.OK, self.server.service.identity())
            return
        if path == "/api/health":
            self._json(HTTPStatus.OK, self.server.service.health())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._trusted_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid request host"})
            return
        if (
            self.headers.get("X-Helix-Dashboard-Token")
            != self.server.dashboard_token
        ):
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": "invalid dashboard token"},
            )
            return
        origin = self.headers.get("Origin")
        if origin and not self._trusted_origin(origin):
            self._json(
                HTTPStatus.FORBIDDEN, {"error": "invalid request origin"}
            )
            return
        try:
            path = urlsplit(self.path).path
            if path == "/api/configuration":
                self._json(
                    HTTPStatus.OK,
                    self.server.service.configure(self._read_json()),
                )
                return
            if path == "/api/preflight":
                self._json(
                    HTTPStatus.OK,
                    self.server.service.preflight(self._read_json()),
                )
                return
            if path == "/api/catalog/forms":
                self._json(
                    HTTPStatus.OK,
                    self.server.service.form_catalog(self._read_json()),
                )
                return
            if path == "/api/catalog/fields":
                self._json(
                    HTTPStatus.OK,
                    self.server.service.field_catalog(self._read_json()),
                )
                return
            if path == "/api/catalog/sql-objects":
                self._json(
                    HTTPStatus.OK,
                    self.server.service.sql_object_catalog(self._read_json()),
                )
                return
            if path == "/api/capabilities/sql":
                self._json(
                    HTTPStatus.OK,
                    self.server.service.sql_capability(self._read_json()),
                )
                return
            if path == "/api/update/check":
                self._require_empty_object(self._read_json())
                self._json(
                    HTTPStatus.OK,
                    self.server.service.check_update(),
                )
                return
            if path == "/api/update/install":
                self._require_empty_object(self._read_json())
                self._json(
                    HTTPStatus.ACCEPTED,
                    self.server.service.start_update(
                        dashboard_port=self.server.server_address[1],
                        dashboard_token=self.server.dashboard_token,
                    ),
                )
                return
            if path == "/api/update/prepare":
                self._require_empty_object(self._read_json())
                self._json(HTTPStatus.ACCEPTED, {"status": "stopping"})
                threading.Thread(
                    target=self.server.shutdown,
                    name="helix-dashboard-update-shutdown",
                    daemon=True,
                ).start()
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except DashboardConflictError as exc:
            self._json(
                HTTPStatus.CONFLICT,
                {"error": str(exc), "error_code": exc.code},
            )
        except DashboardError as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(exc), "error_code": exc.code},
            )
        except (ValueError, json.JSONDecodeError):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": "invalid dashboard request",
                    "error_code": "DASHBOARD_REQUEST_INVALID",
                },
            )
        except Exception:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": "unexpected dashboard error",
                    "error_code": "DASHBOARD_UNEXPECTED_ERROR",
                },
            )

    def _trusted_host(self) -> bool:
        host = self.headers.get("Host", "")
        parsed = urlsplit(f"//{host}")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        return port in {None, self.server.server_address[1]}

    def _trusted_origin(self, origin: str) -> bool:
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and port in {None, self.server.server_address[1]}
        )

    def _read_json(self) -> object:
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit():
            raise ValueError("missing or invalid Content-Length")
        length = int(raw_length)
        if length > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("Content-Type must be application/json")
        return json.loads(self.rfile.read(length))

    @staticmethod
    def _require_empty_object(payload: object) -> None:
        if payload != {}:
            raise ValueError("request body must be an empty object")

    def _json(self, status: HTTPStatus, payload: object) -> None:
        self._send(
            status,
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode(),
            "application/json; charset=utf-8",
        )

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep request details out of process output."""


def dashboard_main(argv: Sequence[str] | None = None) -> int:
    """Run the local dashboard until interrupted."""

    parser = argparse.ArgumentParser(prog="helix-mcp-dashboard")
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env"),
        help="path to the local dotenv file (default: .env)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_DASHBOARD_PORT,
        help=f"loopback port (default: {DEFAULT_DASHBOARD_PORT})",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open the dashboard in the default browser",
    )
    parser.add_argument(
        "--gh-command",
        default="gh",
        help="GitHub CLI command used for managed release checks",
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65_535:
        parser.error("--port must be between 1 and 65535")
    try:
        service = DashboardService(
            arguments.dotenv,
            dashboard_port=arguments.port,
            gh_command=arguments.gh_command,
        )
        service.state()
        server = DashboardHTTPServer(
            (LOOPBACK_HOST, arguments.port),
            service,
            token=secrets.token_urlsafe(32),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": public_error_code(exc),
                },
                separators=(",", ":"),
            )
        )
        return 1
    url = f"http://{LOOPBACK_HOST}:{arguments.port}/"
    print(json.dumps({"status": "ready", "url": url}, separators=(",", ":")))
    if not arguments.no_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    if not os.environ.get(DASHBOARD_MODE_ENV):
        threading.Thread(
            target=_adopt_managed_dashboard,
            kwargs={
                "dotenv_path": arguments.dotenv.expanduser().absolute(),
                "port": arguments.port,
                "gh_command": arguments.gh_command,
            },
            name="helix-dashboard-runtime-adoption",
            daemon=True,
        ).start()
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def stop_dashboard(_signum: int, _frame: object) -> None:
        threading.Thread(
            target=server.shutdown,
            name="helix-dashboard-shutdown",
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, stop_dashboard)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        server.server_close()
    return 0


def _adopt_managed_dashboard(
    *,
    dotenv_path: Path,
    port: int,
    gh_command: str | Path,
) -> None:
    """Migrate an older managed dashboard without interrupting its port."""

    try:
        runtime = load_runtime_settings(dotenv_path, environ={})
        bridge = runtime.arapi_bridge_jar_path
        workspace = (
            bridge.parent.parent if bridge is not None else dotenv_path.parent
        )
        managed = load_managed_installation(workspace)
        if managed is None:
            return
        if (
            not managed.dashboard_launcher.is_file()
            or managed.dashboard_port != port
        ):
            _, server, _, _ = versioned_runtime_paths(
                workspace,
                managed.active_version,
            )
            if not server.is_file():
                return
            managed = activate_managed_installation(
                workspace=workspace,
                version=managed.active_version,
                server_command=server,
                dotenv_path=managed.dotenv_path,
                client=managed.client,
                server_name=managed.server_name,
                openclaw_command=managed.openclaw_command,
                dashboard_port=port,
            )
        DashboardRuntimeManager(
            managed,
            gh_command=gh_command,
        ).install_and_start(verify=False)
    except Exception:
        LOGGER.exception(
            "could not adopt the dashboard into a persistent runtime manager"
        )


def dashboard_entrypoint() -> None:
    """Console-script wrapper."""

    raise SystemExit(dashboard_main())


def _run_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        loop.close()


def _filter_metadata_names(
    names: Sequence[str],
    name_contains: str | None,
) -> tuple[str, ...]:
    marker = name_contains.casefold() if name_contains is not None else None
    unique: dict[str, str] = {}
    for name in names:
        if marker is not None and marker not in name.casefold():
            continue
        unique.setdefault(name.casefold(), name)
    return tuple(sorted(unique.values(), key=str.casefold))


def _filter_metadata_fields(
    fields: Sequence[ArapiField],
    name_contains: str | None,
) -> tuple[ArapiField, ...]:
    marker = name_contains.casefold() if name_contains is not None else None
    return tuple(
        sorted(
            (
                field
                for field in fields
                if marker is None or marker in field.name.casefold()
            ),
            key=lambda field: (field.name.casefold(), field.id),
        )
    )


def _dashboard_sql_object(
    row: tuple[ArapiSqlValue, ...],
) -> DatabaseObjectMetadata:
    if len(row) != 3:
        raise ValueError("SQL object metadata response is invalid")
    schema_name, object_name, raw_kind = row
    if (
        not isinstance(schema_name, str)
        or not schema_name
        or not isinstance(object_name, str)
        or not object_name
        or not isinstance(raw_kind, str)
        or not raw_kind
    ):
        raise ValueError("SQL object metadata response is invalid")
    return DatabaseObjectMetadata(
        schema_name=schema_name,
        name=object_name,
        kind=DatabaseObjectKind(raw_kind),
    )


def _filter_sql_objects(
    objects: Sequence[DatabaseObjectMetadata],
    *,
    name_contains: str | None,
    schema_name: str | None,
    kind: DatabaseObjectKind | None,
) -> tuple[DatabaseObjectMetadata, ...]:
    marker = name_contains.casefold() if name_contains is not None else None
    schema_marker = schema_name.casefold() if schema_name is not None else None
    return tuple(
        sorted(
            (
                item
                for item in objects
                if (
                    schema_marker is None
                    or item.schema_name.casefold() == schema_marker
                )
                and (kind is None or item.kind is kind)
                and (
                    marker is None
                    or marker in f"{item.schema_name}.{item.name}".casefold()
                )
            ),
            key=lambda item: (
                item.schema_name.casefold(),
                item.name.casefold(),
            ),
        )
    )


def _validate_request(model: type[BaseModel], raw_payload: object) -> Any:
    try:
        return model.model_validate(raw_payload)
    except ValidationError as exc:
        raise DashboardConfigurationError(
            _validation_message("request", exc)
        ) from None


def _validation_message(label: str, error: ValidationError) -> str:
    locations = sorted(
        {
            ".".join(str(part) for part in item["loc"]) or "<root>"
            for item in error.errors(include_input=False)
        }
    )
    return f"invalid {label} at: {', '.join(locations)}"


def _credential_variable(environment: Environment) -> str:
    return f"{_CREDENTIAL_PREFIX}{environment.value.upper()}"


def _dotenv_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    key = stripped.split("=", 1)[0].strip()
    return key or None


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    force_private: bool = False,
) -> None:
    if path.is_symlink() or not path.is_file():
        raise DashboardConfigurationError(
            "configuration target is not a regular file"
        )
    mode = 0o600 if force_private else path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _open_private_append(path: Path) -> int:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise DashboardLaunchError("dashboard log is not a regular file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        if os.name != "nt":
            os.chmod(path, 0o600)
        return descriptor
    except OSError:
        raise DashboardLaunchError(
            "dashboard log could not be opened"
        ) from None


def _installation_id(dotenv_path: Path) -> str:
    digest = hashlib.sha256(b"helix-mcp-dashboard\0")
    digest.update(str(dotenv_path).encode("utf-8"))
    return digest.hexdigest()


def _package_version() -> str:
    try:
        return metadata.version("helix-mcp-gateway")
    except metadata.PackageNotFoundError:
        return "development"


if __name__ == "__main__":
    dashboard_entrypoint()
