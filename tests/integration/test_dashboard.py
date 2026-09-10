from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from dotenv import dotenv_values

import helix_mcp.dashboard as dashboard_module
from helix_mcp.clients.arapi import (
    ArapiAdminRequiredError,
    ArapiField,
    ArapiSqlResult,
)
from helix_mcp.config import (
    AccessMode,
    TargetKey,
    TargetPolicyConfig,
    load_single_instance_config,
)
from helix_mcp.dashboard import (
    DashboardConfigurationError,
    DashboardConflictError,
    DashboardHTTPServer,
    DashboardProcessLauncher,
    DashboardService,
)
from helix_mcp.installation.managed import activate_managed_installation
from helix_mcp.installation.updater import ReleaseStatus
from helix_mcp.services.database import (
    DatabaseObjectKind,
    DatabaseObjectMetadata,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _installation(tmp_path: Path, *, secret: str | None = None) -> Path:
    config_path = tmp_path / "helix.yaml"
    config_path.write_bytes((PROJECT_ROOT / "config/helix.yaml").read_bytes())
    dotenv_path = tmp_path / ".env"
    lines = [f"HELIX_CONFIG_PATH={json.dumps(str(config_path))}"]
    if secret is not None:
        lines.append(
            "HELIX_CREDENTIAL_DEV=" + json.dumps(secret, ensure_ascii=False)
        )
    dotenv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dotenv_path.chmod(0o600)
    return dotenv_path


def _configuration(
    state: dict[str, object],
    *,
    credentials: dict[str, object] | None = None,
) -> dict[str, object]:
    configuration = state["configuration"]
    assert isinstance(configuration, dict)
    server = configuration["server"]
    arapi = configuration["arapi"]
    assert isinstance(server, dict)
    assert isinstance(arapi, dict)
    assert arapi["client_version"] is None
    return {
        "revision": state["revision"],
        "server": {
            "log_level": "WARNING",
            "metadata_cache_ttl_seconds": server["metadata_cache_ttl_seconds"],
            "health_cache_ttl_seconds": server["health_cache_ttl_seconds"],
            "write_plan_ttl_seconds": server["write_plan_ttl_seconds"],
            "max_pending_write_plans": server["max_pending_write_plans"],
        },
        "arapi": {
            "bridge_base_url": "http://127.0.0.1:8091",
            "request_timeout_seconds": arapi["request_timeout_seconds"],
            "pool_size": arapi["pool_size"],
        },
        "policies": configuration["policies"],
        "credentials": credentials or {},
    }


@pytest.mark.integration
def test_state_is_sanitized_and_describes_fixed_environments(
    tmp_path: Path,
) -> None:
    raw_secret = json.dumps(
        {"username": "private-user", "password": "private-password"}
    )
    service = DashboardService(
        _installation(tmp_path, secret=raw_secret),
        process_environment={},
    )

    state = service.state()
    serialized = json.dumps(state)

    assert "private-user" not in serialized
    assert "private-password" not in serialized
    assert state["files"] == {
        "configuration": "helix.yaml",
        "dotenv": ".env",
    }
    configuration = state["configuration"]
    assert isinstance(configuration, dict)
    environments = configuration["environments"]
    assert [item["environment"] for item in environments] == [
        "dev",
        "qa",
        "prod",
    ]
    assert [item["gateway_port"] for item in environments] == [
        46_000,
        47_000,
        48_000,
    ]
    assert environments[0]["credential"] == {
        "configured": True,
        "source": "dotenv",
        "editable": True,
    }
    policies = configuration["policies"]
    assert isinstance(policies, dict)
    assert set(policies) == {"dev", "qa", "prod"}
    assert set(policies["dev"]) == set(TargetPolicyConfig.model_fields)
    update = state["update"]
    assert isinstance(update, dict)
    assert update["managed"] is False
    assert update["release"]["status"] == "unavailable"


@pytest.mark.integration
def test_managed_update_can_be_checked_and_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv_path = _installation(tmp_path)
    workspace = tmp_path / "data"
    bridge = workspace / "bridge/helix-arapi-bridge.jar"
    bridge.parent.mkdir(parents=True)
    bridge.write_bytes(b"bridge")
    with dotenv_path.open("a", encoding="utf-8") as stream:
        stream.write(f"HELIX_ARAPI_BRIDGE_JAR_PATH={bridge}\n")
    server = workspace / "runtime/0.6.8/venv/bin/helix-mcp"
    server.parent.mkdir(parents=True)
    server.write_text("server", encoding="utf-8")
    (
        server.parent / ("python.exe" if os.name == "nt" else "python")
    ).write_text("python", encoding="utf-8")
    activate_managed_installation(
        workspace=workspace,
        version="0.6.8",
        server_command=server,
        dotenv_path=dotenv_path,
    )
    monkeypatch.setattr(
        dashboard_module,
        "check_for_update",
        lambda **kwargs: ReleaseStatus(
            status="available",
            repository="hvolckaert/helix-mcp-gateway",
            current_version="0.6.8",
            latest_version="0.7.0",
            update_available=True,
        ),
    )
    captured: dict[str, object] = {}

    class FakeWorker:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def start(self) -> SimpleNamespace:
            return SimpleNamespace(pid=4_321)

    monkeypatch.setattr(
        dashboard_module,
        "DashboardUpdateWorkerLauncher",
        FakeWorker,
    )
    service = DashboardService(dotenv_path, process_environment={})

    update = service.check_update()
    started = service.start_update(
        dashboard_port=8_766,
        dashboard_token="local-token",
    )

    assert update["managed"] is True
    assert update["release"]["latest_version"] == "0.7.0"
    assert started == {
        "status": "started",
        "target_version": "0.7.0",
        "process_id": 4_321,
    }
    assert captured["workspace"] == workspace
    assert captured["dashboard_port"] == 8_766
    assert captured["dashboard_token"] == "local-token"


@pytest.mark.integration
def test_configure_updates_yaml_and_write_only_credential(
    tmp_path: Path,
) -> None:
    dotenv_path = _installation(tmp_path)
    service = DashboardService(dotenv_path, process_environment={})
    state = service.state()
    request = _configuration(
        state,
        credentials={
            "dev": {
                "username": "dashboard-user",
                "password": "dashboard-password",
                "authentication": "example-domain",
            }
        },
    )

    result = service.configure(request)

    assert result["restart_required"] is True
    config = yaml.safe_load((tmp_path / "helix.yaml").read_text())
    assert config["server"]["log_level"] == "WARNING"
    assert config["arapi"]["bridge_base_url"] == ("http://127.0.0.1:8091/")
    assert [policy["name"] for policy in config["policies"]] == [
        "dev",
        "qa",
        "prod",
    ]
    assert config["policy_by_environment"] == {
        "dev": "dev",
        "qa": "qa",
        "prod": "prod",
    }
    values = dotenv_values(dotenv_path, interpolate=False)
    stored = json.loads(values["HELIX_CREDENTIAL_DEV"])
    assert stored == {
        "username": "dashboard-user",
        "password": "dashboard-password",
        "authentication": "example-domain",
    }
    serialized_result = json.dumps(result)
    assert "dashboard-user" not in serialized_result
    assert "dashboard-password" not in serialized_result
    if os.name != "nt":
        assert dotenv_path.stat().st_mode & 0o077 == 0


@pytest.mark.integration
def test_configure_reloads_managed_openclaw_and_reports_applied(
    tmp_path: Path,
) -> None:
    dotenv_path = _installation(tmp_path)
    workspace = tmp_path / "data"
    bridge = workspace / "bridge/helix-arapi-bridge.jar"
    bridge.parent.mkdir(parents=True)
    bridge.write_bytes(b"bridge")
    with dotenv_path.open("a", encoding="utf-8") as stream:
        stream.write(f"HELIX_ARAPI_BRIDGE_JAR_PATH={bridge}\n")
    server = workspace / "runtime/0.7.0/venv/bin/helix-mcp"
    server.parent.mkdir(parents=True)
    server.write_text("server", encoding="utf-8")
    (
        server.parent / ("python.exe" if os.name == "nt" else "python")
    ).write_text("python", encoding="utf-8")
    openclaw = tmp_path / "bin/openclaw"
    openclaw.parent.mkdir()
    openclaw.write_text("openclaw", encoding="utf-8")
    activate_managed_installation(
        workspace=workspace,
        version="0.7.0",
        server_command=server,
        dotenv_path=dotenv_path,
        client="openclaw",
        server_name="helix",
        openclaw_command=openclaw,
    )
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    service = DashboardService(
        dotenv_path,
        process_environment={},
        command_runner=runner,
    )

    result = service.configure(_configuration(service.state()))

    assert calls == [[str(openclaw), "mcp", "reload"]]
    assert result["restart_required"] is False
    assert result["application"] == {
        "client": "openclaw",
        "status": "applied",
        "error_code": None,
    }


@pytest.mark.integration
def test_configure_keeps_saved_files_when_openclaw_reload_fails(
    tmp_path: Path,
) -> None:
    dotenv_path = _installation(tmp_path)
    workspace = tmp_path / "data"
    bridge = workspace / "bridge/helix-arapi-bridge.jar"
    bridge.parent.mkdir(parents=True)
    bridge.write_bytes(b"bridge")
    with dotenv_path.open("a", encoding="utf-8") as stream:
        stream.write(f"HELIX_ARAPI_BRIDGE_JAR_PATH={bridge}\n")
    server = workspace / "runtime/0.7.0/venv/bin/helix-mcp"
    server.parent.mkdir(parents=True)
    server.write_text("server", encoding="utf-8")
    (
        server.parent / ("python.exe" if os.name == "nt" else "python")
    ).write_text("python", encoding="utf-8")
    openclaw = tmp_path / "bin/openclaw"
    openclaw.parent.mkdir()
    openclaw.write_text("openclaw", encoding="utf-8")
    activate_managed_installation(
        workspace=workspace,
        version="0.7.0",
        server_command=server,
        dotenv_path=dotenv_path,
        client="openclaw",
        server_name="helix",
        openclaw_command=openclaw,
    )

    def runner(command: list[str], **kwargs: object):
        return subprocess.CompletedProcess(command, 1, "", "failed")

    service = DashboardService(
        dotenv_path,
        process_environment={},
        command_runner=runner,
    )
    request = _configuration(service.state())

    result = service.configure(request)

    assert result["restart_required"] is True
    assert result["application"] == {
        "client": "openclaw",
        "status": "reload_failed",
        "error_code": "OPENCLAW_INTEGRATION_ERROR",
    }
    assert (
        yaml.safe_load((tmp_path / "helix.yaml").read_text())["server"][
            "log_level"
        ]
        == "WARNING"
    )


@pytest.mark.integration
def test_dashboard_can_enable_controlled_writes_for_prod(
    tmp_path: Path,
) -> None:
    dotenv_path = _installation(tmp_path)
    service = DashboardService(dotenv_path, process_environment={})
    request = _configuration(service.state())
    policies = request["policies"]
    assert isinstance(policies, dict)
    prod = policies["prod"]
    assert isinstance(prod, dict)
    prod.update(
        {
            "allowed_forms": ["Example:WritableForm"],
            "writable_forms": ["Example:WritableForm"],
            "creatable_fields_by_form": {
                "Example:WritableForm": ["Description"]
            },
            "updatable_fields_by_form": {
                "Example:WritableForm": ["Description"]
            },
            "access_mode": "read_write",
            "require_human_approval": True,
            "write_rate_limit_per_minute": 3,
        }
    )

    service.configure(request)

    config = load_single_instance_config(tmp_path / "helix.yaml")
    prod_policy = next(
        policy for policy in config.policies if policy.name == "prod"
    )
    assert prod_policy.access_mode is AccessMode.READ_WRITE
    assert prod_policy.writable_forms == ("Example:WritableForm",)
    assert prod_policy.write_rate_limit_per_minute == 3


@pytest.mark.integration
def test_dashboard_can_enable_broad_non_sensitive_write_scopes(
    tmp_path: Path,
) -> None:
    service = DashboardService(
        _installation(tmp_path),
        process_environment={},
    )
    request = _configuration(service.state())
    policies = request["policies"]
    assert isinstance(policies, dict)
    dev = policies["dev"]
    assert isinstance(dev, dict)
    dev.update(
        {
            "allow_all_forms": True,
            "allowed_forms": [],
            "allow_all_writable_forms": True,
            "writable_forms": [],
            "allow_all_creatable_fields": True,
            "creatable_fields_by_form": {},
            "allow_all_updatable_fields": True,
            "updatable_fields_by_form": {},
            "access_mode": "read_write",
        }
    )

    state = service.configure(request)

    config = load_single_instance_config(tmp_path / "helix.yaml")
    dev_policy = next(
        policy for policy in config.policies if policy.name == "dev"
    )
    assert dev_policy.allow_all_writable_forms is True
    assert dev_policy.allow_all_creatable_fields is True
    assert dev_policy.allow_all_updatable_fields is True
    configured = state["configuration"]["policies"]["dev"]
    assert isinstance(configured, dict)
    assert configured["allow_all_writable_forms"] is True
    assert configured["allow_all_creatable_fields"] is True
    assert configured["allow_all_updatable_fields"] is True


@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    [
        "allow_form_reads",
        "require_human_approval",
        "require_write_reason",
    ],
)
def test_dashboard_rejects_disabling_managed_policy_guarantees(
    tmp_path: Path,
    field: str,
) -> None:
    service = DashboardService(
        _installation(tmp_path),
        process_environment={},
    )
    request = _configuration(service.state())
    policies = request["policies"]
    assert isinstance(policies, dict)
    dev = policies["dev"]
    assert isinstance(dev, dict)
    dev[field] = False

    with pytest.raises(
        DashboardConfigurationError,
        match="invalid request at: policies",
    ):
        service.configure(request)


