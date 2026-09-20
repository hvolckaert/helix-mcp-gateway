"""Tests for composing the fixed runtime target context."""

from __future__ import annotations

from pathlib import Path

from helix_mcp.config import BackendKind, Environment, TargetKey
from helix_mcp.targeting import TargetAvailability, load_runtime_target_context


def test_context_builds_three_environments_without_kaazing_input(
    tmp_path: Path,
) -> None:
    config = tmp_path / "helix.yaml"
    config.write_text(
        """
schema_version: 2
policies:
  - name: dev_read
    allow_all_forms: true
    allow_all_fields: true
    allow_form_reads: true
  - name: locked
    allow_form_reads: false
policy_by_environment:
  dev: dev_read
  qa: locked
  prod: locked
arapi:
  bridge_base_url: http://127.0.0.1:8090
""".lstrip(),
        encoding="utf-8",
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"HELIX_CONFIG_PATH={config}\n",
        encoding="utf-8",
    )

    context = load_runtime_target_context(dotenv, environ={})

    assert len(context.registry) == 3
    dev = context.registry.get(TargetKey(environment=Environment.DEV))
    qa = context.registry.get(TargetKey(environment=Environment.QA))
    assert dev.enabled_backends == frozenset({BackendKind.ARAPI})
    assert qa.enabled_backends == frozenset({BackendKind.ARAPI})
    assert dev.arapi.credentials.key == "HELIX_CREDENTIAL_DEV"
    assert qa.arapi.gateway_port == 47_000
    assert context.registry.list_descriptors() == ()
    assert {
        descriptor.availability
        for descriptor in context.registry.list_descriptors(
            include_disabled=True
        )
    } == {TargetAvailability.CREDENTIAL_NOT_CONFIGURED}


def test_context_enables_only_environments_with_configured_credentials(
    tmp_path: Path,
) -> None:
    config = tmp_path / "helix.yaml"
    config.write_text(
        """
schema_version: 2
policies:
  - name: read_only
    allow_all_forms: true
    allow_all_fields: true
    allow_form_reads: true
policy_by_environment:
  dev: read_only
  qa: read_only
  prod: read_only
arapi:
  bridge_base_url: http://127.0.0.1:8090
""".lstrip(),
        encoding="utf-8",
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"HELIX_CONFIG_PATH={config}\n"
        'HELIX_CREDENTIAL_DEV={"username":"dev","password":"secret"}\n',
        encoding="utf-8",
    )
    dotenv.chmod(0o600)

    context = load_runtime_target_context(dotenv, environ={})

    descriptors = context.registry.list_descriptors()
    assert [descriptor.environment for descriptor in descriptors] == [
        Environment.DEV
    ]
    assert descriptors[0].availability is TargetAvailability.AVAILABLE
    all_descriptors = context.registry.list_descriptors(include_disabled=True)
    assert [descriptor.availability for descriptor in all_descriptors] == [
        TargetAvailability.AVAILABLE,
        TargetAvailability.CREDENTIAL_NOT_CONFIGURED,
        TargetAvailability.CREDENTIAL_NOT_CONFIGURED,
    ]
    assert all_descriptors[1].backends == ()
    assert all_descriptors[1].capabilities.form_read is False
    assert all_descriptors[1].capabilities.health_check is False
