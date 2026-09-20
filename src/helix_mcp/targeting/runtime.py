"""Build the fixed DEV/QA/PROD target context from local configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from helix_mcp.config import (
    Environment,
    HelixConfig,
    RuntimeSettings,
    RuntimeSettingsError,
    Transport,
    compose_single_instance_config,
    load_runtime_settings,
    load_secret_environment,
    load_single_instance_config,
)
from helix_mcp.secrets import EnvironmentSecretProvider, SecretResolver
from helix_mcp.targeting.registry import TargetRegistry


@dataclass(frozen=True, slots=True)
class RuntimeTargetContext:
    """Validated environment catalog and registered secret resolver."""

    settings: RuntimeSettings
    config: HelixConfig
    registry: TargetRegistry
    secrets: SecretResolver


def load_runtime_target_context(
    dotenv_path: str | Path = ".env",
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeTargetContext:
    """Build one logical Helix target for each fixed environment."""

    settings = load_runtime_settings(dotenv_path, environ=environ)
    composition = load_single_instance_config(settings.config_path)
    secret_values = load_secret_environment(dotenv_path, environ=environ)
    credential_environments = frozenset(
        environment
        for environment in Environment
        if secret_values.get(
            f"HELIX_CREDENTIAL_{environment.value.upper()}", ""
        ).strip()
    )
    config = compose_single_instance_config(composition)
    if (
        config.server.transport is Transport.STREAMABLE_HTTP
        and settings.http_bearer_token is None
    ):
        raise RuntimeSettingsError(
            "streamable_http requires HELIX_MCP_HTTP_BEARER_TOKEN"
        )
    secrets = SecretResolver([EnvironmentSecretProvider(secret_values)])
    return RuntimeTargetContext(
        settings=settings,
        config=config,
        registry=TargetRegistry(
            config,
            credential_environments=credential_environments,
        ),
        secrets=secrets,
    )