@pytest.mark.integration
def test_stale_revision_preserves_both_files(tmp_path: Path) -> None:
    dotenv_path = _installation(tmp_path)
    service = DashboardService(dotenv_path, process_environment={})
    state = service.state()
    request = _configuration(state)
    original_config = (tmp_path / "helix.yaml").read_bytes()
    dotenv_path.write_text(
        dotenv_path.read_text() + "# external change\n",
        encoding="utf-8",
    )
    changed_dotenv = dotenv_path.read_bytes()

    with pytest.raises(DashboardConflictError, match="reload"):
        service.configure(request)

    assert (tmp_path / "helix.yaml").read_bytes() == original_config
    assert dotenv_path.read_bytes() == changed_dotenv


@pytest.mark.integration
def test_process_managed_credential_cannot_be_replaced(
    tmp_path: Path,
) -> None:
    dotenv_path = _installation(tmp_path)
    service = DashboardService(
        dotenv_path,
        process_environment={"HELIX_CREDENTIAL_DEV": "private"},
    )
    state = service.state()
    request = _configuration(
        state,
        credentials={
            "dev": {
                "username": "new-user",
                "password": "new-password",
            }
        },
    )

    with pytest.raises(
        DashboardConfigurationError,
        match="managed by the process environment",
    ):
        service.configure(request)


