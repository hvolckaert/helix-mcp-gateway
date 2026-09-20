"""Immutable registry of configured targets and their policies."""

from __future__ import annotations

from collections.abc import Collection
from types import MappingProxyType

from helix_mcp.config import (
    Environment,
    HelixConfig,
    TargetConfig,
    TargetKey,
    TargetPolicyConfig,
)
from helix_mcp.targeting.errors import TargetNotFoundError
from helix_mcp.targeting.models import (
    TargetAvailability,
    TargetDescriptor,
    describe_target,
)


class TargetRegistry:
    """Read-only target catalog built from validated root configuration."""

    __slots__ = ("_credential_environments", "_policies", "_targets")

    def __init__(
        self,
        config: HelixConfig,
        *,
        credential_environments: Collection[Environment] | None = None,
    ) -> None:
        targets = {target.key: target for target in config.targets}
        policies = {policy.name: policy for policy in config.policies}
        self._targets = MappingProxyType(targets)
        self._policies = MappingProxyType(policies)
        self._credential_environments = (
            frozenset(credential_environments)
            if credential_environments is not None
            else None
        )

    def get(self, key: TargetKey) -> TargetConfig:
        try:
            return self._targets[key]
        except KeyError:
            raise TargetNotFoundError(key) from None

    def policy_for(self, target: TargetConfig) -> TargetPolicyConfig:
        return self._policies[target.policy_ref]

    def availability_for(self, target: TargetConfig) -> TargetAvailability:
        """Return effective availability without exposing credential content."""

        if not target.enabled:
            return TargetAvailability.DISABLED
        if (
            self._credential_environments is not None
            and target.environment not in self._credential_environments
        ):
            return TargetAvailability.CREDENTIAL_NOT_CONFIGURED
        return TargetAvailability.AVAILABLE

    def is_available(self, target: TargetConfig) -> bool:
        return self.availability_for(target) is TargetAvailability.AVAILABLE

    def list_descriptors(
        self,
        *,
        include_disabled: bool = False,
    ) -> tuple[TargetDescriptor, ...]:
        targets = (
            target
            for target in self._targets.values()
            if include_disabled or self.is_available(target)
        )
        ordered = sorted(
            targets,
            key=lambda target: _environment_order(target.environment.value),
        )
        return tuple(
            describe_target(
                target,
                self.policy_for(target),
                availability=self.availability_for(target),
            )
            for target in ordered
        )

    def __len__(self) -> int:
        return len(self._targets)


def _environment_order(environment: str) -> int:
    return {"dev": 0, "qa": 1, "prod": 2}[environment]
