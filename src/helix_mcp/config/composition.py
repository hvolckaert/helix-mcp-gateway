"""Compose one logical Helix installation with three fixed environments."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, Self

from pydantic import Field, HttpUrl, ValidationError, model_validator

from helix_mcp.config.loader import ConfigLoader
from helix_mcp.config.models import (
    AccessMode,
    ArapiBackendConfig,
    Environment,
    FrozenModel,
    HelixConfig,
    Identifier,
    SecretProviderKind,
    SecretRef,
    ServerSettings,
    TargetConfig,
    TargetPolicyConfig,
)

HELIX_INSTANCE: Final[Literal["helix"]] = "helix"
ARAPI_PORT_BY_ENVIRONMENT: Mapping[Environment, int] = MappingProxyType(
    {
        Environment.DEV: 46_000,
        Environment.QA: 47_000,
        Environment.PROD: 48_000,
    }
)


class ArapiBackendSettings(FrozenModel):
    """ARAPI bridge defaults with environment ports fixed by the gateway."""

    bridge_base_url: HttpUrl = HttpUrl("http://127.0.0.1:8090")
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)
    pool_size: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def require_loopback_bridge(self) -> Self:
        if self.bridge_base_url.host not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError("ARAPI bridge must use a loopback host")
        return self


class SingleInstanceConfig(FrozenModel):
    """Non-sensitive rules for one Helix installation and three environments."""

    schema_version: Literal[2] = 2
    server: ServerSettings = Field(default_factory=ServerSettings)
    policies: tuple[TargetPolicyConfig, ...]
    policy_by_environment: dict[Environment, Identifier]
    arapi: ArapiBackendSettings = Field(default_factory=ArapiBackendSettings)

    @model_validator(mode="after")
    def validate_fixed_environments(self) -> Self:
        if not self.policies:
            raise ValueError("at least one policy is required")
        policy_names = {policy.name for policy in self.policies}
        if len(policy_names) != len(self.policies):
            raise ValueError("policy names must be unique")
        if set(self.policy_by_environment) != set(Environment):
            raise ValueError(
                "policy_by_environment must define dev, qa and prod"
            )
        missing = sorted(
            {
                policy_ref
                for policy_ref in self.policy_by_environment.values()
                if policy_ref not in policy_names
            }
        )
        if missing:
            raise ValueError(
                "environment mapping references unknown policies: "
                + ", ".join(missing)
            )
        policy_by_name = {policy.name: policy for policy in self.policies}
        production_policy = policy_by_name[
            self.policy_by_environment[Environment.PROD]
        ]
        if production_policy.access_mode is not AccessMode.READ_ONLY:
            raise ValueError("prod policy must use access_mode read_only")
        return self


class SingleInstanceConfigError(ValueError):
    """Sanitized configuration-loading failure."""

    code = "SINGLE_INSTANCE_CONFIG_ERROR"


class SingleInstanceCompositionError(ValueError):
    """Sanitized failure while building the fixed environment catalog."""

    code = "SINGLE_INSTANCE_COMPOSITION_ERROR"


def load_single_instance_config(path: str | Path) -> SingleInstanceConfig:
    """Load strict, non-sensitive single-installation rules from YAML."""

    data = ConfigLoader().load_mapping(path)
    try:
        return SingleInstanceConfig.model_validate(data)
    except ValidationError as exc:
        locations = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "<root>"
                for error in exc.errors(include_input=False)
            }
        )
        raise SingleInstanceConfigError(
            "invalid single-instance settings at: " + ", ".join(locations)
        ) from None


def compose_single_instance_config(
    composition: SingleInstanceConfig,
) -> HelixConfig:
    """Build exactly one logical target for DEV, QA and PROD."""

    try:
        targets = tuple(
            TargetConfig(
                instance=HELIX_INSTANCE,
                environment=environment,
                display_name=f"Helix {environment.value.upper()}",
                policy_ref=composition.policy_by_environment[environment],
                arapi=_compose_arapi(
                    composition.arapi,
                    environment=environment,
                ),
            )
            for environment in Environment
        )
        return HelixConfig(
            server=composition.server,
            policies=composition.policies,
            targets=targets,
        )
    except ValidationError:
        raise SingleInstanceCompositionError(
            "single-instance configuration failed canonical validation"
        ) from None


def _compose_arapi(
    settings: ArapiBackendSettings,
    *,
    environment: Environment,
) -> ArapiBackendConfig:
    return ArapiBackendConfig(
        bridge_base_url=settings.bridge_base_url,
        gateway_host="127.0.0.1",
        gateway_port=ARAPI_PORT_BY_ENVIRONMENT[environment],
        credentials=_credential_reference(environment),
        request_timeout_seconds=settings.request_timeout_seconds,
        pool_size=settings.pool_size,
    )


def _credential_reference(environment: Environment) -> SecretRef:
    return SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key=f"HELIX_CREDENTIAL_{environment.value.upper()}",
    )