@pytest.mark.integration
def test_preflight_forwards_only_process_managed_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Report:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"status": "ready", "checks": []}

    async def fake_check_readiness(
        *args: object,
        **kwargs: object,
    ) -> Report:
        captured.update(kwargs)
        return Report()

    monkeypatch.setattr(
        dashboard_module,
        "check_readiness",
        fake_check_readiness,
    )
    service = DashboardService(
        _installation(tmp_path),
        process_environment={
            "HELIX_CONFIG_PATH": "/tmp/untrusted.yaml",
            "HELIX_CREDENTIAL_DEV": "private",
            "PATH": "/tmp/untrusted",
        },
    )

    report = service.preflight({"live": False, "environments": []})

    assert report == {"status": "ready", "checks": []}
    assert captured["environ"] == {"HELIX_CREDENTIAL_DEV": "private"}


@pytest.mark.integration
def test_dashboard_catalog_filters_and_paginates_policy_independent_metadata(
    tmp_path: Path,
) -> None:
    service = DashboardService(_installation(tmp_path), process_environment={})
    captured: list[tuple[object, ...]] = []

    class FakeMetadataSession:
        def list_forms(
            self,
            environment: object,
            revision: str,
            *,
            refresh: bool = False,
        ) -> tuple[str, ...]:
            captured.append(("forms", environment, revision))
            return ("Zeta:Form", "alpha:Form", "Alpha:Form", "Beta:Form")

        def list_fields(
            self,
            environment: object,
            form: str,
            revision: str,
            *,
            refresh: bool = False,
        ) -> tuple[ArapiField, ...]:
            captured.append(("fields", environment, form, revision))
            return (
                ArapiField(id=8, name="Status", datatype="ENUM"),
                ArapiField(id=7, name="Description", datatype="CHAR"),
            )

        def close(self) -> None:
            return None

        def list_sql_objects(
            self,
            environment: object,
            revision: str,
            *,
            refresh: bool = False,
        ) -> tuple[tuple[DatabaseObjectMetadata, ...], bool]:
            captured.append(("sql_objects", environment, revision))
            return (
                (
                    DatabaseObjectMetadata(
                        schema_name="public",
                        name="tickets",
                        kind=DatabaseObjectKind.TABLE,
                    ),
                    DatabaseObjectMetadata(
                        schema_name="reporting",
                        name="ticket_summary",
                        kind=DatabaseObjectKind.VIEW,
                    ),
                ),
                False,
            )

    service._metadata_session = FakeMetadataSession()  # type: ignore[assignment]

    forms = service.form_catalog(
        {
            "environment": "qa",
            "name_contains": "form",
            "offset": 1,
            "limit": 2,
        }
    )
    fields = service.field_catalog(
        {
            "environment": "qa",
            "form": "alpha:Form",
            "name_contains": "s",
        }
    )
    sql_objects = service.sql_object_catalog(
        {
            "environment": "qa",
            "name_contains": "ticket",
            "kind": "view",
        }
    )

    assert forms == {
        "environment": "qa",
        "items": [{"name": "Beta:Form"}, {"name": "Zeta:Form"}],
        "offset": 1,
        "limit": 2,
        "total": 3,
    }
    assert fields == {
        "environment": "qa",
        "form": "alpha:Form",
        "items": [
            {"id": 7, "name": "Description", "datatype": "CHAR"},
            {"id": 8, "name": "Status", "datatype": "ENUM"},
        ],
        "offset": 0,
        "limit": 100,
        "total": 2,
    }
    assert sql_objects == {
        "environment": "qa",
        "items": [
            {
                "schema": "reporting",
                "name": "ticket_summary",
                "qualified_name": "reporting.ticket_summary",
                "kind": "view",
            }
        ],
        "offset": 0,
        "limit": 100,
        "total": 1,
        "truncated": False,
    }
    assert captured[0][0:2] == ("forms", dashboard_module.Environment.QA)
    assert captured[1][0:3] == (
        "fields",
        dashboard_module.Environment.QA,
        "alpha:Form",
    )
    assert captured[2][0:2] == (
        "sql_objects",
        dashboard_module.Environment.QA,
    )
    assert all(len(str(item[-1])) == 64 for item in captured)


