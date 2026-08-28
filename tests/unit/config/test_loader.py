"""Tests for bounded and sanitized YAML configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from helix_mcp.config import (
    ConfigEncodingError,
    ConfigLoader,
    ConfigSizeError,
    ConfigSourceError,
    ConfigStructureError,
    ConfigSyntaxError,
    SingleInstanceConfigError,
    load_single_instance_config,
)

PROJECT_ROOT = Path(__file__).parents[3]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "helix.example.yaml"

MINIMAL_CONFIG = """
schema_version: 1
settings:
  display_name: Helix DEV
"""


def write_text(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_repository_example_loads_successfully() -> None:
    config = load_single_instance_config(EXAMPLE_CONFIG)

    assert set(item.value for item in config.policy_by_environment) == {
        "dev",
        "qa",
        "prod",
    }
    assert str(config.arapi.bridge_base_url) == "http://127.0.0.1:8090/"


def test_missing_directory_and_symlink_sources_are_rejected(
    tmp_path: Path,
) -> None:
    loader = ConfigLoader()

    with pytest.raises(ConfigSourceError, match="unavailable"):
        loader.load_mapping(tmp_path / "missing.yaml")
    with pytest.raises(ConfigSourceError, match="not a regular file"):
        loader.load_mapping(tmp_path)

    real = write_text(tmp_path / "real.yaml", MINIMAL_CONFIG)
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    with pytest.raises(ConfigSourceError, match="symbolic links"):
        loader.load_mapping(link)

    real_directory = tmp_path / "real-directory"
    real_directory.mkdir()
    write_text(real_directory / "nested.yaml", MINIMAL_CONFIG)
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(ConfigSourceError, match="symbolic links"):
        loader.load_mapping(linked_directory / "nested.yaml")


def test_allowed_root_blocks_paths_outside_it(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = write_text(tmp_path / "outside.yaml", MINIMAL_CONFIG)

    with pytest.raises(ConfigSourceError, match="outside the allowed root"):
        ConfigLoader(allowed_root=allowed).load_mapping(outside)


def test_file_size_is_bounded_before_parsing(tmp_path: Path) -> None:
    source = write_text(tmp_path / "large.yaml", MINIMAL_CONFIG)

    with pytest.raises(ConfigSizeError, match="32-byte limit"):
        ConfigLoader(max_bytes=32).load_mapping(source)


def test_configuration_must_be_utf8(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ConfigEncodingError, match="must be UTF-8"):
        ConfigLoader().load_mapping(source)


def test_yaml_errors_do_not_echo_source_content(tmp_path: Path) -> None:
    source = write_text(
        tmp_path / "invalid.yaml",
        "password: [do-not-echo-this-value\n",
    )

    with pytest.raises(ConfigSyntaxError) as exc_info:
        ConfigLoader().load_mapping(source)

    rendered = str(exc_info.value)
    assert "do-not-echo-this-value" not in rendered
    assert exc_info.value.line == 2
    assert exc_info.value.column is not None


def test_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    source = write_text(
        tmp_path / "duplicate.yaml",
        "schema_version: 1\nschema_version: 1\n",
    )

    with pytest.raises(ConfigSyntaxError, match="duplicate YAML mapping key"):
        ConfigLoader().load_mapping(source)


def test_alias_and_depth_limits_are_enforced(tmp_path: Path) -> None:
    aliases = write_text(
        tmp_path / "aliases.yaml",
        "base: &base {value: safe}\none: *base\ntwo: *base\n",
    )
    nested = write_text(
        tmp_path / "nested.yaml",
        "one:\n  two:\n    three:\n      four: value\n",
    )

    with pytest.raises(ConfigSyntaxError, match="alias limit exceeded"):
        ConfigLoader(max_aliases=1).load_mapping(aliases)
    with pytest.raises(ConfigSyntaxError, match="depth limit exceeded"):
        ConfigLoader(max_depth=3).load_mapping(nested)


def test_multiple_documents_and_non_mapping_roots_are_rejected(
    tmp_path: Path,
) -> None:
    multiple = write_text(
        tmp_path / "multiple.yaml",
        "schema_version: 1\n---\nschema_version: 1\n",
    )
    sequence = write_text(tmp_path / "sequence.yaml", "- one\n- two\n")

    with pytest.raises(ConfigSyntaxError, match="invalid YAML syntax"):
        ConfigLoader().load_mapping(multiple)
    with pytest.raises(ConfigStructureError, match="root must be a mapping"):
        ConfigLoader().load_mapping(sequence)


def test_python_object_tags_are_not_constructed(tmp_path: Path) -> None:
    source = write_text(
        tmp_path / "object.yaml",
        "value: !!python/object/apply:builtins.str [unsafe]\n",
    )

    with pytest.raises(ConfigSyntaxError, match="invalid YAML syntax"):
        ConfigLoader().load_mapping(source)


def test_schema_errors_remain_sanitized(tmp_path: Path) -> None:
    source = write_text(
        tmp_path / "schema.yaml",
        f"{MINIMAL_CONFIG}\ninline_password: do-not-echo-this-value\n",
    )

    with pytest.raises(SingleInstanceConfigError) as exc_info:
        load_single_instance_config(source)

    assert "do-not-echo-this-value" not in str(exc_info.value)


def test_environment_syntax_is_not_interpolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HELIX_DISPLAY_NAME", "Unexpected Name")
    source = write_text(
        tmp_path / "literal.yaml",
        MINIMAL_CONFIG.replace("Helix DEV", "${HELIX_DISPLAY_NAME}"),
    )

    config = ConfigLoader().load_mapping(source)

    assert config["settings"]["display_name"] == "${HELIX_DISPLAY_NAME}"
