"""Encrypted SQLite persistence for restart-safe write plans."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from helix_mcp.config import Environment, TargetKey
from helix_mcp.services.writes.errors import (
    FormWriteError,
    WriteOutcomeUnknownError,
    WritePlanCapacityError,
    WritePlanExpiredError,
    WritePlanMismatchError,
    WritePlanNotFoundError,
    WritePlanPersistenceError,
    WritePlanStateError,
)
from helix_mcp.services.writes.models import (
    ApplyWriteResult,
    JsonScalar,
    WriteOperation,
    WritePlanResult,
    WritePlanStatus,
)
from helix_mcp.services.writes.store import (
    AcquiredWritePlan,
    StoredWritePlan,
    _clear_payload,
    _digest,
    _public,
)

_SCHEMA_VERSION = 1
_KEY_BYTES = 32
_NONCE_BYTES = 12
_MAX_PAYLOAD_BYTES = 131_072
_SQLITE_TIMEOUT_SECONDS = 5
ResultT = TypeVar("ResultT")


class PersistentWritePlanStore:
    """Persist encrypted plans while preserving one-time apply semantics."""

    __slots__ = (
        "_cipher",
        "_clock",
        "_database_path",
        "_lock",
        "_max_pending",
        "_ttl",
    )

    def __init__(
        self,
        *,
        database_path: Path,
        key_path: Path,
        ttl_seconds: int,
        max_pending: int,
        clock: Callable[[], float] = time.time,
        recover_interrupted: bool = True,
    ) -> None:
        self._database_path = database_path.absolute()
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cipher = AESGCM(_read_key(key_path.absolute()))
        _prepare_database_path(self._database_path)
        self._execute(
            lambda connection: self._initialize(
                connection,
                recover_interrupted=recover_interrupted,
            )
        )

    async def create(
        self,
        *,
        operation: WriteOperation,
        target: TargetKey,
        form: str,
        entry_id: str | None,
        current_values: dict[str, JsonScalar] | None,
        proposed_values: dict[str, JsonScalar],
        reason: str,
        precondition: str | None,
    ) -> WritePlanResult:
        async with self._lock:

            def operation_in_transaction(
                connection: sqlite3.Connection,
            ) -> WritePlanResult:
                self._purge_expired(connection)
                pending = connection.execute(
                    "SELECT COUNT(*) FROM write_plans WHERE status IN (?, ?)",
                    (
                        WritePlanStatus.PENDING.value,
                        WritePlanStatus.APPLYING.value,
                    ),
                ).fetchone()[0]
                if int(pending) >= self._max_pending:
                    raise WritePlanCapacityError(
                        "pending write-plan capacity was reached"
                    )
                plan_id = secrets.token_hex(16)
                digest = _digest(
                    operation=operation,
                    target=target,
                    form=form,
                    entry_id=entry_id,
                    current_values=current_values,
                    proposed_values=proposed_values,
                    reason=reason,
                    precondition=precondition,
                )
                plan = StoredWritePlan(
                    plan_id=plan_id,
                    plan_digest=digest,
                    operation=operation,
                    target=target,
                    form=form,
                    entry_id=entry_id,
                    current_values=(
                        None
                        if current_values is None
                        else dict(current_values)
                    ),
                    proposed_values=dict(proposed_values),
                    reason=reason,
                    precondition=precondition,
                    expires_at=self._clock() + self._ttl,
                )
                nonce, encrypted = self._encrypt(plan)
                connection.execute(
                    "INSERT INTO write_plans "
                    "(plan_id, plan_digest, operation, environment, "
                    "expires_at, status, nonce, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan.plan_id,
                        plan.plan_digest,
                        plan.operation.value,
                        plan.target.environment.value,
                        plan.expires_at,
                        plan.status.value,
                        nonce,
                        encrypted,
                    ),
                )
                return _public(plan)

            return self._execute(operation_in_transaction)

    async def acquire(
        self,
        *,
        plan_id: str,
        plan_digest: str,
        operation: WriteOperation,
        target: TargetKey,
    ) -> AcquiredWritePlan:
        async with self._lock:

            def operation_in_transaction(
                connection: sqlite3.Connection,
            ) -> AcquiredWritePlan:
                plan = self._required(connection, plan_id)
                if (
                    plan.operation is not operation
                    or plan.target != target
                    or not secrets.compare_digest(
                        plan.plan_digest,
                        plan_digest,
                    )
                ):
                    raise WritePlanMismatchError(
                        "write plan does not match the apply request"
                    )
                if plan.status is WritePlanStatus.APPLIED:
                    if plan.result is None:
                        raise WritePlanPersistenceError(
                            "applied write plan has no stored result"
                        )
                    return AcquiredWritePlan(
                        plan=None,
                        reused_result=plan.result.model_copy(
                            update={"reused_result": True}
                        ),
                    )
                if plan.status is WritePlanStatus.OUTCOME_UNKNOWN:
                    raise WriteOutcomeUnknownError(
                        "write outcome is unknown and cannot be retried"
                    )
                if plan.status is not WritePlanStatus.PENDING:
                    raise WritePlanStateError(
                        "write plan is not available for apply"
                    )
                plan.status = WritePlanStatus.APPLYING
                self._update(connection, plan)
                return AcquiredWritePlan(plan=plan)

            return self._execute(operation_in_transaction)

    async def complete(
        self,
        plan_id: str,
        result: ApplyWriteResult,
    ) -> ApplyWriteResult:
        async with self._lock:

            def operation_in_transaction(
                connection: sqlite3.Connection,
            ) -> ApplyWriteResult:
                plan = self._required(connection, plan_id, check_expiry=False)
                plan.status = WritePlanStatus.APPLIED
                plan.result = result
                _clear_payload(plan)
                self._update(connection, plan)
                return result

            return self._execute(operation_in_transaction)

    async def fail(self, plan_id: str, *, outcome_unknown: bool) -> None:
        async with self._lock:

            def operation_in_transaction(
                connection: sqlite3.Connection,
            ) -> None:
                plan = self._required(connection, plan_id, check_expiry=False)
                plan.status = (
                    WritePlanStatus.OUTCOME_UNKNOWN
                    if outcome_unknown
                    else WritePlanStatus.FAILED
                )
                _clear_payload(plan)
                self._update(connection, plan)

            self._execute(operation_in_transaction)

    async def get(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> WritePlanResult:
        async with self._lock:

            def operation_in_transaction(
                connection: sqlite3.Connection,
            ) -> WritePlanResult:
                plan = self._required(connection, plan_id)
                if plan.target != target:
                    raise WritePlanMismatchError(
                        "write plan does not match the selected target"
                    )
                return _public(plan)

            return self._execute(operation_in_transaction)

    async def cancel(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> WritePlanResult:
        async with self._lock:

            def operation_in_transaction(
                connection: sqlite3.Connection,
            ) -> WritePlanResult:
                plan = self._required(connection, plan_id)
                if plan.target != target:
                    raise WritePlanMismatchError(
                        "write plan does not match the selected target"
                    )
                if plan.status is not WritePlanStatus.PENDING:
                    raise WritePlanStateError(
                        "only pending write plans can be cancelled"
                    )
                plan.status = WritePlanStatus.CANCELLED
                _clear_payload(plan)
                self._update(connection, plan)
                return _public(plan)

            return self._execute(operation_in_transaction)

    def _initialize(
        self,
        connection: sqlite3.Connection,
        *,
        recover_interrupted: bool,
    ) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, _SCHEMA_VERSION}:
            raise WritePlanPersistenceError(
                "write-plan database schema is unsupported"
            )
        if version == 0:
            connection.execute(
                "CREATE TABLE write_plans ("
                "plan_id TEXT PRIMARY KEY NOT NULL, "
                "plan_digest TEXT NOT NULL, "
                "operation TEXT NOT NULL, "
                "environment TEXT NOT NULL, "
                "expires_at REAL NOT NULL, "
                "status TEXT NOT NULL, "
                "nonce BLOB NOT NULL, "
                "payload BLOB NOT NULL"
                ")"
            )
            connection.execute(
                "CREATE INDEX write_plans_expiry_idx "
                "ON write_plans(expires_at)"
            )
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        self._purge_expired(connection)
        if not recover_interrupted:
            return
        rows = connection.execute(
            "SELECT * FROM write_plans WHERE status = ?",
            (WritePlanStatus.APPLYING.value,),
        ).fetchall()
        for row in rows:
            plan = self._decode(row)
            plan.status = WritePlanStatus.OUTCOME_UNKNOWN
            _clear_payload(plan)
            self._update(connection, plan)

    def _required(
        self,
        connection: sqlite3.Connection,
        plan_id: str,
        *,
        check_expiry: bool = True,
    ) -> StoredWritePlan:
        row = connection.execute(
            "SELECT * FROM write_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise WritePlanNotFoundError("write plan was not found")
        plan = self._decode(row)
        if check_expiry and plan.expires_at <= self._clock():
            connection.execute(
                "DELETE FROM write_plans WHERE plan_id = ?",
                (plan_id,),
            )
            _clear_payload(plan)
            raise WritePlanExpiredError("write plan has expired")
        return plan

    def _purge_expired(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM write_plans WHERE expires_at <= ?",
            (self._clock(),),
        )

    def _update(
        self,
        connection: sqlite3.Connection,
        plan: StoredWritePlan,
    ) -> None:
        nonce, encrypted = self._encrypt(plan)
        cursor = connection.execute(
            "UPDATE write_plans SET status = ?, nonce = ?, payload = ? "
            "WHERE plan_id = ?",
            (plan.status.value, nonce, encrypted, plan.plan_id),
        )
        if cursor.rowcount != 1:
            raise WritePlanNotFoundError("write plan was not found")

    def _encrypt(self, plan: StoredWritePlan) -> tuple[bytes, bytes]:
        payload = json.dumps(
            {
                "form": plan.form,
                "entry_id": plan.entry_id,
                "current_values": plan.current_values,
                "proposed_values": plan.proposed_values,
                "reason": plan.reason,
                "precondition": plan.precondition,
                "result_stored": plan.result is not None,
                "result_entry_id": (
                    None if plan.result is None else plan.result.entry_id
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _MAX_PAYLOAD_BYTES:
            raise WritePlanPersistenceError(
                "write-plan payload exceeds the storage limit"
            )
        nonce = secrets.token_bytes(_NONCE_BYTES)
        return nonce, self._cipher.encrypt(nonce, payload, _aad(plan))

    def _decode(self, row: sqlite3.Row) -> StoredWritePlan:
        try:
            operation = WriteOperation(str(row["operation"]))
            environment = Environment(str(row["environment"]))
            status = WritePlanStatus(str(row["status"]))
            metadata = StoredWritePlan(
                plan_id=str(row["plan_id"]),
                plan_digest=str(row["plan_digest"]),
                operation=operation,
                target=TargetKey(environment=environment),
                form="placeholder",
                entry_id=None,
                current_values=None,
                proposed_values={},
                reason="",
                precondition=None,
                expires_at=float(row["expires_at"]),
                status=status,
            )
            raw = self._cipher.decrypt(
                bytes(row["nonce"]),
                bytes(row["payload"]),
                _aad(metadata),
            )
            if len(raw) > _MAX_PAYLOAD_BYTES:
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            plan = StoredWritePlan(
                plan_id=metadata.plan_id,
                plan_digest=metadata.plan_digest,
                operation=operation,
                target=metadata.target,
                form=_required_string(payload, "form"),
                entry_id=_optional_string(payload, "entry_id"),
                current_values=_optional_values(payload, "current_values"),
                proposed_values=_required_values(payload, "proposed_values"),
                reason=_required_string(payload, "reason", allow_empty=True),
                precondition=_optional_string(payload, "precondition"),
                expires_at=metadata.expires_at,
                status=status,
            )
            if payload.get("result_stored") is True:
                plan.result = ApplyWriteResult(
                    plan_id=plan.plan_id,
                    operation=plan.operation,
                    environment=plan.target.environment,
                    form=plan.form,
                    status=WritePlanStatus.APPLIED,
                    entry_id=_optional_string(payload, "result_entry_id"),
                )
            elif payload.get("result_stored") is not False:
                raise ValueError
            return plan
        except (
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            raise WritePlanPersistenceError(
                "write-plan database contains invalid encrypted data"
            ) from None

    def _execute(
        self,
        operation: Callable[[sqlite3.Connection], ResultT],
    ) -> ResultT:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=_SQLITE_TIMEOUT_SECONDS,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
            return result
        except WritePlanExpiredError:
            if connection is not None:
                connection.commit()
            raise
        except FormWriteError:
            if connection is not None:
                connection.rollback()
            raise
        except (OSError, sqlite3.Error):
            if connection is not None:
                connection.rollback()
            raise WritePlanPersistenceError(
                "write-plan persistence is unavailable"
            ) from None
        finally:
            if connection is not None:
                connection.close()


def _aad(plan: StoredWritePlan) -> bytes:
    return json.dumps(
        {
            "schema": _SCHEMA_VERSION,
            "plan_id": plan.plan_id,
            "plan_digest": plan.plan_digest,
            "operation": plan.operation.value,
            "environment": plan.target.environment.value,
            "expires_at": plan.expires_at,
            "status": plan.status.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_key(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise WritePlanPersistenceError(
                "write-plan encryption key is unavailable"
            )
        _require_private_permissions(path, "write-plan encryption key")
        key = path.read_bytes()
    except OSError:
        raise WritePlanPersistenceError(
            "write-plan encryption key is unavailable"
        ) from None
    if len(key) != _KEY_BYTES:
        raise WritePlanPersistenceError(
            "write-plan encryption key has an invalid length"
        )
    return key


def load_plan_encryption_key(path: Path) -> bytes:
    """Load the shared local plan key after the existing safety checks."""

    return _read_key(path.absolute())


def _prepare_database_path(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt" and path.parent.stat().st_mode & 0o077:
            raise WritePlanPersistenceError(
                "write-plan database directory permissions are too broad"
            )
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise WritePlanPersistenceError(
                    "write-plan database path is invalid"
                )
            _require_private_permissions(path, "write-plan database")
            return
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
    except WritePlanPersistenceError:
        raise
    except OSError:
        raise WritePlanPersistenceError(
            "write-plan database cannot be initialized"
        ) from None


def prepare_plan_database_path(path: Path) -> Path:
    """Prepare and return a private shared plan database path."""

    absolute = path.absolute()
    _prepare_database_path(absolute)
    return absolute


def _require_private_permissions(path: Path, label: str) -> None:
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise WritePlanPersistenceError(f"{label} permissions are too broad")


def _required_string(
    payload: dict[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError
    return value


def _required_values(
    payload: dict[str, Any],
    key: str,
) -> dict[str, JsonScalar]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError
    return _validated_values(value)


def _optional_values(
    payload: dict[str, Any],
    key: str,
) -> dict[str, JsonScalar] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError
    return _validated_values(value)


def _validated_values(value: dict[Any, Any]) -> dict[str, JsonScalar]:
    if not all(isinstance(field, str) and field for field in value):
        raise ValueError
    if not all(
        isinstance(item, (str, int, float, bool))
        and not isinstance(item, list)
        for item in value.values()
    ):
        raise ValueError
    return dict(value)
