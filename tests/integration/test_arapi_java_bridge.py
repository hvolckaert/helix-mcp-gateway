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
        process = ArapiBridgeProcess(settings, (base_url,))
        client = ArapiBridgeClient(
            target=TargetKey(environment=Environment.DEV),
            config=config,
            secrets=secrets,
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

            await client.update_entry(
                form=FORM,
                entry_id=ENTRY_ID,
                values={"Description": "updated through Python"},
                precondition=prepared.precondition,
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