@pytest.mark.integration
def test_confirmed_non_admin_credential_cannot_enable_sql(
    tmp_path: Path,
) -> None:
    service = DashboardService(_installation(tmp_path), process_environment={})
    service._metadata_session = SimpleNamespace(  # type: ignore[assignment]
        check_sql_access=lambda environment, revision, refresh=False: False,
        close=lambda: None,
    )

    capability = service.sql_capability({"environment": "prod"})

    assert capability == {
        "environment": "prod",
        "status": "administrator_required",
    }
    request = _configuration(service.state())
    policies = request["policies"]
    assert isinstance(policies, dict)
    prod = policies["prod"]
    assert isinstance(prod, dict)
    prod.update(
        {
            "allow_sql": True,
            "allow_all_sql_objects": True,
        }
    )

    with pytest.raises(
        DashboardConfigurationError,
        match="administrator permission for: prod",
    ):
        service.configure(request)


@pytest.mark.integration
def test_dashboard_metadata_session_reuses_bridge_catalog_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    class FakeClient:
        async def list_forms(self) -> tuple[str, ...]:
            calls.append(("forms", None))
            return ("Example:Form",)

        async def list_fields(self, form: str) -> tuple[ArapiField, ...]:
            calls.append(("fields", form))
            return (ArapiField(id=1, name="Request ID", datatype="CHAR"),)

        async def query_sql(self, **kwargs: object) -> ArapiSqlResult:
            calls.append(("sql_objects", None))
            return ArapiSqlResult(
                rows=(("public", "tickets", "table"),),
                truncated=False,
            )

    client = FakeClient()

    class FakeApplication:
        settings = SimpleNamespace(metadata_cache_ttl_seconds=60)
        target_resolver = SimpleNamespace(
            resolve=lambda **kwargs: SimpleNamespace(
                policy=SimpleNamespace(query_timeout_seconds=30)
            )
        )
        arapi_clients = SimpleNamespace(get=lambda target: client)

        def __init__(self) -> None:
            self.started = False
            self.closed = False

        async def astart(self) -> None:
            self.started = True

        async def aclose(self) -> None:
            self.closed = True

    application = FakeApplication()
    monkeypatch.setattr(
        dashboard_module,
        "load_application",
        lambda *args, **kwargs: application,
    )
    session = dashboard_module._DashboardMetadataSession(
        _installation(tmp_path),
        {},
    )
    try:
        assert session.list_forms(
            dashboard_module.Environment.DEV,
            "a" * 64,
        ) == ("Example:Form",)
        assert session.list_forms(
            dashboard_module.Environment.DEV,
            "a" * 64,
        ) == ("Example:Form",)
        assert session.list_fields(
            dashboard_module.Environment.DEV,
            "Example:Form",
            "a" * 64,
        ) == (ArapiField(id=1, name="Request ID", datatype="CHAR"),)
        assert session.list_sql_objects(
            dashboard_module.Environment.DEV,
            "a" * 64,
        ) == (
            (
                DatabaseObjectMetadata(
                    schema_name="public",
                    name="tickets",
                    kind=DatabaseObjectKind.TABLE,
                ),
            ),
            False,
        )
        assert (
            session.check_sql_access(
                dashboard_module.Environment.DEV,
                "a" * 64,
            )
            is True
        )
        assert application.started is True
        assert calls == [
            ("forms", None),
            ("fields", "Example:Form"),
            ("sql_objects", None),
        ]
    finally:
        session.close()
    assert application.closed is True


