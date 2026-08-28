"""Atomic local compilation of the packaged ARAPI bridge."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from helix_mcp.clients.arapi import validate_arapi_libraries
from helix_mcp.installation.resources import bridge_source_path

_MAIN_CLASS_FILE = Path("com/example/helix/bridge/ArapiBridge.class")
_BUILD_TIMEOUT_SECONDS = 120
_SAFE_ENVIRONMENT_KEYS = (
    "JAVA_HOME",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)


class BridgeBuildError(RuntimeError):
    """Sanitized local bridge compilation failure."""

    code = "ARAPI_BRIDGE_BUILD_ERROR"


@dataclass(frozen=True, slots=True)
class BridgeBuildResult:
    """Safe metadata for one installed bridge artifact."""

    output_path: Path
    package_version: str
    source_sha256: str


def build_bridge(
    arapi_lib_dir: str | Path,
    output_path: str | Path,
) -> BridgeBuildResult:
    """Compile the packaged bridge and atomically install its JAR."""

    libraries = validate_arapi_libraries(Path(arapi_lib_dir))
    output = Path(output_path).expanduser().absolute()
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise BridgeBuildError("bridge output path is not a regular file")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise BridgeBuildError(
            "bridge output directory is unavailable"
        ) from None

    java = shutil.which("java")
    if java is None:
        raise BridgeBuildError("Java runtime is unavailable")
    package_version = version("helix-mcp-gateway")

    with bridge_source_path() as source:
        try:
            payload = source.read_bytes()
        except OSError:
            raise BridgeBuildError(
                "packaged bridge source is unavailable"
            ) from None
        source_sha256 = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory(
            prefix=".helix-mcp-bridge-",
            dir=output.parent,
        ) as temporary_name:
            temporary = Path(temporary_name)
            classes = temporary / "classes"
            classes.mkdir()
            candidate = temporary / output.name
            manifest = temporary / "MANIFEST.MF"
            manifest.write_text(
                "Manifest-Version: 1.0\n"
                f"Implementation-Version: {package_version}\n"
                "Helix-MCP-Protocol-Version: 1\n"
                f"Helix-MCP-Source-SHA256: {source_sha256}\n\n",
                encoding="utf-8",
            )
            _run_java(
                java,
                (
                    "--module",
                    "jdk.compiler/com.sun.tools.javac.Main",
                    "-proc:none",
                    "-source",
                    "17",
                    "-target",
                    "17",
                    "-encoding",
                    "UTF-8",
                    "-classpath",
                    str(libraries.arapi),
                    "-d",
                    str(classes),
                    str(source),
                ),
            )
            _run_java(
                java,
                (
                    "--module",
                    "jdk.jartool/sun.tools.jar.Main",
                    "--create",
                    "--file",
                    str(candidate),
                    "--manifest",
                    str(manifest),
                    "-C",
                    str(classes),
                    ".",
                ),
            )
            _validate_candidate(candidate)
            try:
                os.replace(candidate, output)
                if os.name != "nt":
                    output.chmod(0o644)
            except OSError:
                raise BridgeBuildError(
                    "compiled bridge could not be installed"
                ) from None
    return BridgeBuildResult(
        output_path=output,
        package_version=package_version,
        source_sha256=source_sha256,
    )


def _run_java(java: str, arguments: tuple[str, ...]) -> None:
    environment = {
        key: value
        for key in _SAFE_ENVIRONMENT_KEYS
        if (value := os.environ.get(key)) is not None
    }
    try:
        completed = subprocess.run(
            (java, *arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=_BUILD_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        raise BridgeBuildError("Java build process failed") from None
    if completed.returncode != 0:
        raise BridgeBuildError("Java build process failed")


def _validate_candidate(candidate: Path) -> None:
    try:
        if candidate.stat().st_size < 1:
            raise BridgeBuildError("compiled bridge is empty")
        with zipfile.ZipFile(candidate) as archive:
            if _MAIN_CLASS_FILE.as_posix() not in archive.namelist():
                raise BridgeBuildError("compiled bridge is incomplete")
    except (OSError, zipfile.BadZipFile):
        raise BridgeBuildError("compiled bridge is invalid") from None
