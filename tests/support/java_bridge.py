"""Build an isolated Java bridge runtime without proprietary BMC binaries."""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path


def build_test_runtime(tmp_path: Path) -> tuple[Path, Path]:
    """Return a bridge JAR and a minimal manifest-valid ARAPI library set."""

    java = shutil.which("java")
    if java is None:
        raise RuntimeError("Java is required for the Java bridge tests")

    root = Path(__file__).parents[2]
    main_source = (
        root / "arapi-bridge/src/main/java/com/example/helix/bridge/"
        "ArapiBridge.java"
    )
    stub_sources = sorted(
        (root / "arapi-bridge/src/test/java/com/bmc/arsys/api").glob("*.java")
    )
    classes = tmp_path / "classes"
    classes.mkdir()
    _run_java(
        java,
        "--module",
        "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none",
        "-source",
        "17",
        "-target",
        "17",
        "-encoding",
        "UTF-8",
        "-Xlint:all,-options",
        "-Werror",
        "-d",
        str(classes),
        str(main_source),
        *(str(source) for source in stub_sources),
    )

    bridge_jar = tmp_path / "bridge.jar"
    _create_jar(
        java,
        bridge_jar,
        classes,
        "com/example/helix/bridge",
    )

    library_dir = tmp_path / "lib"
    library_dir.mkdir()
    manifest = tmp_path / "BMC-MANIFEST.MF"
    manifest.write_text(
        "Manifest-Version: 1.0\n"
        "Implementation-Vendor: BMC Software\n"
        "Implementation-Version: 21.30.07-SNAPSHOT\n\n",
        encoding="utf-8",
    )
    _create_jar(
        java,
        library_dir / "arapi2130_build007.jar",
        classes,
        "com/bmc/arsys/api",
        manifest=manifest,
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    for name in (
        "arapiext2130_build007.jar",
        "arlogger-21.30.07-SNAPSHOT.jar",
    ):
        _create_jar(
            java,
            library_dir / name,
            empty,
            ".",
            manifest=manifest,
        )
    return bridge_jar, library_dir


def available_port() -> int:
    """Reserve and release one currently available IPv4 loopback port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _create_jar(
    java: str,
    output: Path,
    source: Path,
    entry: str,
    *,
    manifest: Path | None = None,
) -> None:
    arguments = [
        "--module",
        "jdk.jartool/sun.tools.jar.Main",
        "--create",
        "--file",
        str(output),
    ]
    if manifest is not None:
        arguments.extend(("--manifest", str(manifest)))
    arguments.extend(("-C", str(source), entry))
    _run_java(java, *arguments)


def _run_java(java: str, *arguments: str) -> None:
    subprocess.run(
        (java, *arguments),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=60,
    )