@pytest.mark.integration
def test_dashboard_sql_capability_recognizes_admin_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def query_sql(self, **kwargs: object) -> ArapiSqlResult:
            raise ArapiAdminRequiredError(
                TargetKey(environment=dashboard_module.Environment.PROD),
                "administrator permission is required",
                status_code=403,
            )

    class FakeApplication:
        settings = SimpleNamespace(metadata_cache_ttl_seconds=60)
        target_resolver = SimpleNamespace(
            resolve=lambda **kwargs: SimpleNamespace(
                policy=SimpleNamespace(query_timeout_seconds=30)
            )
        )
        arapi_clients = SimpleNamespace(get=lambda target: FakeClient())

        async def astart(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        dashboard_module,
        "load_application",
        lambda *args, **kwargs: FakeApplication(),
    )
    session = dashboard_module._DashboardMetadataSession(
        _installation(tmp_path),
        {},
    )
    try:
        assert (
            session.check_sql_access(
                dashboard_module.Environment.PROD,
                "b" * 64,
            )
            is False
        )
    finally:
        session.close()


@pytest.mark.integration
def test_save_failure_restores_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv_path = _installation(tmp_path)
    config_path = tmp_path / "helix.yaml"
    service = DashboardService(dotenv_path, process_environment={})
    request = _configuration(service.state())
    original_config = config_path.read_bytes()
    original_dotenv = dotenv_path.read_bytes()
    original_atomic_write = dashboard_module._atomic_write
    call_count = 0

    def fail_second_write(
        path: Path,
        content: bytes,
        *,
        force_private: bool = False,
    ) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("simulated dotenv write failure")
        original_atomic_write(path, content, force_private=force_private)

    monkeypatch.setattr(
        dashboard_module,
        "_atomic_write",
        fail_second_write,
    )

    with pytest.raises(DashboardConfigurationError, match="restored"):
        service.configure(request)

    assert config_path.read_bytes() == original_config
    assert dotenv_path.read_bytes() == original_dotenv


