"""Real Python-to-Java contract test with an isolated ARAPI double."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from helix_mcp.clients.arapi import (
    ArapiBridgeClient,
    ArapiBridgeConflictError,
    ArapiBridgeProcess,
    ArapiBridgeProtocolError,
)
from helix_mcp.config import (
    ArapiBackendConfig,
    Environment,
    RuntimeSettings,
    SecretProviderKind,
    SecretRef,
    TargetKey,
)
from helix_mcp.secrets import EnvironmentSecretProvider, SecretResolver
from tests.support.java_bridge import available_port, build_test_runtime

pytestmark = [
    pytest.mark.integration,
    pytest.mark.java_bridge,
    pytest.mark.skipif(
        os.environ.get("HELIX_JAVA_BRIDGE_TESTS") != "1",
        reason="set HELIX_JAVA_BRIDGE_TESTS=1 for the Java bridge test",
    ),
]

FORM = "Sample:Form"
ERROR_FORM = "Error:Form"
ENTRY_ID = "000000000000001"
CREATED_ENTRY_ID = "000000000000999"
USERNAME = "java-bridge-user"
PASSWORD = "java-bridge-password-never-expose"
BRIDGE_TOKEN = "java-bridge-test-token-that-is-long-enough"


def test_real_client_and_managed_java_bridge_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_jar, library_dir = build_test_runtime(tmp_path)
    port = available_port()
    base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("HELIX_ARAPI_BRIDGE_PORT", str(port))
    monkeypatch.setenv("HELIX_ARAPI_TEST_DATA", "true")

    secret_ref = SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key="HELIX_JAVA_BRIDGE_TEST_CREDENTIAL",
    )
    config = ArapiBackendConfig(
        bridge_base_url=base_url,
        gateway_host="127.0.0.1",
        gateway_port=46_000,
        credentials=secret_ref,
        request_timeout_seconds=5,
    )
    settings = RuntimeSettings(
        config_path=tmp_path / "helix.yaml",
        arapi_bridge_jar_path=bridge_jar,
        arapi_lib_dir=library_dir,
    )
    secrets = SecretResolver(
        [
            EnvironmentSecretProvider(
                {
                    secret_ref.key: json.dumps(
                        {"username": USERNAME, "password": PASSWORD}
                    )
                }
            )
        ]
    )

    async def scenario() -> None:
        process = ArapiBridgeProcess(
            settings,
            (base_url,),
            bridge_token=BRIDGE_TOKEN,
        )
        client = ArapiBridgeClient(
            target=TargetKey(environment=Environment.DEV),
            config=config,
            secrets=secrets,
            bridge_token=BRIDGE_TOKEN,
        )
        try:
            await process.start()
            assert process.owned is True
            await client.probe_bridge()

            assert await client.list_forms() == (ERROR_FORM, FORM)
            sql = await client.query_sql(
                sql=("SELECT id AS id, name AS name FROM public.sample_table"),
                column_count=2,
                limit=10,
                timeout_seconds=5,
            )
            assert sql.rows == ((7, "sample-system"),)
            assert sql.truncated is False
            fields = await client.list_fields(FORM)
            assert [(field.name, field.datatype) for field in fields] == [
                ("Request ID", "CHAR"),
                ("Name", "CHAR"),
                ("Count", "INTEGER"),
                ("Enabled", "ENUM"),
                ("Description", "CHAR"),
                ("Modified Date", "TIME"),
            ]
            selection = await client.get_field_selection_values(
                form=FORM,
                field_id=4,
            )
            assert selection.name == "Enabled"
            assert selection.datatype == "ENUM"
            assert selection.selection_style == "custom"
            assert [
                (item.number, item.label) for item in selection.values
            ] == [
                (0, "Disabled"),
                (1, "Enabled"),
            ]
            menu = await client.get_field_menu_metadata(
                form=FORM,
                field_id=2,
            )
            assert menu.name == "Name"
            assert menu.datatype == "CHAR"
            assert menu.has_menu is True
            assert menu.menu_name == "Sample:Names"
            assert menu.menu_style == "append"
            menu_values = await client.resolve_field_menu_values(
                form=FORM,
                field_id=2,
                limit=10,
            )
            assert menu_values.menu_type == "query"
            assert [
                (item.label, item.value, item.path)
                for item in menu_values.values
            ] == [
                ("Sample system", "sample-system", ()),
                (
                    "Backup system",
                    "backup-system",
                    ("Additional systems",),
                ),
            ]
            assert menu_values.truncated is False

            page = await client.query_entries(
                form=FORM,
                fields=(
                    "Request ID",
                    "Name",
                    "Count",
                    "Enabled",
                    "Description",
                ),
                qualification=None,
                sort=(("Name", "asc"),),
                offset=0,
                limit=10,
                include_total=True,
            )
            assert page.total == 1
            assert page.entries[0].values == {
                "Request ID": ENTRY_ID,
                "Name": "sample-system",
                "Count": 7,
                "Enabled": 1,
                "Description": "initial description",
            }

            direct = await client.get_entry(
                form=FORM,
                entry_id=ENTRY_ID,
                fields=("Request ID", "Description"),
            )
            assert direct.entry.values == {
                "Request ID": ENTRY_ID,
                "Description": "initial description",
            }

            prepared = await client.prepare_update(
                form=FORM,
                entry_id=ENTRY_ID,
                fields=("Description",),
            )
            assert prepared.precondition == "1000"
            assert prepared.entry.values == {
                "Description": "initial description"
            }

            created_id = await client.create_entry(
                form=FORM,
                values={
                    "Name": "created-system",
                    "Count": 8,
                    "Enabled": False,
                    "Description": "created through Python",
                },
            )
            assert created_id == CREATED_ENTRY_ID
            created = await client.get_entry(
                form=FORM,
                entry_id=CREATED_ENTRY_ID,
                fields=("Request ID", "Name", "Count", "Enabled"),
            )
            assert created.entry.values == {
                "Request ID": CREATED_ENTRY_ID,
                "Name": "created-system",
                "Count": 8,
                "Enabled": 0,
            }
            verified = await client.verify_entry(
                form=FORM,
                entry_id=CREATED_ENTRY_ID,
                values={
                    "Name": "created-system",
                    "Count": 8,
                    "Enabled": False,
                },
            )
            assert set(verified.matched_fields) == {"Name", "Count", "Enabled"}
            assert (
                not verified.differing_fields
                and not verified.unverified_fields
            )
            different = await client.verify_entry(
                form=FORM,
                entry_id=CREATED_ENTRY_ID,
                values={"Name": "different requested name", "Enabled": False},
            )
            assert different.differing_fields == ("Name",)
            assert different.matched_fields == ("Enabled",)

            await client.update_entry(
                form=FORM,
                entry_id=ENTRY_ID,
                values={"Description": "updated through Python"},
                precondition=prepared.precondition,
                values_fingerprint=prepared.values_fingerprint,
            )
            updated = await client.get_entry(
                form=FORM,
                entry_id=ENTRY_ID,
                fields=("Description",),
            )
            assert updated.entry.values == {
                "Description": "updated through Python"
            }

            with pytest.raises(ArapiBridgeConflictError) as conflict:
                await client.update_entry(
                    form=FORM,
                    entry_id=ENTRY_ID,
                    values={"Description": "stale update"},
                    precondition=prepared.precondition,
                    values_fingerprint=prepared.values_fingerprint,
                )
            assert conflict.value.status_code == 409

            with pytest.raises(ArapiBridgeProtocolError) as invalid:
                await client.get_entry(
                    form=FORM,
                    entry_id=ENTRY_ID,
                    fields=("Missing Field",),
                )
            assert invalid.value.status_code == 400

            with pytest.raises(ArapiBridgeProtocolError) as backend_error:
                await client.list_fields(ERROR_FORM)
            assert backend_error.value.status_code == 502

            exposed = " ".join(
                (
                    repr(client),
                    str(conflict.value),
                    str(invalid.value),
                    str(backend_error.value),
                )
            )
            assert USERNAME not in exposed
            assert PASSWORD not in exposed
        finally:
            await client.aclose()
            await process.aclose()
        assert process.owned is False

    asyncio.run(scenario())


def test_client_recovers_after_its_owned_java_process_is_killed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_jar, library_dir = build_test_runtime(tmp_path)
    base_url = f"http://127.0.0.1:{available_port()}"
    monkeypatch.setenv("HELIX_ARAPI_TEST_DATA", "true")
    runtime = RuntimeSettings(
        config_path=tmp_path / "helix.yaml",
        arapi_bridge_jar_path=bridge_jar,
        arapi_lib_dir=library_dir,
    )
    credential = SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key="HELIX_ISOLATED_TEST_CREDENTIAL",
    )
    resolver = SecretResolver(
        [
            EnvironmentSecretProvider(
                {
                    credential.key: json.dumps(
                        {"username": USERNAME, "password": PASSWORD}
                    ),
                }
            )
        ]
    )
    config = ArapiBackendConfig(
        bridge_base_url=base_url,
        gateway_host="127.0.0.1",
        gateway_port=46_000,
        credentials=credential,
        request_timeout_seconds=5,
    )

    async def scenario() -> None:
        manager = ArapiBridgeProcess(
            runtime, (base_url,), bridge_token=BRIDGE_TOKEN
        )
        client = ArapiBridgeClient(
            target=TargetKey(environment=Environment.DEV),
            config=config,
            secrets=resolver,
            bridge_token=BRIDGE_TOKEN,
            ensure_bridge=manager.start,
        )
        try:
            await manager.start()
            assert await client.list_forms() == (ERROR_FORM, FORM)
            previous = manager._process
            assert previous is not None
            previous.kill()
            await previous.wait()
            # Concurrent real HTTP requests must share a single recovery.
            results = await asyncio.gather(
                *(client.list_forms() for _ in range(4))
            )
            assert results == [(ERROR_FORM, FORM)] * 4
            assert manager._process is not None
            assert manager._process.pid != previous.pid
            assert manager.owned is True
            await client.probe_bridge()
        finally:
            await client.aclose()
            await manager.aclose()

    asyncio.run(scenario())


def test_concurrent_java_startups_reuse_the_winning_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_jar, library_dir = build_test_runtime(tmp_path)
    base_url = f"http://127.0.0.1:{available_port()}"
    settings = RuntimeSettings(
        config_path=tmp_path / "helix.yaml",
        arapi_bridge_jar_path=bridge_jar,
        arapi_lib_dir=library_dir,
    )
    original_create = asyncio.create_subprocess_exec
    original_healthy = ArapiBridgeProcess._healthy

    async def scenario() -> None:
        managers = [
            ArapiBridgeProcess(
                settings, (base_url,), bridge_token=BRIDGE_TOKEN
            )
            for _ in range(2)
        ]
        initial_checks: set[ArapiBridgeProcess] = set()
        both_checked = asyncio.Event()
        children_started = asyncio.Event()
        bind_race_finished = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []

        async def healthy(manager: ArapiBridgeProcess) -> bool:
            if manager not in initial_checks:
                initial_checks.add(manager)
                if len(initial_checks) == 2:
                    both_checked.set()
                await both_checked.wait()
                return False
            return await original_healthy(manager)

        async def create(*args, **kwargs):
            child = await original_create(*args, **kwargs)
            children.append(child)
            if len(children) == 2:
                children_started.set()
            # Deterministically expose the losing child's nonzero returncode
            # before either manager enters its readiness loop.
            await bind_race_finished.wait()
            return child

        monkeypatch.setattr(ArapiBridgeProcess, "_healthy", healthy)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        starts = [asyncio.create_task(manager.start()) for manager in managers]
        exits: list[asyncio.Task[int]] = []
        try:
            async with asyncio.timeout(20):
                await children_started.wait()
                exits = [
                    asyncio.create_task(child.wait()) for child in children
                ]
                finished, _ = await asyncio.wait(
                    exits, return_when=asyncio.FIRST_COMPLETED
                )
                assert len(finished) == 1
                assert next(iter(finished)).result() != 0
                bind_race_finished.set()
                await asyncio.gather(*starts)

                owners = [manager for manager in managers if manager.owned]
                borrowers = [
                    manager for manager in managers if not manager.owned
                ]
                assert len(owners) == len(borrowers) == 1
                await borrowers[0].aclose()
                assert await original_healthy(owners[0]) is True
                await owners[0].aclose()
                assert all(child.returncode is not None for child in children)
        finally:
            bind_race_finished.set()
            for task in starts:
                task.cancel()
            await asyncio.gather(*starts, return_exceptions=True)
            for manager in managers:
                await manager.aclose()
            # Also clean up children if a test assertion failed before their
            # managers acquired ownership from create_subprocess_exec.
            for child in children:
                if child.returncode is None:
                    child.kill()
                await child.wait()
            for task in exits:
                task.cancel()
            await asyncio.gather(*exits, return_exceptions=True)

    asyncio.run(scenario())
