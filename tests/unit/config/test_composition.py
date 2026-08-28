"""Tests for composing one ARAPI-only Helix installation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from helix_mcp.config import (
    ARAPI_PORT_BY_ENVIRONMENT,
    AccessMode,
    BackendKind,
    Environment,
    SingleInstanceConfig,
    SingleInstanceConfigError,
    compose_single_instance_config,
    load_single_instance_config,
)

PROJECT_ROOT = Path(__file__).parents[3]
CURRENT_CONFIG = PROJECT_ROOT / "config" / "helix.yaml"


def composition_data() -> dict[str, object]:
    return {
        "schema_version": 2,
        "policies": [
            {
                "name": "read_only",
                "allowed_forms": ["Example:Readable"],
                "allow_sql": False,
            }
        ],
        "policy_by_environment": {
            "dev": "read_only",
            "qa": "read_only",
            "prod": "read_only",
        },
        "arapi": {
            "bridge_base_url": "http://127.0.0.1:8090",
        },
    }


def test_composition_builds_three_fixed_environments_and_ports() -> None:
    rules = SingleInstanceConfig.model_validate(composition_data())

    config = compose_single_instance_config(rules)

    assert [
        (target.instance, target.environment) for target in config.targets
    ] == [
        ("helix", Environment.DEV),
        ("helix", Environment.QA),
        ("helix", Environment.PROD),
    ]
    by_environment = {target.environment: target for target in config.targets}
    assert by_environment[Environment.DEV].enabled_backends == frozenset(
        {BackendKind.ARAPI}
    )
    assert by_environment[Environment.QA].enabled_backends == frozenset(
        {BackendKind.ARAPI}
    )
    assert by_environment[Environment.PROD].enabled_backends == frozenset(
        {BackendKind.ARAPI}
    )
    for environment, port in ARAPI_PORT_BY_ENVIRONMENT.items():
        target = by_environment[environment]
        assert target.arapi is not None
        assert target.arapi.gateway_port == port
        assert (
            target.arapi.credentials.key
            == f"HELIX_CREDENTIAL_{environment.value.upper()}"
        )


def test_every_fixed_environment_requires_a_policy() -> None:
    data = composition_data()
    del data["policy_by_environment"]["qa"]

    with pytest.raises(
        ValueError,
        match="must define dev, qa and prod",
    ):
        SingleInstanceConfig.model_validate(data)


def test_unknown_configuration_is_sanitized(tmp_path: Path) -> None:
    data = composition_data()
    data["arapi"]["inline_password"] = "do-not-leak"
    source = tmp_path / "helix.yaml"
    source.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(SingleInstanceConfigError) as exc_info:
        load_single_instance_config(source)

    assert "do-not-leak" not in str(exc_info.value)
    assert "arapi.inline_password" in str(exc_info.value)


def test_arapi_cannot_be_disabled() -> None:
    data = composition_data()
    data["arapi"] = {"enabled": False}

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        SingleInstanceConfig.model_validate(data)


def test_schema_version_one_is_rejected_after_write_policy_migration() -> None:
    data = composition_data()
    data["schema_version"] = 1

    with pytest.raises(ValueError, match="Input should be 2"):
        SingleInstanceConfig.model_validate(data)


def test_production_policy_cannot_enable_writes() -> None:
    data = composition_data()
    policy = data["policies"][0]
    policy.update(
        {
            "writable_forms": ["Example:Readable"],
            "creatable_fields_by_form": {"Example:Readable": ["Description"]},
            "updatable_fields_by_form": {"Example:Readable": ["Description"]},
            "access_mode": "read_write",
            "require_human_approval": True,
        }
    )

    with pytest.raises(ValueError, match="prod policy must use"):
        SingleInstanceConfig.model_validate(data)


def test_repository_public_config_is_safe_by_default() -> None:
    config = load_single_instance_config(CURRENT_CONFIG)
    by_name = {policy.name: policy for policy in config.policies}
    dev = by_name[config.policy_by_environment[Environment.DEV]]
    qa = by_name[config.policy_by_environment[Environment.QA]]
    prod = by_name[config.policy_by_environment[Environment.PROD]]

    assert dev.access_mode is AccessMode.READ_ONLY
    assert dev.allow_form_reads is True
    assert dev.allowed_forms == ("Example:ReadableForm",)
    assert dev.allowed_fields_by_form == {
        "Example:ReadableForm": ("Request ID", "Status")
    }
    assert dev.writable_forms == ()
    assert dev.creatable_fields_by_form == {}
    assert dev.updatable_fields_by_form == {}
    assert dev.allow_sql is False
    assert dev.max_rows == 25

    assert qa is prod
    assert qa.access_mode is AccessMode.READ_ONLY
    assert qa.allow_form_reads is False
    assert qa.allowed_forms == ()
    assert qa.allowed_fields_by_form == {}
    assert qa.writable_forms == ()
    assert qa.creatable_fields_by_form == {}
    assert qa.updatable_fields_by_form == {}
    assert qa.allow_sql is False
    assert qa.allow_all_sql_objects is False
    assert qa.allowed_sql_objects == ()
    assert qa.max_rows == 10
