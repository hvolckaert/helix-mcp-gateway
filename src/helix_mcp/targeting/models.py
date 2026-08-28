"""Safe public and internal models for resolved targets."""

from __future__ import annotations

from dataclasses import dataclass

from helix_mcp.config import (
    AccessMode,
    ArapiBackendConfig,
    BackendKind,
    Environment,
    TargetConfig,
    TargetKey,
    TargetPolicyConfig,
)
from helix_mcp.config.models import FrozenModel

BackendConfig = ArapiBackendConfig


class TargetCapabilities(FrozenModel):
    """Non-sensitive operations available for a target."""

    form_read: bool
    sql_read: bool
    form_create: bool
    form_update: bool
    health_check: bool = True


class TargetDescriptor(FrozenModel):
    """Public target view that excludes connection and credential details."""

    environment: Environment
    display_name: str
    enabled: bool
    production: bool
    read_only: bool
    backends: tuple[BackendKind, ...]
    capabilities: TargetCapabilities


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedTarget:
    """Internal target and policy selected for one operation."""

    config: TargetConfig
    policy: TargetPolicyConfig
    backend: BackendKind | None = None

    @property
    def key(self) -> TargetKey:
        return self.config.key

    @property
    def backend_config(self) -> BackendConfig | None:
        if self.backend is BackendKind.ARAPI:
            return self.config.arapi
        return None

    def __repr__(self) -> str:
        backend = self.backend.value if self.backend is not None else "auto"
        return f"<ResolvedTarget {self.key} backend={backend}>"


def describe_target(
    target: TargetConfig,
    policy: TargetPolicyConfig,
) -> TargetDescriptor:
    """Build a public descriptor without serializing internal configuration."""

    backends = tuple(
        backend
        for backend in BackendKind
        if backend in target.enabled_backends
    )
    form_backend_available = BackendKind.ARAPI in target.enabled_backends
    writes_enabled = policy.access_mode is AccessMode.READ_WRITE
    capabilities = TargetCapabilities(
        form_read=policy.allow_form_reads and form_backend_available,
        sql_read=policy.allow_sql and form_backend_available,
        form_create=writes_enabled
        and bool(policy.creatable_fields_by_form)
        and form_backend_available,
        form_update=writes_enabled
        and bool(policy.updatable_fields_by_form)
        and form_backend_available,
    )
    return TargetDescriptor(
        environment=target.environment,
        display_name=target.display_name,
        enabled=target.enabled,
        production=target.environment is Environment.PROD,
        read_only=not capabilities.form_create
        and not capabilities.form_update,
        backends=backends,
        capabilities=capabilities,
    )
