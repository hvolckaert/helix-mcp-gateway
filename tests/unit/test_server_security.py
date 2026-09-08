"""Security tests for MCP transport assembly."""

from __future__ import annotations

import asyncio

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
