"""Stateless resolution of one fixed Helix environment per operation."""

from __future__ import annotations

from helix_mcp.config import BackendKind, Environment, TargetKey
from helix_mcp.targeting.errors import (
    BackendUnavailableError,
    InvalidBackendError,
    InvalidEnvironmentError,
    TargetDisabledError,
    TargetSelectionRequiredError,
)
from helix_mcp.targeting.models import ResolvedTarget
from helix_mcp.targeting.registry import TargetRegistry


class TargetResolver:
    """Resolve request fields without retaining a mutable active target."""

    __slots__ = ("_registry",)

    def __init__(self, registry: TargetRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        *,
        environment: str | Environment | None,
        backend: str | BackendKind | None = None,
        require_enabled: bool = True,
    ) -> ResolvedTarget:
        key = _parse_target_key(environment)
        target = self._registry.get(key)

        if require_enabled and not target.enabled:
            raise TargetDisabledError(key)

        selected_backend = _parse_backend(backend)
        if (
            selected_backend is not None
            and selected_backend not in target.enabled_backends
        ):
            raise BackendUnavailableError(key, selected_backend)

        return ResolvedTarget(
            config=target,
            policy=self._registry.policy_for(target),
            backend=selected_backend,
        )


def _parse_target_key(
    environment: str | Environment | None,
) -> TargetKey:
    if environment is None or (
        isinstance(environment, str) and not environment.strip()
    ):
        raise TargetSelectionRequiredError("environment")

    try:
        parsed_environment = Environment(environment)
    except (TypeError, ValueError):
        raise InvalidEnvironmentError(environment) from None

    return TargetKey(environment=parsed_environment)


def _parse_backend(
    backend: str | BackendKind | None,
) -> BackendKind | None:
    if backend is None:
        return None
    try:
        return BackendKind(backend)
    except (TypeError, ValueError):
        raise InvalidBackendError(backend) from None
