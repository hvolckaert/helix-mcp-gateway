"""Tests for sanitized standalone server startup failures."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from helix_mcp import server
from helix_mcp.config import HttpServerConfig, RuntimeSettings, Transport


class StartupFailure(RuntimeError):
    code = "SAFE_STARTUP_FAILURE"


def test_main_logs_only_a_stable_code_on_startup_failure(
    monkeypatch,
    caplog,
) -> None:
    leaked = "private path and credential detail"
    received: list[Path] = []

    def fail(dotenv_path: Path) -> None:
        received.append(dotenv_path)
        raise StartupFailure(leaked)

    monkeypatch.setattr(server, "run_server", fail)
    monkeypatch.setattr(server, "configure_logging", lambda level: None)

    with (
        caplog.at_level(logging.ERROR, logger="helix_mcp.startup"),
        pytest.raises(SystemExit) as exc_info,
    ):
        server.main(["--dotenv", "runtime.env"])

    assert exc_info.value.code == 1
    assert received == [Path("runtime.env")]
    assert len(caplog.records) == 1
    assert caplog.records[0].error_code == "SAFE_STARTUP_FAILURE"
    assert leaked not in caplog.records[0].getMessage()


def test_run_server_configures_the_optional_audit_sink(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    operation_path = tmp_path / "operations.jsonl"
    runtime_settings = RuntimeSettings(
        config_path=tmp_path / "helix.yaml",
        audit_log_path=audit_path,
        audit_log_max_bytes=4096,
        audit_log_backup_count=3,
        operation_log_path=operation_path,
        operation_log_max_bytes=8192,
        operation_log_backup_count=4,
    )
    application = SimpleNamespace(
        settings=SimpleNamespace(
            log_level="INFO",
            transport=Transport.STDIO,
        ),
        runtime=SimpleNamespace(settings=runtime_settings),
        closed=True,
    )
    calls: list[tuple[str, dict[str, object]]] = []
    transports: list[str] = []
    fake_server = SimpleNamespace(
        run=lambda *, transport: transports.append(transport)
    )
    monkeypatch.setattr(server, "load_application", lambda path: application)
    monkeypatch.setattr(server, "create_mcp_server", lambda app: fake_server)
    monkeypatch.setattr(
        server,
        "configure_logging",
        lambda level, **kwargs: calls.append((level, kwargs)),
    )

    server.run_server(tmp_path / ".env")

    assert calls == [
        (
            "INFO",
            {
                "audit_log_path": audit_path,
                "audit_log_max_bytes": 4096,
                "audit_log_backup_count": 3,
                "operation_log_path": operation_path,
                "operation_log_max_bytes": 8192,
                "operation_log_backup_count": 4,
            },
        )
    ]
    assert transports == ["stdio"]


def test_run_server_preserves_http_binding_and_request_limits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    application = SimpleNamespace(
        settings=SimpleNamespace(
            log_level="INFO",
            transport=Transport.STREAMABLE_HTTP,
            http=HttpServerConfig(host="127.0.0.1", port=8123),
        ),
        runtime=SimpleNamespace(
            settings=RuntimeSettings(config_path=tmp_path / "helix.yaml"),
        ),
        closed=True,
    )
    calls = []
    monkeypatch.setattr(server, "load_application", lambda path: application)
    monkeypatch.setattr(server, "configure_logging", lambda *a, **kw: None)
    monkeypatch.setattr(
        server,
        "create_mcp_server",
        lambda app: SimpleNamespace(run=lambda **kw: calls.append(kw)),
    )

    server.run_server(tmp_path / ".env")

    assert calls == [
        {
            "transport": "streamable-http",
            "host": "127.0.0.1",
            "port": 8123,
            "streamable_http_path": "/mcp",
            "json_response": True,
            "stateless_http": True,
            "max_request_body_size": 1_048_576,
        }
    ]
