"""Build the fixed DEV/QA/PROD target context from local configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from helix_mcp.config import (
    HelixConfig,
    RuntimeSettings,
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
    config = compose_single_instance_config(composition)
    secret_values = load_secret_environment(dotenv_path, environ=environ)
    secrets = SecretResolver([EnvironmentSecretProvider(secret_values)])
    return RuntimeTargetContext(
        settings=settings,
        config=config,
        registry=TargetRegistry(config),
        secrets=secrets,
    )
