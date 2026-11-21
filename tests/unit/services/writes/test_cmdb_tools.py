"""Exact references, directed endpoint controls and the shared approval store."""

import pytest

from helix_mcp.config import Environment
from helix_mcp.services.forms.errors import FormFieldNotAllowedError
from helix_mcp.services.writes import (
    ApplyWriteRequest,
    FormWriteFieldNotAllowedError,
    WritePlanMismatchError,
    WritePlanStateError,
    WriteValuesRequest,
)
from helix_mcp.services.writes.cmdb import InstanceReference
from tests.unit.services.writes.test_write_service import (
    FakeCmdbClient,
    build_service,
    run,
)


class EndpointClient(FakeCmdbClient):
    def __init__(self):
        super().__init__(["RESERVED"])
        self.rows = {
            i: {
                "InstanceId": i,
                "DatasetId": "SAMPLE",
                "ClassId": "NATIVE-CI-ID",
            }
            for i in ("LEFT", "RIGHT")
        }

    async def get_cmdb_instance(
        self, *, form, dataset_id, instance_id, fields
    ):
        self.calls.append(("get", {"instance": instance_id, "fields": fields}))
        if instance_id not in self.rows:
            raise WritePlanStateError("CMDB endpoint unavailable")
        return {f: self.rows[instance_id][f] for f in fields}


def ref(i):
    return InstanceReference(
        namespace="BMC.CORE",
        class_name="BMC_ComputerSystem",
        dataset_id="SAMPLE",
        instance_id=i,
    )


def setup(**policy):
    client = EndpointClient()
    svc, _ = build_service(
        client,
        allow_all_writable_forms=True,
        allow_all_creatable_fields=True,
        allow_all_updatable_fields=True,
        **policy,
    )
    return client, svc


def plan(svc, **overrides):
    req = WriteValuesRequest(
        values={"Name": "fictional", **overrides},
        reason="offline verification",
    )
    return run(
        svc.plan_cmdb_create(
            Environment.DEV,
            "SAMPLE",
            req,
            endpoints=(ref("LEFT"), ref("RIGHT")),
        )
    )


def test_direction_and_approval_digest_preserved():
    client, svc = setup()
    first = plan(svc)
    assert first.proposed_values["Source.InstanceId"] == "LEFT"
    assert first.proposed_values["Destination.InstanceId"] == "RIGHT"
    assert first.proposed_values["Source.ClassId"] == "NATIVE-CI-ID"
    assert first.proposed_values["InstanceId"] == "RESERVED"
    client.outcomes.append("OTHER-RESERVED")
    second = run(
        svc.plan_cmdb_create(
            Environment.DEV,
            "SAMPLE",
            WriteValuesRequest(
                values={"Name": "fictional"}, reason="offline verification"
            ),
            endpoints=(ref("RIGHT"), ref("LEFT")),
        )
    )
    with pytest.raises(WritePlanMismatchError):
        run(
            svc.apply_create(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=first.plan_id, plan_digest=second.plan_digest
                ),
            )
        )
    client.rows["RIGHT"]["ClassId"] = "CHANGED-AFTER-APPROVAL"
    with pytest.raises(WritePlanStateError):
        run(
            svc.apply_create(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=first.plan_id, plan_digest=first.plan_digest
                ),
            )
        )
    assert (
        run(svc.get_plan(environment="dev", plan_id=first.plan_id)).status
        == "failed"
    )
    assert all(op != "cmdb_create" for op, _ in client.calls)


@pytest.mark.parametrize("denial", ["hidden", "missing"])
def test_unreadable_or_missing_endpoint_never_reserves(denial):
    client, svc = setup(
        sensitive_fields=("ClassId",) if denial == "hidden" else ()
    )
    del client.rows["RIGHT"]
    with pytest.raises((FormFieldNotAllowedError, WritePlanStateError)):
        plan(svc)
    assert all(op == "get" for op, _ in client.calls)


@pytest.mark.parametrize(
    "field",
    ["instanceID", "Request Id", "Destination.InstanceId", "SourceDatasetId"],
)
def test_caller_cannot_replace_managed_attributes(field):
    client, svc = setup()
    with pytest.raises(FormWriteFieldNotAllowedError):
        plan(svc, **{field: "OTHER"})
    assert not client.calls


def test_lookup_returns_only_requested_attributes_and_checks_visible_role():
    client, svc = setup()
    rel = ref("REL").model_copy(update={"class_name": "BMC_Dependency"})
    client.rows["REL"] = {
        "Source." + k: v for k, v in client.rows["LEFT"].items()
    }
    result = run(
        svc.get_cmdb_instance(Environment.DEV, rel, ["Source.InstanceId"])
    )
    assert result.attributes == {"Source.InstanceId": "LEFT"}
    assert [args["instance"] for _, args in client.calls] == ["REL", "LEFT"]
    del client.rows["LEFT"]
    with pytest.raises(WritePlanStateError):
        run(svc.get_cmdb_instance(Environment.DEV, rel, ["Source.InstanceId"]))


def test_hidden_ar_request_id_preserves_native_identity_without_ar_query():
    client, svc = setup(sensitive_fields=("RequestId",))
    pending = run(
        svc.plan_cmdb_create(
            Environment.DEV,
            "SAMPLE",
            WriteValuesRequest(
                values={"Name": "fictional"}, reason="native identity only"
            ),
        )
    )
    client.outcomes.append("NATIVE-CI-ID")
    result = run(
        svc.apply_create(
            environment="dev",
            request=ApplyWriteRequest(
                plan_id=pending.plan_id, plan_digest=pending.plan_digest
            ),
        )
    )
    assert result.instance_id == "RESERVED" and result.entry_id is None
    assert set(result.verification.unverified_fields) == set(
        pending.proposed_values
    )
    assert [op for op, _ in client.calls] == ["reserve", "cmdb_create"]
