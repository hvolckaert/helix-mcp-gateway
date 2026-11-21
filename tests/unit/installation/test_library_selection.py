"""Fresh installs, supervised library choices and reuse of an existing configuration."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path

import pytest
from dotenv import dotenv_values

from helix_mcp.clients.arapi.process import bridge_classpath
from helix_mcp.installation.bridge import BridgeBuildResult
from helix_mcp.installation.cli import _prompt_library_choice, setup_main
from helix_mcp.installation.libraries import (
    LibrarySetupError,
    prepare_library_links,
)
from helix_mcp.installation.setup import setup_installation
from tests.support.java_bridge import write_arapi_folder

setup = import_module("helix_mcp.installation.setup")


def write_sdk(
    folder: Path,
    name: str = "cmdbapi251.jar",
    version: str = "25.1.00-SNAPSHOT",
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    jar = folder / name
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("com/bmc/cmdb/api/CMDBInstance.class", b"test")
        archive.writestr(
            "META-INF/maven/com.bmc.cmdb/cmdbapi/pom.properties",
            f"version={version}\n",
        )
    return jar


@pytest.fixture
def vendor(tmp_path, monkeypatch):
    root = tmp_path / "BMC Software"
    studio = (
        root
        / "ARSystem25/DeveloperStudio/plugins/com.bmc.arsys.studio.api_25/lib"
    )
    ar = write_arapi_folder(studio)
    with zipfile.ZipFile(ar / "dependency.jar", "w") as archive:
        archive.writestr("dep-marker", b"test")
    sdk = write_sdk(root / "ARSystem25/diserver/data-integration/lib")
    (sdk.parent / "keep-in-vendor.jar").write_bytes(
        b"relative manifest dependency"
    )
    monkeypatch.setattr(setup, "_KNOWN_ARAPI_ROOTS", (root,))
    monkeypatch.delenv("HELIX_ARAPI_LIB_DIR", raising=False)
    monkeypatch.setattr(setup, "jdk_executable", lambda home: "java")

    def build(libs, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"bridge")
        return BridgeBuildResult(dest, "test", "hash")

    monkeypatch.setattr(setup, "build_bridge", build)
    return root, ar, sdk


def paths(tmp_path):
    return {
        f"{key}_dir": tmp_path / key for key in ("config", "data", "state")
    }


def test_setup_links_and_reuses_vendor_jars(tmp_path, vendor):
    _, ar, sdk = vendor
    dest = paths(tmp_path)
    preview = setup_installation(**dest, dry_run=True)
    assert (
        preview.selected_libraries.arapi_version
        == preview.selected_libraries.cmdb_version
        == "25.1.00-SNAPSHOT"
    )
    assert preview.selected_libraries.cmdb_jar == sdk
    assert not dest["data_dir"].exists()

    result = setup_installation(**dest)
    folder = result.arapi_lib_dir
    assert folder == preview.arapi_lib_dir
    assert result.bridge_built and not result.pending
    assert all(p.is_symlink() for p in folder.iterdir())
    assert {p.name: p.resolve() for p in folder.iterdir()} == {
        p.name: p for p in [*ar.glob("*.jar"), sdk]
    }
    assert dotenv_values(result.dotenv_path)["HELIX_ARAPI_LIB_DIR"] == str(
        folder
    )
    original = result.dotenv_path.read_bytes()
    # The managed updater passes the already saved folder back to setup.
    second = setup_installation(**dest, arapi_lib_dir=folder)
    assert second.arapi_lib_dir == folder and second.bridge_built
    assert second.dotenv_path.read_bytes() == original and list(
        folder.parent.iterdir()
    ) == [folder]


def test_existing_manual_configuration_stays_in_place(tmp_path, vendor):
    _, ar, _ = vendor
    dest = paths(tmp_path)
    dest["config_dir"].mkdir()
    env = dest["config_dir"] / ".env"
    env.write_text(f"HELIX_ARAPI_LIB_DIR={ar}\n# operator setting\n")
    before = env.read_bytes()
    result = setup_installation(**dest)
    assert (
        result.arapi_lib_dir == ar
        and result.selected_libraries.cmdb_jar is None
    )
    assert env.read_bytes() == before
    assert not (dest["data_dir"] / "vendor-lib").exists()


def test_finish_pending_setup_without_losing_settings(
    tmp_path, vendor, monkeypatch
):
    root, _, _ = vendor
    dest = paths(tmp_path)
    monkeypatch.setattr(setup, "_KNOWN_ARAPI_ROOTS", ())
    pending = setup_installation(**dest)
    assert "arapi" in pending.pending
    with pending.dotenv_path.open("a") as stream:
        stream.write("# keep this value\nCUSTOM_LOCAL_VALUE=kept\n")
    monkeypatch.setattr(setup, "_KNOWN_ARAPI_ROOTS", (root,))
    done = setup_installation(**dest)
    assert done.bridge_built
    values = dotenv_values(done.dotenv_path)
    assert values["HELIX_ARAPI_LIB_DIR"] == str(done.arapi_lib_dir)
    assert values["CUSTOM_LOCAL_VALUE"] == "kept"


def test_choices_are_reported_and_operator_can_select(
    tmp_path, vendor, monkeypatch, capsys
):
    _, ar, sdk = vendor
    other = write_sdk(sdk.parent, name="cmdbapi-alternate.jar")
    different = write_sdk(sdk.parent, name="cmdbapi-old.jar", version="21.3")
    options = [
        "--config-dir",
        str(tmp_path / "config"),
        "--data-dir",
        str(tmp_path / "data"),
        "--state-dir",
        str(tmp_path / "state"),
        "--no-managed",
        "--no-dashboard",
    ]
    assert setup_main([*options, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["pending"] == ["library_selection"]
    assert {c["cmdb_jar"] for c in preview["library_choices"]} == {
        str(other),
        str(sdk),
    }
    assert str(different) in [s["jar"] for s in preview["detected_cmdb_sdks"]]
    assert setup_main(options) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["library_choices"] == preview["library_choices"]
    assert (
        not (tmp_path / "config").exists() and not (tmp_path / "data").exists()
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.ensure_managed_github_cli",
        lambda workspace: type("Cli", (), {"to_dict": lambda self: {}})(),
    )
    assert (
        setup_main(
            [*options, "--arapi-lib-dir", str(ar), "--cmdb-sdk", str(other)]
        )
        == 0
    )
    done = json.loads(capsys.readouterr().out)
    assert done["selected_libraries"]["cmdb_jar"] == str(other)
    assert (Path(done["arapi_lib_dir"]) / other.name).resolve() == other


def test_interactive_menu_respects_choice_and_repeat_prompt(
    tmp_path, vendor, monkeypatch, capsys
):
    _, ar, sdk = vendor
    other = write_sdk(sdk.parent, name="cmdbapi-two.jar")

    preview = setup_installation(**paths(tmp_path), dry_run=True)
    monkeypatch.setattr("sys.stdin", io.StringIO("99\n2\n"))
    result = setup_installation(
        **paths(tmp_path), choose_libraries=_prompt_library_choice
    )
    assert result.selected_libraries == preview.library_choices[1]
    stderr = capsys.readouterr().err
    assert str(ar) in stderr and str(sdk) in stderr and str(other) in stderr
    assert "25.1.00-SNAPSHOT" in stderr


def test_explicit_arapi_only_excludes_colocated_sdk(tmp_path, vendor):
    _, ar, _ = vendor
    write_sdk(ar)
    done = setup_installation(
        **paths(tmp_path), arapi_lib_dir=ar, arapi_only=True
    )
    assert done.selected_libraries.cmdb_jar is None
    assert not list(done.arapi_lib_dir.glob("cmdbapi*.jar"))
    assert (ar / "cmdbapi251.jar").is_file()


def test_link_failures_leave_no_partial_selection(
    tmp_path, vendor, monkeypatch
):
    _, ar, sdk = vendor
    dest = paths(tmp_path)
    original = Path.symlink_to

    def fail_on_sdk(link, target, **kwargs):
        if Path(target) == sdk:
            raise PermissionError("symlink unavailable")
        return original(link, target, **kwargs)

    monkeypatch.setattr(Path, "symlink_to", fail_on_sdk)
    with pytest.raises(LibrarySetupError, match="symbolic links"):
        setup_installation(**dest, arapi_lib_dir=ar, cmdb_sdk=sdk)
    assert not (dest["config_dir"] / ".env").exists()
    assert not list((dest["data_dir"] / "vendor-lib").iterdir())
    assert sdk.is_file()


def test_concurrent_link_preparation_and_changed_destination(tmp_path, vendor):

    choice = setup_installation(
        **paths(tmp_path), dry_run=True
    ).selected_libraries
    data = tmp_path / "data"
    with ThreadPoolExecutor(max_workers=2) as pool:
        folders = list(
            pool.map(
                lambda _: prepare_library_links(data, choice, dry_run=False),
                range(2),
            )
        )
    assert folders[0] == folders[1]
    foreign = folders[0] / "dependency.jar"
    foreign.unlink()
    foreign.write_text("keep me")
    with pytest.raises(LibrarySetupError, match="do not match"):
        prepare_library_links(data, choice, dry_run=False)
    assert foreign.read_text() == "keep me"


def test_explicit_invalid_sdk_fails_before_configuration(tmp_path, vendor):
    _, ar, _ = vendor
    with pytest.raises(LibrarySetupError, match="valid cmdbapi"):
        setup_installation(
            **paths(tmp_path), arapi_lib_dir=ar, cmdb_sdk=tmp_path / "missing"
        )
    assert not (tmp_path / "config").exists()


@pytest.mark.skipif(shutil.which("java") is None, reason="Java required")
def test_linked_sdk_keeps_manifest_dependency_visible_from_java(
    tmp_path, vendor
):
    _, ar, sdk = vendor
    neighbor = sdk.parent / "keep-in-vendor.jar"
    with zipfile.ZipFile(neighbor, "w") as archive:
        archive.writestr("vendor-marker", b"ok")
    with zipfile.ZipFile(sdk, "a") as archive:
        archive.writestr(
            "META-INF/MANIFEST.MF",
            "Manifest-Version: 1.0\nClass-Path: keep-in-vendor.jar\n\n",
        )
    done = setup_installation(
        **paths(tmp_path), arapi_lib_dir=ar, cmdb_sdk=sdk
    )
    assert not (done.arapi_lib_dir / neighbor.name).exists()
    bridge = tmp_path / "empty-bridge.jar"
    with zipfile.ZipFile(bridge, "w"):
        pass
    source = tmp_path / "Probe.java"
    source.write_text(
        "class Probe { public static void main(String[] args) {"
        'if (ClassLoader.getSystemResource("vendor-marker") == null) '
        'throw new IllegalStateException("Missing dependency"); }}'
    )
    subprocess.run(
        [
            "java",
            "-cp",
            bridge_classpath(bridge, done.arapi_lib_dir),
            str(source),
        ],
        check=True,
        timeout=30,
    )


def test_rerun_after_failed_build_can_select_a_different_sdk(
    tmp_path, vendor, monkeypatch
):
    from helix_mcp.installation.bridge import BridgeBuildError

    _, ar, sdk = vendor
    with monkeypatch.context() as patch:

        def bad_build(*args, **kwargs):
            raise BridgeBuildError("incompatible SDK")

        patch.setattr(setup, "build_bridge", bad_build)
        failed = setup_installation(
            **paths(tmp_path), arapi_lib_dir=ar, cmdb_sdk=sdk
        )
    assert "bridge" in failed.pending
    assert dotenv_values(failed.dotenv_path)["HELIX_ARAPI_LIB_DIR"] == ""
    retry = setup_installation(
        **paths(tmp_path), arapi_lib_dir=ar, arapi_only=True
    )
    assert retry.bridge_built and dotenv_values(retry.dotenv_path)[
        "HELIX_ARAPI_LIB_DIR"
    ] == str(retry.arapi_lib_dir)
