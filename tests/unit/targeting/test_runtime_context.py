"""Tests for composing the fixed runtime target context."""

from __future__ import annotations

from pathlib import Path

from helix_mcp.config import BackendKind, Environment, TargetKey
from helix_mcp.targeting import load_runtime_target_context


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
