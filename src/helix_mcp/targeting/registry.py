"""Immutable registry of configured targets and their policies."""

from __future__ import annotations

from types import MappingProxyType

from helix_mcp.config import (
    HelixConfig,
    TargetConfig,
    TargetKey,
    TargetPolicyConfig,
)
from helix_mcp.targeting.errors import TargetNotFoundError
from helix_mcp.targeting.models import TargetDescriptor, describe_target


class TargetRegistry:
    """Read-only target catalog built from validated root configuration."""

    __slots__ = ("_policies", "_targets")

    def __init__(self, config: HelixConfig) -> None:
        targets = {target.key: target for target in config.targets}
        policies = {policy.name: policy for policy in config.policies}
        self._targets = MappingProxyType(targets)
        self._policies = MappingProxyType(policies)

    def get(self, key: TargetKey) -> TargetConfig:
        try:
            return self._targets[key]
        except KeyError:
            raise TargetNotFoundError(key) from None

    def policy_for(self, target: TargetConfig) -> TargetPolicyConfig:
        return self._policies[target.policy_ref]

    def list_descriptors(
        self,
        *,
        include_disabled: bool = False,
    ) -> tuple[TargetDescriptor, ...]:
        targets = (
            target
            for target in self._targets.values()
            if include_disabled or target.enabled
        )
        ordered = sorted(
            targets,
            key=lambda target: _environment_order(target.environment.value),
        )
        return tuple(
            describe_target(target, self.policy_for(target))
            for target in ordered
        )

    def __len__(self) -> int:
        return len(self._targets)


def _environment_order(environment: str) -> int:
    return {"dev": 0, "qa": 1, "prod": 2}[environment]