@pytest.mark.integration
def test_invalid_request_does_not_echo_secret(tmp_path: Path) -> None:
    dotenv_path = _installation(tmp_path)
    service = DashboardService(dotenv_path, process_environment={})
    request = _configuration(service.state())
    request["credentials"] = {
        "dev": {"username": "user", "password": "secret-value", "extra": 1}
    }

    with pytest.raises(DashboardConfigurationError) as exc_info:
        service.configure(request)

    assert "secret-value" not in str(exc_info.value)


@pytest.mark.integration
def test_http_surface_is_local_english_and_csrf_protected(
    tmp_path: Path,
) -> None:
    service = DashboardService(
        _installation(tmp_path),
        process_environment={},
    )
    service._metadata_session = SimpleNamespace(  # type: ignore[assignment]
        list_forms=lambda environment, revision, refresh=False: (
            "Example:HelpDesk",
        ),
        list_fields=lambda environment, form, revision, refresh=False: (
            ArapiField(id=1, name="Request ID", datatype="CHAR"),
        ),
        list_sql_objects=lambda environment, revision, refresh=False: (
            (
                DatabaseObjectMetadata(
                    schema_name="public",
                    name="tickets",
                    kind=DatabaseObjectKind.TABLE,
                ),
            ),
            False,
        ),
        check_sql_access=lambda environment, revision, refresh=False: True,
        close=lambda: None,
    )
    server = DashboardHTTPServer(
        ("127.0.0.1", 0),
        service,
        token="test-token",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        html = response.read().decode()
        assert response.status == 200
        assert '<html lang="en">' in html
        assert "Helix MCP Gateway" in html
        assert "Configure access" in html
        assert "Configure access without editing YAML" not in html
        assert ">Access policy<" not in html
        assert "This policy is permanently assigned" not in html
        assert "Save changes" in html
        assert "Review changes before saving" in html
        assert "Write access" in html
        assert "Writes disabled" in html
        assert "Controlled writes enabled" in html
        assert "Controlled read and write" not in html
        assert "Maximum rows per request" in html
        assert "Read requests per minute" in html
        assert "Write requests per minute" in html
        assert "Gateway safeguards, not Helix API limits" in html
        assert "Form permissions" in html
        assert "Form reads are always enabled" in html
        assert "Forms in scope" in html
        assert "Readable fields" in html
        assert "Writable forms and fields" in html
        assert "SQL read permissions" in html
        assert "Enable reviewed SQL reads" in html
        assert "Enable form reads" not in html
        assert "Human approval for controlled writes" not in html
        assert "Require a write reason" not in html
        assert (
            "Controlled writes always require human approval and a reason"
            in html
        )
        assert "Form access scope" not in html
        assert "Form read scope" not in html
        assert "Controlled write scope" not in html
        assert "SQL read scope" not in html
        assert (
            html.count(
                '<button class="workspace-tab" type="button" role="tab"'
            )
            == 4
        )
        assert 'data-tab="dev"' in html
        assert 'data-tab="qa"' in html
        assert 'data-tab="prod"' in html
        assert 'data-tab="advanced"' in html
        assert 'id="tab-panel-advanced"' in html
        assert "Diagnostics" in html
        assert '<span class="metric-label">Server</span>' in html
        assert '<span class="metric-label">Credentials</span>' in html
        assert '<span class="metric-label">MCP client</span>' not in html
        assert (
            '<span class="metric-label">Dashboard service</span>' not in html
        )
        assert "Runtime connections" in html
        assert "Managed and running" in html
        assert "Running manually" in html
        assert "Automatic startup unavailable" in html
        assert "Stopped" in html
        assert "Dashboard service" in html
        assert "Managed and active" not in html
        assert "Running this session" not in html
        assert "Managed but stopped" not in html
        assert "OpenClaw" in html
        assert "Not connected" in html
        assert "status.textContent = 'Current'" not in html
        assert "status.textContent = 'Updated'" in html
        assert 'id="savebar" role="status" aria-live="polite">' in html
        assert '<span class="metric-label">Transport</span>' not in html
        assert "Check for updates" in html
        assert "The server is up to date." in html
        assert "Version ${release.latest_version} is available." in html
        assert "Unable to check for updates" in html
        assert "Check GitHub connectivity and try again." in html
        assert "Install update" in html
        assert "/api/update/check" in html
        assert "/api/update/install" in html
        assert "Search available forms" in html
        assert "Selected writable forms" in html
        assert "Selected creatable fields" in html
        assert "Selected updatable fields" in html
        assert "allow_all_writable_forms" in html
        assert "allow_all_creatable_fields" in html
        assert "allow_all_updatable_fields" in html
        assert "Broad write scopes" in html
        assert "/api/catalog/forms" in html
        assert "/api/catalog/fields" in html
        assert "/api/catalog/sql-objects" in html
        assert "/api/capabilities/sql" in html
        assert "Checking administrator access" in html
        assert "Retry" in html
        assert "Policy overview" not in html
        assert "Installation checks" not in html
        assert "Sensitive field protection" not in html
        assert "Exact sensitive field names" not in html
        assert "Sensitive name markers" not in html
        assert "Manual entry fallback" not in html
        assert "Manual JSON fallback" not in html
        assert 'class="manual-editor"' not in html
        assert "test-token" in html
        assert "__DASHBOARD_TOKEN__" not in html
        assert "__CSP_NONCE__" not in html
        assert response.getheader("Cache-Control") == "no-store"
        content_security_policy = response.getheader("Content-Security-Policy")
        assert "frame-ancestors 'none'" in content_security_policy
        assert "'unsafe-inline'" not in content_security_policy
        nonce = content_security_policy.split("script-src 'nonce-", 1)[
            1
        ].split("'", 1)[0]
        assert f'<style nonce="{nonce}">' in html
        assert f'<script nonce="{nonce}">' in html

        connection.request(
            "GET",
            "/api/state",
            headers={"Host": "127.0.0.1:not-a-port"},
        )
        response = connection.getresponse()
        assert response.status == 403
        assert json.loads(response.read())["error"] == "invalid request host"

        connection.request("GET", "/api/identity")
        response = connection.getresponse()
        identity = json.loads(response.read())
        assert response.status == 200
        assert identity["product"] == "helix-mcp-gateway"
        assert identity["process_id"] == os.getpid()
        assert len(identity["installation_id"]) == 64

        connection.request("GET", "/api/health")
        response = connection.getresponse()
        health = json.loads(response.read())
        assert response.status == 200
        assert health["status"] == "ok"
        assert health["server_version"]
        assert len(health["workspace_id"]) == 16

        connection.request("GET", "/api/state")
        response = connection.getresponse()
        state = json.loads(response.read())
        assert response.status == 200

        connection.request(
            "POST",
            "/api/catalog/forms",
            body=json.dumps({"environment": "dev", "limit": 10}),
            headers={
                "Content-Type": "application/json",
                "X-Helix-Dashboard-Token": "test-token",
            },
        )
        response = connection.getresponse()
        catalog = json.loads(response.read())
        assert response.status == 200
        assert catalog["items"] == [{"name": "Example:HelpDesk"}]

        connection.request(
            "POST",
            "/api/catalog/sql-objects",
            body=json.dumps({"environment": "dev", "limit": 10}),
            headers={
                "Content-Type": "application/json",
                "X-Helix-Dashboard-Token": "test-token",
            },
        )
        response = connection.getresponse()
        sql_catalog = json.loads(response.read())
        assert response.status == 200
        assert sql_catalog["items"] == [
            {
                "schema": "public",
                "name": "tickets",
                "qualified_name": "public.tickets",
                "kind": "table",
            }
        ]

        connection.request(
            "POST",
            "/api/capabilities/sql",
            body=json.dumps({"environment": "dev"}),
            headers={
                "Content-Type": "application/json",
                "X-Helix-Dashboard-Token": "test-token",
            },
        )
        response = connection.getresponse()
        capability = json.loads(response.read())
        assert response.status == 200
        assert capability == {
            "environment": "dev",
            "status": "available",
        }

        connection.request(
            "POST",
            "/api/configuration",
            body=json.dumps(_configuration(state)),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 403
        assert json.loads(response.read())["error"] == (
            "invalid dashboard token"
        )

        connection.request(
            "POST",
            "/api/configuration",
            body=json.dumps(_configuration(state)),
            headers={
                "Content-Type": "application/json",
                "X-Helix-Dashboard-Token": "test-token",
                "Origin": "https://example.test",
            },
        )
        response = connection.getresponse()
        assert response.status == 403
        assert json.loads(response.read())["error"] == (
            "invalid request origin"
        )
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class FakeDashboardProcess:
    pid = 8_765

    def __init__(self) -> None:
        self.waited = threading.Event()

    def wait(self) -> int:
        self.waited.set()
        return 0

    def poll(self) -> None:
        return None


@pytest.mark.integration
def test_dashboard_launcher_detaches_and_waits_for_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv_path = _installation(tmp_path)
    captured: dict[str, object] = {}
    fake_process = FakeDashboardProcess()

    def fake_popen(
        command: list[str], **kwargs: object
    ) -> FakeDashboardProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return fake_process

    launcher = DashboardProcessLauncher(
        dotenv_path=dotenv_path,
        errors_path=tmp_path / "state/errors",
        python_executable="/runtime/python",
        port=8_877,
    )
    probes = iter([None, fake_process.pid])
    opened: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "_probe_pid", lambda: next(probes))
    monkeypatch.setattr(
        dashboard_module.webbrowser,
        "open",
        lambda url: opened.append(url),
    )

    process = launcher.start()

    assert process.to_dict() == {
        "pid": 8_765,
        "url": "http://127.0.0.1:8877/",
        "reused": False,
    }
    assert captured["command"] == [
        "/runtime/python",
        "-m",
        "helix_mcp.dashboard",
        "--dotenv",
        str(dotenv_path),
        "--port",
        "8877",
        "--no-browser",
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == tmp_path
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    if os.name == "nt":
        assert kwargs["creationflags"]
    else:
        assert kwargs["start_new_session"] is True
    assert fake_process.waited.wait(timeout=1)
    assert opened == ["http://127.0.0.1:8877/"]
    assert "HELIX_DASHBOARD_TOKEN" not in kwargs["env"]
    assert (tmp_path / "state/errors/dashboard.log").is_file()


@pytest.mark.integration
def test_dashboard_launcher_reuses_the_same_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = DashboardProcessLauncher(
        dotenv_path=_installation(tmp_path),
        errors_path=tmp_path / "errors",
        port=8_877,
    )
    opened: list[str] = []
    monkeypatch.setattr(launcher, "_probe_pid", lambda: 4_321)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("dashboard was launched twice"),
    )
    monkeypatch.setattr(
        dashboard_module.webbrowser,
        "open",
        lambda url: opened.append(url),
    )

    process = launcher.start()

    assert process.to_dict() == {
        "pid": 4_321,
        "url": "http://127.0.0.1:8877/",
        "reused": True,
    }
    assert opened == ["http://127.0.0.1:8877/"]


@pytest.mark.integration
def test_detached_dashboard_process_is_reused_end_to_end(
    tmp_path: Path,
) -> None:
    dotenv_path = _installation(tmp_path)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    launcher = DashboardProcessLauncher(
        dotenv_path=dotenv_path,
        errors_path=tmp_path / "errors",
        port=port,
    )
    try:
        started = launcher.start(open_browser=False)
        reused = DashboardProcessLauncher(
            dotenv_path=dotenv_path,
            errors_path=tmp_path / "errors",
            port=port,
        ).start(open_browser=False)

        assert started.reused is False
        assert reused.reused is True
        assert reused.pid == started.pid
        assert reused.url == started.url
    finally:
        if launcher._process is not None:
            launcher._process.terminate()
            launcher._process.wait(timeout=5)
