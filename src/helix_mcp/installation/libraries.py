"""Discover BMC Java libraries and assemble a local classpath of symbolic links."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from helix_mcp.clients.arapi import (
    ArapiBridgeProcessError,
    validate_arapi_libraries,
)
from helix_mcp.clients.arapi.process import inspect_cmdb_jar


class LibrarySetupError(RuntimeError):
    """Safe, actionable setup diagnostic containing no configuration secrets."""

    code = "BMC_LIBRARY_SETUP_ERROR"


@dataclass(frozen=True, slots=True)
class LibraryChoice:
    arapi_dir: Path
    arapi_jar: str
    arapi_version: str
    cmdb_jar: Path | None = None
    cmdb_version: str | None = None


class LibraryChoiceRequired(LibrarySetupError):
    code = "BMC_LIBRARY_SELECTION_REQUIRED"

    def __init__(self, choices: tuple[LibraryChoice, ...]) -> None:
        self.choices = choices
        super().__init__(
            "Select the AR API folder and CMDB SDK, or choose AR API only."
        )


@dataclass(frozen=True, slots=True)
class SdkCandidate:
    jar: Path
    version: str | None


def find_cmdb_sdks(
    arapi_dirs: Iterable[Path],
    roots: Iterable[Path],
    *,
    explicit: Path | None = None,
) -> tuple[SdkCandidate, ...]:
    if explicit is not None:
        files = (
            sorted(explicit.glob("cmdbapi*.jar"))
            if explicit.is_dir()
            else [explicit]
        )
    else:
        arapi_dirs = tuple(arapi_dirs)
        directories = set(arapi_dirs)
        for root in roots:
            try:
                directories.update(
                    root.glob("ARSystem*/diserver/data-integration/lib")
                )
                directories.update(root.glob("*/data-integration/lib"))
            except OSError:
                continue
        for directory in arapi_dirs:
            for parent in directory.parents:
                if parent.name.startswith("DeveloperStudio"):
                    directories.add(
                        parent.parent / "diserver/data-integration/lib"
                    )
                    break
        files = sorted(
            {
                jar
                for directory in directories
                for jar in directory.glob("cmdbapi*.jar")
            }
        )

    candidates = []
    for jar in files:
        if jar.match("cmdbapi*.jar"):
            report = inspect_cmdb_jar(jar)
            if report.status == "detected":
                candidates.append(SdkCandidate(jar, report.version))
    if explicit is not None and not candidates:
        raise LibrarySetupError(
            "The specified CMDB SDK must contain a valid cmdbapi JAR."
        )
    return tuple(candidates)


def library_choices(
    arapi_dirs: Iterable[Path],
    sdks: tuple[SdkCandidate, ...],
    *,
    explicit_sdk: bool,
    explicit_arapi: bool,
) -> tuple[LibraryChoice, ...]:
    """Offer colocated SDKs and external SDKs with matching artifact versions."""
    choices: list[LibraryChoice] = []
    for directory in sorted(set(arapi_dirs)):
        try:
            ar = validate_arapi_libraries(directory)
        except ArapiBridgeProcessError:
            continue
        matches = {
            sdk.jar.resolve(): sdk
            for sdk in sdks
            if (explicit_sdk and explicit_arapi)
            or sdk.jar.parent == directory
            or sdk.version == ar.version
        }
        if matches:
            choices.extend(
                LibraryChoice(
                    directory, ar.arapi.name, ar.version, sdk.jar, sdk.version
                )
                for sdk in matches.values()
            )
        elif not explicit_sdk:
            choices.append(LibraryChoice(directory, ar.arapi.name, ar.version))
    return tuple(choices)


def prepare_library_links(
    data_dir: Path,
    choice: LibraryChoice,
    *,
    dry_run: bool,
    saved_dir: Path | None = None,
) -> Path:
    """Publish a complete, reusable link directory without modifying vendor files."""
    try:
        links = {
            path.name: path.resolve(strict=True)
            for path in choice.arapi_dir.glob("*.jar")
            if path.is_file() and not path.match("cmdbapi*.jar")
        }
        if choice.cmdb_jar:
            links[choice.cmdb_jar.name] = choice.cmdb_jar.resolve(strict=True)
        identity = sorted(
            (name, str(target)) for name, target in links.items()
        )
        digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        parent = data_dir / "vendor-lib"
        destination = parent / digest[:20]
        if saved_dir is not None and saved_dir != destination:
            raise LibrarySetupError(
                "This installation already has a different saved library folder. Use the Dashboard to change it."
            )
        if dry_run:
            return destination
        if parent.is_symlink() or parent.is_junction():
            raise LibrarySetupError(
                "The managed vendor-lib directory must be a local directory."
            )
        parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with tempfile.TemporaryDirectory(dir=parent) as temporary:
                staged = Path(temporary) / "lib"
                staged.mkdir()
                for name, target in links.items():
                    (staged / name).symlink_to(target)
                try:
                    staged.rename(destination)
                except OSError:
                    if not destination.is_dir():
                        raise
        if (
            destination.is_symlink()
            or destination.is_junction()
            or not destination.is_dir()
            or {p.name for p in destination.iterdir()} != set(links)
            or not all(
                (destination / name).is_symlink()
                and (destination / name).resolve(strict=True) == target
                for name, target in links.items()
            )
        ):
            raise LibrarySetupError(
                "The existing managed library links do not match the selection."
            )
        return destination
    except OSError as exc:
        raise LibrarySetupError(
            "Cannot prepare BMC library links. Check the source files and write permissions. "
            "On Windows, enable Developer Mode or use an account permitted to create symbolic links."
        ) from exc
