"""Access immutable installation resources included in the wheel."""

from __future__ import annotations

from contextlib import AbstractContextManager
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path

_ROOT = files("helix_mcp").joinpath("installation", "resources")
_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]


def _resource(packaged: Traversable, checkout: Path) -> Traversable:
    return packaged if packaged.is_file() else checkout


def bridge_source_path() -> AbstractContextManager[Path]:
    """Return a context manager exposing the packaged Java source."""

    return as_file(
        _resource(
            _ROOT.joinpath("bridge", "ArapiBridge.java"),
            _CHECKOUT_ROOT
            / "arapi-bridge/src/main/java/com/example/helix/bridge/ArapiBridge.java",
        )
    )


def read_config_template() -> str:
    """Return the packaged generic YAML configuration template."""

    return _resource(
        _ROOT.joinpath("config", "helix.example.yaml"),
        _CHECKOUT_ROOT / "config/helix.example.yaml",
    ).read_text(encoding="utf-8")


def validate_packaged_resources() -> None:
    """Fail safely when a wheel omitted required installation resources."""

    with bridge_source_path() as source:
        if not source.is_file() or source.stat().st_size < 1:
            raise FileNotFoundError("packaged bridge source is unavailable")
    if not read_config_template().strip():
        raise FileNotFoundError("packaged configuration is unavailable")
