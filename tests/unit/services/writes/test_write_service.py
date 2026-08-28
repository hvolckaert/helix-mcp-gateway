"""Tests for two-phase, allowlisted and idempotent Helix writes."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from helix_mcp.clients.arapi import (
    ArapiBridgeConflictError,
    ArapiBridgeProtocolError,
    ArapiBridgeTransportError,
    ArapiEntry,
    ArapiPreparedUpdate,
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
    FormWriteService,
    UpdateValuesRequest,
    WriteOutcomeUnknownError,
    WritePlanMismatchError,
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
    ) -> None:
        outcome = self._next(
            "update_entry",
            {
                "form": form,
                "entry_id": entry_id,
                "values": values,
                "precondition": precondition,
            },
        )
        assert outcome is None


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
) -> tuple[FormWriteService, FakeProvider]:
    policy = TargetPolicyConfig(
        name="dev_write",
        allow_all_forms=True,
        allow_all_fields=True,
        writable_forms=(FORM,),
        creatable_fields_by_form={FORM: creatable_fields},
        updatable_fields_by_form={FORM: updatable_fields},
        access_mode="read_write",
        require_human_approval=True,
        require_write_reason=True,
        write_rate_limit_per_minute=5,
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
