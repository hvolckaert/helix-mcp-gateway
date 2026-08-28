"""Bounded, safe YAML loading for Helix configuration files."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

DEFAULT_MAX_CONFIG_BYTES = 1_048_576
DEFAULT_MAX_YAML_ALIASES = 50
DEFAULT_MAX_YAML_DEPTH = 64


class ConfigLoadError(ValueError):
    """Base error that identifies a source without echoing its content."""

    code = "CONFIG_LOAD_ERROR"

    def __init__(
        self,
        source: Path,
        message: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        self.source_name = source.name or "<config>"
        self.line = line
        self.column = column
        location = ""
        if line is not None and column is not None:
            location = f":{line}:{column}"
        super().__init__(f"{self.source_name}{location}: {message}")


class ConfigSourceError(ConfigLoadError):
    """The configuration path cannot be opened safely."""

    code = "CONFIG_SOURCE_ERROR"


class ConfigSizeError(ConfigLoadError):
    """The configuration exceeds the configured byte limit."""

    code = "CONFIG_TOO_LARGE"


class ConfigEncodingError(ConfigLoadError):
    """The configuration is not valid UTF-8."""

    code = "CONFIG_ENCODING_ERROR"


class ConfigSyntaxError(ConfigLoadError):
    """YAML syntax or safety constraints were violated."""

    code = "CONFIG_SYNTAX_ERROR"


class ConfigStructureError(ConfigLoadError):
    """The YAML root is not a mapping suitable for configuration."""

    code = "CONFIG_STRUCTURE_ERROR"


@dataclass(frozen=True, slots=True)
class ConfigLoader:
    """Load one YAML document with explicit filesystem and parser limits."""

    allowed_root: Path | None = None
    max_bytes: int = DEFAULT_MAX_CONFIG_BYTES
    max_aliases: int = DEFAULT_MAX_YAML_ALIASES
    max_depth: int = DEFAULT_MAX_YAML_DEPTH
    allow_symlinks: bool = False

    def __post_init__(self) -> None:
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self.max_aliases < 0:
            raise ValueError("max_aliases cannot be negative")
        if self.max_depth < 1:
            raise ValueError("max_depth must be positive")

    def load_mapping(self, path: str | Path) -> Mapping[str, Any]:
        """Load a safe YAML mapping without applying a domain schema."""

        source = Path(path)
        resolved = self._resolve_source(source)
        payload = self._read_bounded(source, resolved)
        text = self._decode(source, payload)
        data = self._parse(source, text)
        if not isinstance(data, Mapping):
            raise ConfigStructureError(
                source,
                "YAML document root must be a mapping",
            )
        return data

    def _resolve_source(self, source: Path) -> Path:
        try:
            if not self.allow_symlinks and _path_contains_symlink(source):
                raise ConfigSourceError(
                    source,
                    "symbolic links are not allowed in the configuration path",
                )
            resolved = source.resolve(strict=True)
        except ConfigSourceError:
            raise
        except (OSError, RuntimeError):
            raise ConfigSourceError(
                source,
                "configuration file is unavailable",
            ) from None

        if not resolved.is_file():
            raise ConfigSourceError(
                source,
                "configuration source is not a regular file",
            )

        if self.allowed_root is not None:
            try:
                root = self.allowed_root.resolve(strict=True)
                if not root.is_dir():
                    raise ValueError
                resolved.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                raise ConfigSourceError(
                    source,
                    "configuration path is outside the allowed root",
                ) from None
        return resolved

    def _read_bounded(self, source: Path, resolved: Path) -> bytes:
        try:
            with resolved.open("rb") as stream:
                payload = stream.read(self.max_bytes + 1)
        except OSError:
            raise ConfigSourceError(
                source,
                "configuration file could not be read",
            ) from None

        if len(payload) > self.max_bytes:
            raise ConfigSizeError(
                source,
                f"configuration exceeds the {self.max_bytes}-byte limit",
            )
        return payload

    def _decode(self, source: Path, payload: bytes) -> str:
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            prefix = payload[: exc.start]
            line = prefix.count(b"\n") + 1
            last_line_break = prefix.rfind(b"\n")
            column = (
                exc.start + 1
                if last_line_break < 0
                else exc.start - last_line_break
            )
            raise ConfigEncodingError(
                source,
                "configuration must be UTF-8",
                line=line,
                column=column,
            ) from None

    def _parse(self, source: Path, text: str) -> Any:
        loader = _LimitedSafeLoader(
            text,
            max_aliases=self.max_aliases,
            max_depth=self.max_depth,
        )
        try:
            return loader.get_single_data()
        except _YamlConstraintError as exc:
            line, column = _mark_location(exc.mark)
            raise ConfigSyntaxError(
                source,
                exc.safe_message,
                line=line,
                column=column,
            ) from None
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line, column = _mark_location(mark)
            raise ConfigSyntaxError(
                source,
                "invalid YAML syntax",
                line=line,
                column=column,
            ) from None
        finally:
            loader.dispose()  # type: ignore[no-untyped-call, unused-ignore]


class _YamlConstraintError(yaml.YAMLError):
    def __init__(self, safe_message: str, mark: object | None) -> None:
        self.safe_message = safe_message
        self.mark = mark
        super().__init__(safe_message)


class _LimitedSafeLoader(yaml.SafeLoader):
    def __init__(
        self,
        stream: str,
        *,
        max_aliases: int,
        max_depth: int,
    ) -> None:
        super().__init__(stream)
        self._alias_count = 0
        self._current_depth = 0
        self._max_aliases = max_aliases
        self._max_depth = max_depth

    def compose_node(
        self,
        parent: Node | None,
        index: int,
    ) -> Node | None:
        if self.check_event(AliasEvent):  # type: ignore[no-untyped-call, unused-ignore]
            self._alias_count += 1
            if self._alias_count > self._max_aliases:
                event = self.peek_event()  # type: ignore[no-untyped-call]
                raise _YamlConstraintError(
                    "YAML alias limit exceeded",
                    event.start_mark,
                )

        self._current_depth += 1
        try:
            if self._current_depth > self._max_depth:
                event = self.peek_event()  # type: ignore[no-untyped-call]
                raise _YamlConstraintError(
                    "YAML nesting depth limit exceeded",
                    event.start_mark,
                )
            return super().compose_node(parent, index)
        finally:
            self._current_depth -= 1

    def construct_mapping(
        self,
        node: MappingNode,
        deep: bool = False,
    ) -> dict[object, object]:
        if not isinstance(node, MappingNode):
            return super().construct_mapping(node, deep=deep)

        self.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError:
                raise _YamlConstraintError(
                    "YAML mapping keys must be scalar",
                    key_node.start_mark,
                ) from None
            if duplicate:
                raise _YamlConstraintError(
                    "duplicate YAML mapping key",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _mark_location(mark: object | None) -> tuple[int | None, int | None]:
    if mark is None:
        return None, None
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    if not isinstance(line, int) or not isinstance(column, int):
        return None, None
    return line + 1, column + 1


def _path_contains_symlink(path: Path) -> bool:
    absolute = path.absolute()
    return any(item.is_symlink() for item in (absolute, *absolute.parents))
