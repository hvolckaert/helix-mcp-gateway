"""Resolve an optional local JDK folder or Java from PATH."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_KNOWN_JAVA_ROOTS = (
    Path("/usr/lib/jvm"),
    Path("/mnt/c/Program Files/Java"),
    Path("/mnt/c/Program Files/Eclipse Adoptium"),
    Path("C:/Program Files/Java"),
    Path("C:/Program Files/Eclipse Adoptium"),
)


def java_executable(java_home: Path | None = None) -> str | None:
    """Return the Java executable for a selected JDK or the process PATH."""

    if java_home is None:
        return shutil.which("java")
    executable = (
        java_home / "bin" / ("java.exe" if os.name == "nt" else "java")
    )
    try:
        is_file = executable.is_file()
    except OSError:
        return None
    if not is_file:
        return None
    if os.name != "nt" and not os.access(executable, os.X_OK):
        return None
    return str(executable)


def jdk_executable(java_home: Path | None = None) -> str | None:
    """Return Java only for a JDK 17+ with compiler and JAR modules."""

    java = java_executable(java_home)
    if java is None:
        return None
    home = (
        java_home
        if java_home is not None
        else Path(java).resolve().parent.parent
    )
    try:
        with (home / "release").open("rb") as stream:
            payload = stream.read(65_537)
        if len(payload) > 65_536:
            return None
        release = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    values = dict(
        line.split("=", 1) for line in release.splitlines() if "=" in line
    )
    version = values.get("JAVA_VERSION", "").strip('"')
    match = re.match(r"^(\d+)", version)
    if match is None or int(match.group(1)) < 17:
        return None
    modules = set(values.get("MODULES", "").strip('"').split())
    if not {"jdk.compiler", "jdk.jartool"} <= modules:
        return None
    return java


def find_java_homes() -> tuple[Path, ...]:
    """List JDK candidate folders in supported common installation roots."""

    candidates = []
    for root in _KNOWN_JAVA_ROOTS:
        try:
            children = tuple(root.iterdir())
        except OSError:
            continue
        for candidate in children:
            if jdk_executable(candidate) is not None:
                candidates.append(candidate.resolve())
    return tuple(dict.fromkeys(candidates))
