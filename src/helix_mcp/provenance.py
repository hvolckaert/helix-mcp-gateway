"""Read identities and request-local observations of Gateway caches."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from helix_mcp.config import Environment, TargetPolicyConfig
from helix_mcp.config.models import FrozenModel

PolicyVersion = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
CacheKind = Literal["form_catalog", "field_catalog", "health"]


class CacheRead(FrozenModel):
    """A consulted cache, including its original acquisition timestamp."""

    kind: CacheKind
    read_at: AwareDatetime
    cached: bool


class ReadSource(FrozenModel):
    """Only identifiers already authorized and present in the result."""

    operation: str
    backend: Literal["arapi", "gateway", "health"]
    form: str | None = None
    entry_id: str | None = None
    field_id: int | None = None
    view_id: int | None = None
    schema_name: str | None = None
    object_name: str | None = None
    plan_id: str | None = None
    plan_digest: str | None = None


class ReadProvenance(FrozenModel):
    """Gateway observations, not source modification times or signatures."""

    schema_version: Literal[1] = 1
    environment: Environment | None
    source: ReadSource
    read_at: AwareDatetime
    data_read_at: AwareDatetime
    cached: bool
    cache_reads: tuple[CacheRead, ...]
    policy_versions: dict[Environment, PolicyVersion]


_cache_reads: ContextVar[list[CacheRead] | None] = ContextVar(
    "helix_provenance_cache_reads", default=None
)


def utc_now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def capture_cache_reads() -> Iterator[list[CacheRead]]:
    """Keep concurrent calls, failures and cancellations isolated."""

    observations: list[CacheRead] = []
    token = _cache_reads.set(observations)
    try:
        yield observations
    finally:
        _cache_reads.reset(token)


def record_cache_read(
    kind: CacheKind, read_at: datetime, *, cached: bool
) -> None:
    observations = _cache_reads.get()
    if observations is not None:
        observations.append(
            CacheRead(kind=kind, read_at=read_at, cached=cached)
        )


def policy_version(policy: TargetPolicyConfig) -> str:
    """Hash normalized policy rules only; never connection or secret data.

    Every policy sequence is an unordered allowlist or denylist. Normalize
    their order and dictionary order; retain spelling and all rule values.
    Renaming a policy without changing its rules does not change its version.
    """

    payload = {
        "schema": "helix-policy-v1",
        "rules": _canonical(policy.model_dump(mode="json", exclude={"name"})),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _canonical(value: object) -> object:
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted((_canonical(item) for item in value), key=str)
    return value
