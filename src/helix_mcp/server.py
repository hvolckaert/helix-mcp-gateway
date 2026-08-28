"""MCP server assembly and transport selection."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from helix_mcp.bootstrap import ApplicationContext, load_application
from helix_mcp.config import Transport
from helix_mcp.lifecycle import application_lifespan
from helix_mcp.observability import configure_logging, public_error_code
from helix_mcp.tools import register_mcp_tools

SERVER_NAME = "helix-mcp-gateway"
SERVER_INSTRUCTIONS = (
    "Use list_targets before querying BMC Helix, list_forms when form names "
    "are unknown, and list_form_fields when field names are unknown. Every "
    "operation must specify dev, qa or prod explicitly. Never infer or "
    "retain an active environment. Use list_database_objects before querying "
    "unknown database objects and list_database_columns when their columns "
    "are unknown. Every SQL query requires plan_sql_query. Show its exact "
    "environment, SQL, limit and digest in the visible response, "
    "using explicit unique aliases for every selected expression. ARAPI SQL "
    "does not support bound parameters, so every reviewed literal must be "
    "visible in the plan. SQL tools require an AR System administrator; "
    "ARAPI_ADMIN_REQUIRED does not make form tools unavailable. "
    "then end the turn. Times ending in Z are UTC. Treat the plan status and "
    "remaining_seconds returned by the MCP as authoritative; never infer "
    "expiry from the agent's local clock. Never call execute_sql_query until "
    "the user explicitly approves that exact plan in a later message. After "
    "approval, call get_sql_query_plan for the same plan_id and, if its status "
    "is pending, execute that same plan immediately. Never create a replacement "
    "plan after approval. Every Helix write also "
    "requires a plan. Never call an apply tool until the user has reviewed the "
    "exact plan and explicitly approved that write."
)


def create_mcp_server(application: ApplicationContext) -> FastMCP:
    """Create the MCP protocol adapter without starting a transport."""

    settings = application.settings
    host = settings.http.host if settings.http is not None else "127.0.0.1"
    port = settings.http.port if settings.http is not None else 8000
    server = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        log_level=settings.log_level,
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=1_048_576,
        lifespan=application_lifespan(application),
    )
    register_mcp_tools(
        server,
        targets=application.target_tools,
        forms=application.form_tools,
        database=application.database_tools,
        health=application.health_tools,
        writes=application.write_tools,
        audit=application.tool_auditor,
    )
    return server


def run_server(dotenv_path: str | Path = ".env") -> None:
    """Load configuration and run the selected MCP transport."""

    application = load_application(dotenv_path)
    try:
        runtime_settings = application.runtime.settings
        configure_logging(
            application.settings.log_level,
            audit_log_path=runtime_settings.audit_log_path,
            audit_log_max_bytes=runtime_settings.audit_log_max_bytes,
            audit_log_backup_count=runtime_settings.audit_log_backup_count,
            operation_log_path=runtime_settings.operation_log_path,
            operation_log_max_bytes=(runtime_settings.operation_log_max_bytes),
            operation_log_backup_count=(
                runtime_settings.operation_log_backup_count
            ),
        )
        server = create_mcp_server(application)
        transport: Literal["stdio", "streamable-http"] = (
            "stdio"
            if application.settings.transport is Transport.STDIO
            else "streamable-http"
        )
        server.run(transport=transport)
    finally:
        if not application.closed:
            asyncio.run(application.aclose())


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry point."""

    parser = argparse.ArgumentParser(prog="helix-mcp")
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env"),
        help="path to the local dotenv file (default: .env)",
    )
    arguments = parser.parse_args(argv)
    try:
        run_server(arguments.dotenv)
    except KeyboardInterrupt:
        return
    except Exception as exc:
        configure_logging("ERROR")
        logging.getLogger("helix_mcp.startup").error(
            "server startup failed",
            extra={
                "event": "server_startup_failed",
                "error_code": public_error_code(exc),
            },
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
