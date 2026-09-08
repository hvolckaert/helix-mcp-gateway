"""Tests for application dependency composition."""

from __future__ import annotations

from pathlib import Path

from helix_mcp.bootstrap import _bridge_auth_token
from helix_mcp.config import RuntimeSettings


def _settings(key_path: Path | None = None) -> RuntimeSettings:
    return RuntimeSettings(
        config_path=Path("config.yaml"),
        write_plan_db_path=(
            key_path.with_suffix(".sqlite3") if key_path else None
        ),
        write_plan_key_path=key_path,
    )


def test_bridge_auth_token_is_stable_for_one_persisted_installation(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "plans.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)

    first = _bridge_auth_token(_settings(key_path))
    second = _bridge_auth_token(_settings(key_path))

    assert first == second
    assert len(first) == 43
    assert "k" * 16 not in first


def test_bridge_auth_token_is_ephemeral_without_persistent_state() -> None:
    first = _bridge_auth_token(_settings())
    second = _bridge_auth_token(_settings())

    assert first != second
