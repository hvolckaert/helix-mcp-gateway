"""Tests for immutable, explicit environment selection."""

from __future__ import annotations

import json

import pytest

from helix_mcp.config import BackendKind, HelixConfig
from helix_mcp.targeting import (
    InvalidBackendError,
    InvalidEnvironmentError,
    TargetDisabledError,
    TargetRegistry,
    TargetResolver,
    TargetSelectionRequiredError,
)


def build_config() -> HelixConfig:
    return HelixConfig.model_validate(
        {
            "policies": [
                {
                    "name": "read_policy",
                    "allowed_forms": ["Example:ComputerSystem"],
                    "allowed_sql_objects": ["example_computer_system"],
                    "allow_sql": True,
                },
                {
                    "name": "qa_write",
                    "allowed_forms": ["Example:CreateForm"],
                    "writable_forms": ["Example:CreateForm"],
                    "creatable_fields_by_form": {
                        "Example:CreateForm": ["Description"]
                    },
                    "updatable_fields_by_form": {
                        "Example:CreateForm": ["Description"]
                    },
                    "access_mode": "read_write",
                    "require_human_approval": True,
                },
            ],
            "targets": [
                {
                    "environment": "dev",
                    "display_name": "Helix DEV",
                    "policy_ref": "read_policy",
                    "arapi": {
                        "bridge_base_url": "http://127.0.0.1:8090",
                        "gateway_port": 46000,
                        "credentials": {
                            "provider": "keyring",
                            "key": "helix-mcp-gateway/dev/arapi-service",
                        },
                    },
                },
                {
                    "environment": "qa",
                    "display_name": "Helix QA",
                    "policy_ref": "qa_write",
                    "enabled": False,
                    "arapi": {
                        "bridge_base_url": "http://127.0.0.1:8090",
                        "gateway_port": 47000,
                        "credentials": {
                            "provider": "keyring",
                            "key": "helix-mcp-gateway/qa/arapi-service",
                        },
                    },
                },
                {
                    "environment": "prod",
                    "display_name": "Helix PROD",
                    "policy_ref": "read_policy",
                    "arapi": {
                        "bridge_base_url": "http://127.0.0.1:8090",
                        "gateway_port": 48000,
                        "credentials": {
                            "provider": "vault",
                            "key": "helix/prod/arapi",
                        },
                    },
                },
            ],
        }
    )


def build_resolver() -> tuple[TargetRegistry, TargetResolver]:
    registry = TargetRegistry(build_config())
    return registry, TargetResolver(registry)


def test_registry_lists_safe_descriptors_in_environment_order() -> None:
    registry, _ = build_resolver()

    descriptors = registry.list_descriptors()

    assert [item.environment.value for item in descriptors] == [
        "dev",
        "prod",
    ]
    assert descriptors[0].capabilities.sql_read is True
    assert descriptors[1].production is True
    assert descriptors[1].read_only is True

    disabled = registry.list_descriptors(include_disabled=True)[1]
    assert disabled.environment.value == "qa"
    assert disabled.capabilities.form_create is True
    assert disabled.capabilities.form_update is True


def test_public_descriptors_do_not_expose_connection_or_secret_details() -> (
    None
):
    registry, _ = build_resolver()

    rendered = json.dumps(
        [
            descriptor.model_dump(mode="json")
            for descriptor in registry.list_descriptors(include_disabled=True)
        ]
    )

    for forbidden in (
        "instance",
        "credentials",
        "127.0.0.1",
        "46500",
        "helix-mcp-gateway",
    ):
        assert forbidden not in rendered


def test_resolver_binds_exact_environment_policy_and_backend() -> None:
    _, resolver = build_resolver()

    resolved = resolver.resolve(
        environment="dev",
        backend="arapi",
    )

    assert str(resolved.key) == "helix.dev"
    assert resolved.policy.name == "read_policy"
    assert resolved.backend is BackendKind.ARAPI
    assert resolved.backend_config is resolved.config.arapi
    assert "127.0.0.1" not in repr(resolved)
    assert "arapi-service" not in repr(resolved)


def test_resolver_requires_environment() -> None:
    _, resolver = build_resolver()

    with pytest.raises(TargetSelectionRequiredError) as exc_info:
        resolver.resolve(environment=None)

    assert exc_info.value.field == "environment"


def test_resolver_rejects_invalid_selection_values() -> None:
    _, resolver = build_resolver()

    with pytest.raises(InvalidEnvironmentError):
        resolver.resolve(environment="production")
    with pytest.raises(InvalidBackendError):
        resolver.resolve(environment="dev", backend="sql")


def test_resolver_distinguishes_disabled_and_invalid_backend_errors() -> None:
    _, resolver = build_resolver()

    with pytest.raises(TargetDisabledError):
        resolver.resolve(environment="qa")
    with pytest.raises(InvalidBackendError):
        resolver.resolve(environment="prod", backend="postgres")


def test_disabled_environment_requires_explicit_diagnostic_resolution() -> (
    None
):
    _, resolver = build_resolver()

    resolved = resolver.resolve(
        environment="qa",
        require_enabled=False,
    )

    assert str(resolved.key) == "helix.qa"
    assert resolved.config.enabled is False


def test_resolutions_do_not_retain_an_active_environment() -> None:
    _, resolver = build_resolver()

    first = resolver.resolve(environment="dev", backend="arapi")
    second = resolver.resolve(environment="prod")

    assert str(first.key) == "helix.dev"
    assert first.backend is BackendKind.ARAPI
    assert str(second.key) == "helix.prod"
    assert second.backend is None
    assert str(first.key) == "helix.dev"
