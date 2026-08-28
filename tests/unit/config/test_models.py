"""Tests for strict configuration models and cross-field validation."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from helix_mcp.config import (
    BackendKind,
    ConfigValidationError,
    Environment,
    HelixConfig,
    TargetKey,
    validate_config,
)


def valid_config_data() -> dict[str, object]:
    return {
        "schema_version": 2,
        "server": {
            "transport": "stdio",
            "log_level": "INFO",
        },
        "policies": [
            {
                "name": "dev_read",
                "allowed_forms": ["Example:ComputerSystem"],
                "allowed_sql_objects": ["example_computer_system"],
                "allow_sql": True,
            }
        ],
        "targets": [
            {
                "instance": "helix",
                "environment": "dev",
                "display_name": "Core DEV",
                "policy_ref": "dev_read",
                "arapi": {
                    "bridge_base_url": "http://127.0.0.1:8090",
                    "gateway_port": 46000,
                    "credentials": {
                        "provider": "keyring",
                        "key": "helix/core/dev/arapi",
                    },
                },
            }
        ],
    }


def test_valid_configuration_is_immutable_and_resolves_target_key() -> None:
    config = validate_config(valid_config_data())

    assert isinstance(config, HelixConfig)
    assert config.targets[0].key == TargetKey(
        environment=Environment.DEV,
    )
    assert config.targets[0].enabled_backends == frozenset({BackendKind.ARAPI})

    with pytest.raises(ValidationError, match="Instance is frozen"):
        config.targets[0].display_name = "Changed"


def test_unknown_fields_are_rejected_without_echoing_values() -> None:
    data = valid_config_data()
    data["inline_password"] = "do-not-leak"

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(data)

    assert exc_info.value.issues[0].location == "inline_password"
    assert "do-not-leak" not in str(exc_info.value)


def test_duplicate_target_keys_are_rejected() -> None:
    data = valid_config_data()
    data["targets"].append(deepcopy(data["targets"][0]))

    with pytest.raises(
        ConfigValidationError,
        match="target environments must be unique",
    ):
        validate_config(data)


def test_unknown_policy_reference_is_rejected() -> None:
    data = valid_config_data()
    data["targets"][0]["policy_ref"] = "missing_policy"

    with pytest.raises(ConfigValidationError, match="unknown policy"):
        validate_config(data)


def test_production_policy_must_be_read_only() -> None:
    data = valid_config_data()
    data["targets"][0]["environment"] = "prod"
    data["policies"][0].update(
        {
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
        }
    )

    with pytest.raises(
        ConfigValidationError,
        match="must use access_mode read_only",
    ):
        validate_config(data)


def test_writes_require_an_exact_field_allowlist() -> None:
    data = valid_config_data()
    data["policies"][0].update(
        {
            "writable_forms": ["Example:ComputerSystem"],
            "access_mode": "read_write",
            "require_human_approval": True,
        }
    )

    with pytest.raises(
        ConfigValidationError,
        match="creatable_fields_by_form must define every writable form",
    ):
        validate_config(data)


def test_read_write_access_requires_human_approval() -> None:
    data = valid_config_data()
    data["policies"][0].update(
        {
            "writable_forms": ["Example:ComputerSystem"],
            "creatable_fields_by_form": {"Example:ComputerSystem": ["Name"]},
            "updatable_fields_by_form": {"Example:ComputerSystem": ["Status"]},
            "access_mode": "read_write",
            "require_human_approval": False,
        }
    )

    with pytest.raises(
        ConfigValidationError,
        match="human approval is required when writes are enabled",
    ):
        validate_config(data)


def test_read_write_access_requires_both_operation_allowlists() -> None:
    data = valid_config_data()
    data["policies"][0].update(
        {
            "writable_forms": ["Example:ComputerSystem"],
            "creatable_fields_by_form": {"Example:ComputerSystem": ["Name"]},
            "access_mode": "read_write",
            "require_human_approval": True,
        }
    )

    with pytest.raises(
        ConfigValidationError,
        match="updatable_fields_by_form must define every writable form",
    ):
        validate_config(data)


def test_allow_all_sql_objects_is_explicit_and_mutually_exclusive() -> None:
    data = valid_config_data()
    data["policies"][0].update(
        {
            "allow_all_sql_objects": True,
            "allowed_sql_objects": [],
        }
    )

    config = validate_config(data)

    assert config.policies[0].allow_all_sql_objects is True

    data = valid_config_data()
    data["policies"][0]["allow_all_sql_objects"] = True

    with pytest.raises(
        ConfigValidationError,
        match="allowed_sql_objects must be empty",
    ):
        validate_config(data)


def test_allow_all_sql_objects_requires_sql_to_be_enabled() -> None:
    data = valid_config_data()
    data["policies"][0].update(
        {
            "allow_all_sql_objects": True,
            "allowed_sql_objects": [],
            "allow_sql": False,
        }
    )

    with pytest.raises(
        ConfigValidationError,
        match="requires SQL to be enabled",
    ):
        validate_config(data)


def test_writable_fields_reject_sensitive_names_and_markers() -> None:
    data = valid_config_data()
    data["policies"][0].update(
        {
            "writable_forms": ["Example:ComputerSystem"],
            "creatable_fields_by_form": {
                "Example:ComputerSystem": ["Password"]
            },
            "updatable_fields_by_form": {
                "Example:ComputerSystem": ["Password"]
            },
            "access_mode": "read_write",
            "require_human_approval": True,
            "sensitive_fields": ["password"],
        }
    )

    with pytest.raises(ConfigValidationError, match="sensitive fields"):
        validate_config(data)

    data = valid_config_data()
    data["policies"][0].update(
        {
            "writable_forms": ["Example:ComputerSystem"],
            "creatable_fields_by_form": {
                "Example:ComputerSystem": ["Access Token Value"]
            },
            "updatable_fields_by_form": {
                "Example:ComputerSystem": ["Access Token Value"]
            },
            "access_mode": "read_write",
            "require_human_approval": True,
            "sensitive_field_markers": ["token"],
        }
    )

    with pytest.raises(ConfigValidationError, match="sensitive fields"):
        validate_config(data)


def test_disabled_writes_reject_a_dormant_field_allowlist() -> None:
    data = valid_config_data()
    data["policies"][0]["creatable_fields_by_form"] = {
        "Example:ComputerSystem": ["Name"]
    }

    with pytest.raises(
        ConfigValidationError,
        match="creatable_fields_by_form requires access_mode read_write",
    ):
        validate_config(data)


def test_postgres_backend_is_rejected() -> None:
    data = valid_config_data()
    data["targets"][0]["postgres"] = {"port": 46500}

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(data)

    assert any(
        issue.location == "targets[0].postgres"
        for issue in exc_info.value.issues
    )


def test_arapi_bridge_must_be_on_loopback() -> None:
    data = valid_config_data()
    data["targets"][0]["arapi"] = {
        "bridge_base_url": "http://bridge.internal:8090",
        "gateway_port": 46000,
        "credentials": {
            "provider": "keyring",
            "key": "helix/core/dev/arapi",
        },
    }

    with pytest.raises(ConfigValidationError, match="loopback host"):
        validate_config(data)


def test_arapi_gateway_must_be_on_loopback() -> None:
    data = valid_config_data()
    data["targets"][0]["arapi"] = {
        "bridge_base_url": "http://127.0.0.1:8090",
        "gateway_host": "remote-ar.example.invalid",
        "gateway_port": 46000,
        "credentials": {
            "provider": "keyring",
            "key": "helix/core/dev/arapi",
        },
    }

    with pytest.raises(ConfigValidationError, match="gateway must use"):
        validate_config(data)


def test_stdio_rejects_http_listener_settings() -> None:
    data = valid_config_data()
    data["server"]["http"] = {"host": "127.0.0.1", "port": 8000}

    with pytest.raises(
        ConfigValidationError,
        match="http settings are not valid for stdio",
    ):
        validate_config(data)


def test_allowed_form_fields_must_reference_forms_and_exclude_sensitive() -> (
    None
):
    data = valid_config_data()
    data["policies"][0]["allowed_fields_by_form"] = {
        "Unknown:Form": ["Status"]
    }

    with pytest.raises(
        ConfigValidationError, match="included in allowed_forms"
    ):
        validate_config(data)

    data = valid_config_data()
    data["policies"][0]["sensitive_fields"] = ["Password"]
    data["policies"][0]["allowed_fields_by_form"] = {
        "Example:ComputerSystem": ["Name", "password"]
    }

    with pytest.raises(ConfigValidationError, match="sensitive fields"):
        validate_config(data)


def test_streamable_http_is_loopback_only_without_mcp_authentication() -> None:
    data = valid_config_data()
    data["server"] = {
        "transport": "streamable_http",
        "http": {"host": "0.0.0.0", "port": 8000},
    }

    with pytest.raises(ConfigValidationError, match="must use loopback"):
        validate_config(data)


def test_read_all_policy_is_explicit_and_cannot_mix_allowlist_modes() -> None:
    data = valid_config_data()
    policy = data["policies"][0]
    policy["allow_all_forms"] = True

    with pytest.raises(
        ConfigValidationError, match="allowed_forms must be empty"
    ):
        validate_config(data)

    data = valid_config_data()
    policy = data["policies"][0]
    policy["allow_all_fields"] = True
    policy["allowed_fields_by_form"] = {"Example:ComputerSystem": ["Name"]}

    with pytest.raises(
        ConfigValidationError,
        match="allowed_fields_by_form must be empty",
    ):
        validate_config(data)


def test_sensitive_field_markers_reject_matching_allowlisted_fields() -> None:
    data = valid_config_data()
    policy = data["policies"][0]
    policy["sensitive_field_markers"] = ["token"]
    policy["allowed_fields_by_form"] = {
        "Example:ComputerSystem": ["Access Token Value"]
    }

    with pytest.raises(ConfigValidationError, match="sensitive fields"):
        validate_config(data)
