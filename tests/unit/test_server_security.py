"""Security tests for MCP transport assembly."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from helix_mcp.bootstrap import ApplicationContext
from helix_mcp.config import (
    ArapiBackendConfig,
    HelixConfig,
    HttpServerConfig,
    RuntimeSettings,
    RuntimeSettingsError,
    SecretRef,
    ServerSettings,
    TargetConfig,
    TargetPolicyConfig,
)
from helix_mcp.secrets import EnvironmentSecretProvider, SecretResolver
from helix_mcp.server import create_mcp_server
from helix_mcp.targeting import RuntimeTargetContext, TargetRegistry


def _runtime(tmp_path, *, token: str | None) -> RuntimeTargetContext:
    policy = TargetPolicyConfig(name="read_only")
    target = TargetConfig(
        environment="dev",
        display_name="DEV",
        policy_ref=policy.name,
        arapi=ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_port=46_000,
            credentials=SecretRef(
                provider="environment",
                key="HELIX_CREDENTIAL_DEV",
            ),
        ),
    )
    config = HelixConfig(
        server=ServerSettings(
            transport="streamable_http",
            http=HttpServerConfig(host="127.0.0.1", port=8_000),
        ),
        policies=(policy,),
        targets=(target,),
    )
    settings = RuntimeSettings(
        config_path=tmp_path / "helix.yaml",
        http_bearer_token=token,
    )
    return RuntimeTargetContext(
        settings=settings,
        config=config,
        registry=TargetRegistry(config),
        secrets=SecretResolver([EnvironmentSecretProvider({})]),
    )


def test_http_transport_fails_closed_without_bearer_token(tmp_path) -> None:
    with pytest.raises(RuntimeSettingsError, match="requires"):
        ApplicationContext(_runtime(tmp_path, token=None))


def test_http_transport_uses_constant_time_static_token_verifier(
    tmp_path,
) -> None:
    application = ApplicationContext(_runtime(tmp_path, token="x" * 32))
    server = create_mcp_server(application)
    verifier = server._token_verifier
    assert verifier is not None

    assert asyncio.run(verifier.verify_token("incorrect")) is None
    accepted = asyncio.run(verifier.verify_token("x" * 32))
    assert accepted is not None
    assert accepted.scopes == ["helix:mcp"]

    async def unauthorized_request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.streamable_http_app()),
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.get("/mcp")

    assert asyncio.run(unauthorized_request()).status_code == 401
    asyncio.run(application.aclose())


@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
def test_http_requests_preserve_auth_wire_schema_limits_and_lifespan(
    tmp_path,
    protocol,
) -> None:
    application = ApplicationContext(_runtime(tmp_path, token="x" * 32))
    bridge = SimpleNamespace(start=AsyncMock(), aclose=AsyncMock())
    application.arapi_bridge = bridge
    server = create_mcp_server(application)
    app = server.streamable_http_app(
        host="127.0.0.1",
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=1_048_576,
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
                headers={
                    "Authorization": "Bearer " + "x" * 32,
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": protocol,
                    "MCP-Method": "tools/list",
                },
            ) as client,
        ):
            meta = (
                {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": protocol,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                }
                if protocol == "2026-07-28"
                else {}
            )
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": meta,
            }
            denied = await client.post(
                "/mcp",
                json=request,
                headers={"Authorization": "Bearer incorrect"},
            )
            assert denied.status_code == 401
            listed = await client.post("/mcp", json=request)
            assert listed.status_code == 200, listed.text
            tools = listed.json()["result"]["tools"]
            assert len(tools) == 22
            read_tool = next(t for t in tools if t["name"] == "query_form")
            assert read_tool["annotations"]["readOnlyHint"] is True
            assert "inputSchema" in read_tool
            assert "outputSchema" in read_tool
            for request_id in (2, 3):
                result = await client.post(
                    "/mcp",
                    headers={
                        "MCP-Method": "tools/call",
                        "MCP-Name": "list_targets",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {
                            "name": "list_targets",
                            "arguments": {},
                            **meta,
                        },
                    },
                )
                assert result.status_code == 200
                payload = result.json()["result"]
                assert payload["isError"] is False
                assert "structuredContent" in payload
                assert application.closed is False
            invalid_host = await client.post(
                "/mcp",
                json=request,
                headers={"Host": "attacker.test"},
            )
            assert invalid_host.status_code == 421
            oversized = await client.post(
                "/mcp",
                content=b" " * 1_048_577,
                headers={"Content-Type": "application/json"},
            )
            assert oversized.status_code == 413
        assert application.closed is True
        bridge.start.assert_awaited_once()
        bridge.aclose.assert_awaited_once()

    asyncio.run(scenario())
