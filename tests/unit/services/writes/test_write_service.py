"""Tests for two-phase, allowlisted and idempotent Helix writes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from helix_mcp.clients.arapi import (
    ArapiBridgeConflictError,
    ArapiBridgeProtocolError,
    ArapiBridgeTransportError,
    ArapiEntry,
    ArapiPreparedUpdate,
)
from helix_mcp.clients.arapi.models import (
    ArapiQueryPage,
    ArapiWriteVerification,
)
from helix_mcp.config import (
    ArapiBackendConfig,
    BackendKind,
    HelixConfig,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetKey,
    TargetPolicyConfig,
)
from helix_mcp.services.writes import (
    ApplyWriteRequest,
    FormWriteConflictError,
    FormWriteDisabledError,
    FormWriteFieldNotAllowedError,
    FormWriteFormNotAllowedError,
    FormWriteService,
    UpdateValuesRequest,
    WriteOperation,
    WriteOutcomeUnknownError,
    WritePlanMismatchError,
    WritePlanStateError,
    WritePlanStatus,
    WritePlanStore,
    WriteValuesRequest,
)
from helix_mcp.targeting import ResolvedTarget, TargetRegistry, TargetResolver

FORM = "Example:ComputerSystem"
FIELDS = ("Name", "DatasetId", "ShortDescription")
ENTRY_ID = "000000000000123"
PRECONDITION = "1785502800"


def run(coroutine):
    return asyncio.run(coroutine)


class FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _next(self, operation: str, arguments: dict[str, Any]) -> object:
        self.calls.append((operation, arguments))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def prepare_update(
        self,
        *,
        form: str,
        entry_id: str,
        fields: tuple[str, ...],
    ) -> ArapiPreparedUpdate:
        outcome = self._next(
            "prepare_update",
            {
                "form": form,
                "entry_id": entry_id,
                "fields": fields,
            },
        )
        assert isinstance(outcome, ArapiPreparedUpdate)
        return outcome

    async def create_entry(
        self,
        *,
        form: str,
        values: dict[str, object],
    ) -> str | None:
        outcome = self._next(
            "create_entry",
            {"form": form, "values": values},
        )
        assert outcome is None or isinstance(outcome, str)
        return outcome

    async def update_entry(
        self,
        *,
        form: str,
        entry_id: str,
        values: dict[str, object],
        precondition: str,
        values_fingerprint: str,
    ) -> None:
        outcome = self._next(
            "update_entry",
            {
                "form": form,
                "entry_id": entry_id,
                "values": values,
                "precondition": precondition,
                "values_fingerprint": values_fingerprint,
            },
        )
        assert outcome is None

    async def verify_entry(self, *, form, entry_id, values):
        return ArapiWriteVerification(datetime.now(UTC), tuple(values), (), ())


class FakeCmdbClient(FakeClient):
    async def reserve_cmdb_instance_id(self, form):
        return self._next("reserve", {"form": form})

    async def create_cmdb_instance(self, *, form, values):
        return self._next("cmdb_create", {"form": form, "values": values})

    async def query_entries(self, **kwargs):
        return self._next("resolve", kwargs)


@pytest.mark.parametrize("duplicate", [False, True])
def test_cmdb_plan_identity_and_replay(duplicate):
    form = "BMC.CORE:BMC_ComputerSystem"
    row = {
        "ClassId": "CS",
        "DatasetId": "BMC.SAMPLE",
        "InstanceId": "SDK1",
        "RequestId": "0001|0002",
    }
    rows = tuple(ArapiEntry(values=row) for _ in range(2 if duplicate else 1))
    query = ArapiQueryPage(rows, 0, 2, len(rows))
    client = FakeCmdbClient(["SDK1", "CS", query])
    svc, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
    )
    req = WriteValuesRequest(
        values={"DatasetId": "BMC.SAMPLE", "Name": "same"},
        reason="synthetic test",
    )
    plan = run(
        svc.plan_create_for_form(environment="dev", form=form, request=req)
    )
    assert plan.operation is WriteOperation.CREATE
    assert plan.create_backend == "cmdb_sdk"
    assert plan.proposed_values["InstanceId"] == "SDK1"
    assert [name for name, _ in client.calls] == ["reserve"]
    apply = ApplyWriteRequest(
        plan_id=plan.plan_id, plan_digest=plan.plan_digest
    )
    first = run(svc.apply_create(environment="dev", request=apply))
    again = run(svc.apply_create(environment="dev", request=apply))
    assert first.instance_id == again.instance_id == "SDK1"
    assert first.entry_id == (None if duplicate else "0001|0002")
    assert first.verification.status == (
        "unavailable" if duplicate else "matched"
    )
    assert again.reused_result and again.verification == first.verification
    assert [name for name, _ in client.calls] == [
        "reserve",
        "cmdb_create",
        "resolve",
    ]


def test_cmdb_same_name_plans_have_separate_approved_identities():
    client = FakeCmdbClient(["SDK1", "SDK2"])
    svc, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
    )
    form = "BMC.CORE:BMC_ComputerSystem"
    req = WriteValuesRequest(
        values={"DatasetId": "BMC.SAMPLE", "Name": "same"},
        reason="synthetic concurrent plans",
    )

    async def scenario():
        first, second = await asyncio.gather(
            *(
                svc.plan_create_for_form(
                    environment="dev", form=form, request=req
                )
                for _ in range(2)
            )
        )
        assert (
            first.proposed_values["InstanceId"]
            != second.proposed_values["InstanceId"]
        )
        assert first.plan_digest != second.plan_digest
        for plan in (second, first):
            key = plan.proposed_values["InstanceId"]
            row = {
                "InstanceId": key,
                "ClassId": "CS",
                "DatasetId": "BMC.SAMPLE",
                "RequestId": f"a|{key}",
            }
            client.outcomes.extend(
                ["CS", ArapiQueryPage((ArapiEntry(values=row),), 0, 2, 1)]
            )
            result = await svc.apply_create(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=plan.plan_id, plan_digest=plan.plan_digest
                ),
            )
            assert (result.entry_id, result.instance_id) == (
                row["RequestId"],
                key,
            )
            history = await svc.get_plan(
                environment="dev", plan_id=plan.plan_id
            )
            assert history.instance_id == key

    run(scenario())


def test_cmdb_plan_rejects_a_caller_supplied_identity():
    client = FakeCmdbClient([])
    svc, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
    )
    req = WriteValuesRequest(
        values={"DatasetId": "BMC.SAMPLE", "InstanceId": "caller-id"},
        reason="synthetic input check",
    )
    with pytest.raises(FormWriteFieldNotAllowedError):
        run(
            svc.plan_create_for_form(
                environment="dev",
                form="BMC.CORE:BMC_ComputerSystem",
                request=req,
            )
        )
    assert not client.calls


def test_cmdb_missing_commit_response_is_never_retried():
    client = FakeCmdbClient(
        [
            "SDK1",
            ArapiBridgeTransportError(TargetKey(environment="dev"), "timeout"),
        ]
    )
    svc, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
    )
    req = WriteValuesRequest(
        values={"DatasetId": "BMC.SAMPLE", "Name": "same"},
        reason="SDK timeout test",
    )
    plan = run(
        svc.plan_create_for_form(
            environment="dev", form="BMC.CORE:BMC_ComputerSystem", request=req
        )
    )
    apply = ApplyWriteRequest(
        plan_id=plan.plan_id, plan_digest=plan.plan_digest
    )
    for _ in range(2):
        with pytest.raises(WriteOutcomeUnknownError):
            run(svc.apply_create(environment="dev", request=apply))
    assert [name for name, _ in client.calls] == ["reserve", "cmdb_create"]


class FakeProvider:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.targets: list[ResolvedTarget] = []

    def get(self, target: ResolvedTarget) -> FakeClient:
        self.targets.append(target)
        return self.client


def build_service(
    client: FakeClient,
    *,
    creatable_fields: tuple[str, ...] = FIELDS,
    updatable_fields: tuple[str, ...] = FIELDS,
    allow_all_forms: bool = True,
    allow_all_writable_forms: bool = False,
    allow_all_creatable_fields: bool = False,
    allow_all_updatable_fields: bool = False,
    allow_all_creatable_fields_for_forms: tuple[str, ...] = (),
    allow_all_updatable_fields_for_forms: tuple[str, ...] = (),
    sensitive_fields: tuple[str, ...] = (),
    sensitive_field_markers: tuple[str, ...] = (),
    allow_form_reads: bool = True,
    allowed_read_fields: tuple[str, ...] | None = None,
) -> tuple[FormWriteService, FakeProvider]:
    policy = TargetPolicyConfig(
        name="dev_write",
        allow_all_forms=allow_all_forms,
        allow_form_reads=allow_form_reads,
        allow_all_fields=allowed_read_fields is None,
        allowed_fields_by_form={FORM: allowed_read_fields}
        if allowed_read_fields
        else {},
        allowed_forms=() if allow_all_forms else (FORM,),
        allow_all_writable_forms=allow_all_writable_forms,
        writable_forms=() if allow_all_writable_forms else (FORM,),
        allow_all_creatable_fields=allow_all_creatable_fields,
        allow_all_creatable_fields_for_forms=(
            allow_all_creatable_fields_for_forms
        ),
        creatable_fields_by_form=(
            {}
            if allow_all_creatable_fields
            or FORM in allow_all_creatable_fields_for_forms
            else {FORM: creatable_fields}
        ),
        allow_all_updatable_fields=allow_all_updatable_fields,
        allow_all_updatable_fields_for_forms=(
            allow_all_updatable_fields_for_forms
        ),
        updatable_fields_by_form=(
            {}
            if allow_all_updatable_fields
            or FORM in allow_all_updatable_fields_for_forms
            else {FORM: updatable_fields}
        ),
        access_mode="read_write",
        require_human_approval=True,
        require_write_reason=True,
        write_rate_limit_per_minute=5,
        sensitive_fields=sensitive_fields,
        sensitive_field_markers=sensitive_field_markers,
    )
    target = TargetConfig(
        environment="dev",
        display_name="Global DEV",
        policy_ref=policy.name,
        arapi=ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_host="127.0.0.1",
            gateway_port=46000,
            credentials=SecretRef(
                provider=SecretProviderKind.ENVIRONMENT,
                key="HELIX_CREDENTIAL_DEV",
            ),
        ),
    )
    resolver = TargetResolver(
        TargetRegistry(HelixConfig(policies=(policy,), targets=(target,)))
    )
    provider = FakeProvider(client)
    return (
        FormWriteService(
            resolver,
            provider,
            WritePlanStore(ttl_seconds=600, max_pending=10),
        ),
        provider,
    )


def prepared(values: dict[str, object]) -> ArapiPreparedUpdate:
    return ArapiPreparedUpdate(
        entry_id=ENTRY_ID,
        entry=ArapiEntry(values=values),
        precondition=PRECONDITION,
        values_fingerprint="a" * 64,
    )


def test_broad_write_scope_accepts_dynamic_forms_and_fields() -> None:
    client = FakeClient([])
    service, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
    )

    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form="Example:FutureForm",
            request=WriteValuesRequest(
                values={"Future Field": "value"},
                reason="Approved dynamic write scope test",
            ),
        )
    )

    assert plan.form == "Example:FutureForm"
    assert plan.proposed_values == {"Future Field": "value"}
    assert client.calls == []


def test_broad_update_scope_accepts_dynamic_forms_and_fields() -> None:
    client = FakeClient([prepared({"Future Field": "old"})])
    service, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
    )

    plan = run(
        service.plan_update(
            environment="dev",
            form="Example:FutureForm",
            request=UpdateValuesRequest(
                entry_id=ENTRY_ID,
                values={"Future Field": "new"},
                reason="Approved dynamic update scope test",
            ),
        )
    )

    assert plan.form == "Example:FutureForm"
    assert plan.proposed_values == {"Future Field": "new"}
    assert client.calls[0][0] == "prepare_update"


def test_per_form_broad_create_scope_accepts_only_non_sensitive_fields() -> (
    None
):
    client = FakeClient([])
    service, _ = build_service(
        client,
        allow_all_creatable_fields_for_forms=(FORM,),
        sensitive_field_markers=("token",),
    )

    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={"Future Field": "value"},
                reason="Approved per-form broad scope test",
            ),
        )
    )

    assert plan.proposed_values == {"Future Field": "value"}
    with pytest.raises(FormWriteFieldNotAllowedError, match="sensitive"):
        run(
            service.plan_create_for_form(
                environment="dev",
                form=FORM,
                request=WriteValuesRequest(
                    values={"API Token": "do-not-write"},
                    reason="Approved sensitive-field rejection test",
                ),
            )
        )


def test_per_form_broad_scopes_remain_operation_specific() -> None:
    client = FakeClient([])
    service, _ = build_service(
        client,
        updatable_fields=("Status",),
        allow_all_creatable_fields_for_forms=(FORM,),
    )

    with pytest.raises(FormWriteFieldNotAllowedError):
        run(
            service.plan_update(
                environment="dev",
                form=FORM,
                request=UpdateValuesRequest(
                    entry_id=ENTRY_ID,
                    values={"Future Field": "value"},
                    reason="Approved operation-specific scope test",
                ),
            )
        )


def test_broad_write_scope_still_enforces_general_form_boundary() -> None:
    client = FakeClient([])
    service, _ = build_service(
        client,
        allow_all_forms=False,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
    )

    with pytest.raises(FormWriteFormNotAllowedError):
        run(
            service.plan_create_for_form(
                environment="dev",
                form="Example:OutsideScope",
                request=WriteValuesRequest(
                    values={"Description": "value"},
                    reason="Approved boundary enforcement test",
                ),
            )
        )


@pytest.mark.parametrize("field", ["Password", "API Token Value"])
def test_broad_write_fields_still_reject_sensitive_names(field: str) -> None:
    client = FakeClient([])
    service, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
        sensitive_fields=("Password",),
        sensitive_field_markers=("token",),
    )

    with pytest.raises(FormWriteFieldNotAllowedError, match="sensitive"):
        run(
            service.plan_create_for_form(
                environment="dev",
                form="Example:FutureForm",
                request=WriteValuesRequest(
                    values={field: "do-not-write"},
                    reason="Approved sensitive-field rejection test",
                ),
            )
        )


def test_create_plan_is_reviewable_and_apply_is_idempotent() -> None:
    client = FakeClient([ENTRY_ID])
    service, provider = build_service(client)
    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={
                    "name": "Company",
                    "DatasetId": "TEST.SAMPLE",
                    "ShortDescription": "sample computer system",
                },
                reason="create computer system for unit test",
            ),
        )
    )

    assert plan.status is WritePlanStatus.PENDING
    assert plan.proposed_values == {
        "Name": "Company",
        "DatasetId": "TEST.SAMPLE",
        "ShortDescription": "sample computer system",
    }
    assert "TEST.SAMPLE" not in repr(plan)
    assert client.calls == []

    request = ApplyWriteRequest(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
    )
    first = run(
        service.apply_create(
            environment="dev",
            request=request,
        )
    )
    second = run(
        service.apply_create(
            environment="dev",
            request=request,
        )
    )

    assert first.entry_id == ENTRY_ID
    assert first.reused_result is False
    assert second.entry_id == first.entry_id
    assert second.reused_result is True
    assert client.calls == [
        (
            "create_entry",
            {"form": FORM, "values": plan.proposed_values},
        )
    ]
    assert provider.targets[0].backend is BackendKind.ARAPI


def test_create_without_server_entry_id_is_applied_and_idempotent() -> None:
    client = FakeClient([None])
    service, _ = build_service(client)
    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={"ShortDescription": "sample computer system"},
                reason="create compound form entry for unit test",
            ),
        )
    )
    request = ApplyWriteRequest(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
    )

    first = run(service.apply_create(environment="dev", request=request))
    second = run(service.apply_create(environment="dev", request=request))

    assert first.status is WritePlanStatus.APPLIED
    assert first.entry_id is None
    assert second.entry_id is None
    assert second.reused_result is True
    assert len(client.calls) == 1


def test_concurrent_same_name_creates_keep_their_native_ids_and_history():
    async def scenario():
        first_read = asyncio.Event()
        release_first = asyncio.Event()
        ids = ("0001|0002", "0003|0004")
        reads = []

        class InterleavedClient(FakeClient):
            async def verify_entry(self, *, form, entry_id, values):
                reads.append((form, entry_id, dict(values)))
                if entry_id == ids[0]:
                    first_read.set()
                    await release_first.wait()
                return await super().verify_entry(
                    form=form, entry_id=entry_id, values=values
                )

        client = InterleavedClient(list(ids))
        service, _ = build_service(client)
        request = WriteValuesRequest(
            values={"Name": "same"}, reason="Verify plan identity"
        )
        plans = [
            await service.plan_create_for_form(
                environment="dev", form=FORM, request=request
            )
            for _ in ids
        ]
        assert plans[0].plan_id != plans[1].plan_id
        assert plans[0].plan_digest == plans[1].plan_digest
        requests = [
            ApplyWriteRequest(plan_id=p.plan_id, plan_digest=p.plan_digest)
            for p in plans
        ]
        first = asyncio.create_task(
            service.apply_create(environment="dev", request=requests[0])
        )
        try:
            await asyncio.wait_for(first_read.wait(), timeout=3)
            second = await service.apply_create(
                environment="dev", request=requests[1]
            )
        finally:
            release_first.set()
        results = (await first, second)
        for req, result, expected in zip(requests, results, ids, strict=True):
            assert result.plan_id == req.plan_id
            assert result.entry_id == expected
            assert result.verification.provenance.source.entry_id == expected
            history = await service.get_plan(
                environment="dev", plan_id=req.plan_id
            )
            assert (history.entry_id, history.plan_digest) == (
                expected,
                req.plan_digest,
            )
            replay = await service.apply_create(environment="dev", request=req)
            assert replay == result.model_copy(update={"reused_result": True})
        assert reads == [
            (FORM, entry_id, {"Name": "same"}) for entry_id in ids
        ]
        assert [operation for operation, _ in client.calls] == [
            "create_entry"
        ] * 2

    run(scenario())


@pytest.mark.parametrize(
    "failure", ["denied", "timeout", "form_policy", "field_policy"]
)
def test_create_read_failure_keeps_the_id_and_does_not_repeat(failure):
    reads = []
    entry_id = "0007|0008"

    class ReadFailureClient(FakeClient):
        async def verify_entry(self, *, form, entry_id, values):
            reads.append((form, entry_id, tuple(values)))
            if failure == "timeout":
                raise TimeoutError
            raise PermissionError

    client = ReadFailureClient([entry_id])
    service, _ = build_service(
        client,
        allow_form_reads=failure != "form_policy",
        allowed_read_fields=("DatasetId",)
        if failure == "field_policy"
        else None,
    )
    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={"Name": "same"}, reason="Verify plan identity"
            ),
        )
    )
    req = ApplyWriteRequest(plan_id=plan.plan_id, plan_digest=plan.plan_digest)
    result = run(service.apply_create(environment="dev", request=req))
    assert result.status == "applied"
    assert result.entry_id == entry_id
    assert result.verification.status == "unavailable"
    assert result.verification.unverified_fields == ("Name",)
    assert result.verification.reason == "read_failed"
    assert run(
        service.apply_create(environment="dev", request=req)
    ) == result.model_copy(update={"reused_result": True})
    assert len(client.calls) == 1
    assert reads == (
        [(FORM, entry_id, ("Name",))]
        if failure in {"denied", "timeout"}
        else []
    )


def test_update_plan_reads_values_and_applies_server_precondition() -> None:
    client = FakeClient(
        [
            prepared(
                {
                    "DatasetId": "TEST.ASSET",
                    "Unexpected": "hidden",
                }
            ),
            None,
        ]
    )
    service, provider = build_service(client)
    plan = run(
        service.plan_update(
            environment="dev",
            form=FORM,
            request=UpdateValuesRequest(
                entry_id=ENTRY_ID,
                values={"DatasetId": "TEST.SAMPLE"},
                reason="update computer system for unit test",
            ),
        )
    )

    assert plan.current_values == {"DatasetId": "TEST.ASSET"}
    assert plan.proposed_values == {"DatasetId": "TEST.SAMPLE"}
    result = run(
        service.apply_update(
            environment="dev",
            request=ApplyWriteRequest(
                plan_id=plan.plan_id,
                plan_digest=plan.plan_digest,
            ),
        )
    )

    assert result.entry_id == ENTRY_ID
    assert client.calls == [
        (
            "prepare_update",
            {
                "form": FORM,
                "entry_id": ENTRY_ID,
                "fields": ("DatasetId",),
            },
        ),
        (
            "update_entry",
            {
                "form": FORM,
                "entry_id": ENTRY_ID,
                "values": {"DatasetId": "TEST.SAMPLE"},
                "precondition": PRECONDITION,
                "values_fingerprint": "a" * 64,
            },
        ),
    ]
    assert all(
        target.backend is BackendKind.ARAPI for target in provider.targets
    )


def test_unlisted_field_and_digest_mismatch_never_write() -> None:
    client = FakeClient([])
    service, _ = build_service(client)
    with pytest.raises(FormWriteFieldNotAllowedError):
        run(
            service.plan_create_for_form(
                environment="dev",
                form=FORM,
                request=WriteValuesRequest(
                    values={"DatasetId_2": "blocked"},
                    reason="attempt disallowed computer system field",
                ),
            )
        )

    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={"DatasetId": "TEST.SAMPLE"},
                reason="valid plan with wrong digest",
            ),
        )
    )
    with pytest.raises(WritePlanMismatchError):
        run(
            service.apply_create(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=plan.plan_id,
                    plan_digest="0" * 64,
                ),
            )
        )
    assert client.calls == []


def test_create_and_update_use_independent_field_allowlists() -> None:
    client = FakeClient([])
    service, _ = build_service(
        client,
        creatable_fields=("Name",),
        updatable_fields=("ShortDescription",),
    )

    with pytest.raises(FormWriteFieldNotAllowedError):
        run(
            service.plan_create_for_form(
                environment="dev",
                form=FORM,
                request=WriteValuesRequest(
                    values={"ShortDescription": "not creatable"},
                    reason="verify create field allowlist",
                ),
            )
        )
    with pytest.raises(FormWriteFieldNotAllowedError):
        run(
            service.plan_update(
                environment="dev",
                form=FORM,
                request=UpdateValuesRequest(
                    entry_id=ENTRY_ID,
                    values={"Name": "not updatable"},
                    reason="verify update field allowlist",
                ),
            )
        )

    assert client.calls == []


def test_read_only_policy_rejects_plans_before_selecting_a_client() -> None:
    policy = TargetPolicyConfig(
        name="read_only",
        access_mode="read_only",
    )
    target = TargetConfig(
        environment="dev",
        display_name="Read-only DEV",
        policy_ref=policy.name,
        arapi=ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_port=46000,
            credentials=SecretRef(
                provider=SecretProviderKind.ENVIRONMENT,
                key="HELIX_CREDENTIAL_DEV",
            ),
        ),
    )
    client = FakeClient([])
    provider = FakeProvider(client)
    service = FormWriteService(
        TargetResolver(
            TargetRegistry(HelixConfig(policies=(policy,), targets=(target,)))
        ),
        provider,
        WritePlanStore(ttl_seconds=600, max_pending=10),
    )

    with pytest.raises(FormWriteDisabledError):
        run(
            service.plan_create_for_form(
                environment="dev",
                form=FORM,
                request=WriteValuesRequest(
                    values={"Name": "blocked"},
                    reason="verify read-only create rejection",
                ),
            )
        )
    with pytest.raises(FormWriteDisabledError):
        run(
            service.plan_update(
                environment="dev",
                form=FORM,
                request=UpdateValuesRequest(
                    entry_id=ENTRY_ID,
                    values={"ShortDescription": "blocked"},
                    reason="verify read-only update rejection",
                ),
            )
        )

    assert provider.targets == []
    assert client.calls == []


def test_transport_failure_becomes_unknown_and_cannot_be_retried() -> None:
    key = TargetKey(instance="helix", environment="dev")
    client = FakeClient(
        [ArapiBridgeTransportError(key, "private transport detail")]
    )
    service, _ = build_service(client)
    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={"ShortDescription": "target"},
                reason="test uncertain write outcome",
            ),
        )
    )
    request = ApplyWriteRequest(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
    )

    with pytest.raises(WriteOutcomeUnknownError):
        run(
            service.apply_create(
                environment="dev",
                request=request,
            )
        )
    with pytest.raises(WriteOutcomeUnknownError):
        run(
            service.apply_create(
                environment="dev",
                request=request,
            )
        )
    assert len(client.calls) == 1


@pytest.mark.parametrize("status_code", [200, 400, 500])
def test_protocol_failure_during_apply_becomes_unknown(
    status_code: int,
) -> None:
    key = TargetKey(instance="helix", environment="dev")
    client = FakeClient(
        [
            ArapiBridgeProtocolError(
                key,
                "private response detail",
                status_code=status_code,
            )
        ]
    )
    service, _ = build_service(client)
    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={"ShortDescription": "target"},
                reason="test uncertain response payload",
            ),
        )
    )

    with pytest.raises(WriteOutcomeUnknownError):
        run(
            service.apply_create(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=plan.plan_id,
                    plan_digest=plan.plan_digest,
                ),
            )
        )
    assert len(client.calls) == 1


def test_update_precondition_failure_requires_a_new_plan() -> None:
    key = TargetKey(instance="helix", environment="dev")
    client = FakeClient(
        [
            prepared({"DatasetId": "TEST.ASSET"}),
            ArapiBridgeConflictError(
                key,
                "private conflict detail",
                status_code=409,
            ),
        ]
    )
    service, _ = build_service(client)
    plan = run(
        service.plan_update(
            environment="dev",
            form=FORM,
            request=UpdateValuesRequest(
                entry_id=ENTRY_ID,
                values={"DatasetId": "TEST.SAMPLE"},
                reason="test optimistic concurrency conflict",
            ),
        )
    )

    with pytest.raises(FormWriteConflictError):
        run(
            service.apply_update(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=plan.plan_id,
                    plan_digest=plan.plan_digest,
                ),
            )
        )
    assert len(client.calls) == 2


def test_legacy_update_plan_requires_new_review_without_writing():
    client = FakeClient([])
    service, _ = build_service(client)
    plan = run(
        service._plans.create(
            operation=WriteOperation.UPDATE,
            target=TargetKey(environment="dev"),
            form=FORM,
            entry_id=ENTRY_ID,
            current_values={"Name": "old"},
            proposed_values={"Name": "new"},
            reason="legacy plan without value preconditions",
            precondition=PRECONDITION,
        )
    )
    with pytest.raises(WritePlanStateError, match="new plan"):
        run(
            service.apply_update(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=plan.plan_id, plan_digest=plan.plan_digest
                ),
            )
        )
    assert client.calls == []


def test_create_without_entry_id_is_applied_but_not_verified():
    reads = []

    class NoIdentityClient(FakeClient):
        async def verify_entry(self, **kwargs):
            reads.append(kwargs)
            raise AssertionError("verification requires the native create ID")

    client = NoIdentityClient([None])
    service, _ = build_service(client)
    plan = run(
        service.plan_create_for_form(
            environment="dev",
            form=FORM,
            request=WriteValuesRequest(
                values={"Name": "new", "DatasetId": "SAMPLE"},
                reason="create without addressable result",
            ),
        )
    )
    request = ApplyWriteRequest(
        plan_id=plan.plan_id, plan_digest=plan.plan_digest
    )
    result = run(service.apply_create(environment="dev", request=request))
    assert result.status == "applied"
    assert result.entry_id is None
    assert result.verification.status == "unavailable"
    assert result.verification.reason == "entry_id_unavailable"
    assert set(result.verification.unverified_fields) == {"Name", "DatasetId"}
    assert run(
        service.apply_create(environment="dev", request=request)
    ) == result.model_copy(update={"reused_result": True})
    assert reads == []
    assert len(client.calls) == 1
