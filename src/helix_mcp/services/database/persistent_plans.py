"""Encrypted SQLite persistence for restart-safe SQL query plans."""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from helix_mcp.config import Environment, TargetKey
from helix_mcp.services.database.errors import (
    DatabaseServiceError,
    SqlQueryPlanCapacityError,
    SqlQueryPlanExpiredError,
    SqlQueryPlanMismatchError,
    SqlQueryPlanNotFoundError,
    SqlQueryPlanPersistenceError,
    SqlQueryPlanStateError,
)
from helix_mcp.services.database.models import (
    DatabaseQuery,
    SqlQueryPlanResult,
    SqlQueryPlanStatus,
)
from helix_mcp.services.database.plans import (
    StoredSqlQueryPlan,
    _digest,
    _public,
)
from helix_mcp.services.writes.persistent_store import (
    load_plan_encryption_key,
    prepare_plan_database_path,
)

_NONCE_BYTES = 12
_MAX_PAYLOAD_BYTES = 131_072
_SQLITE_TIMEOUT_SECONDS = 5
_AAD_SCHEMA_VERSION = 1
ResultT = TypeVar("ResultT")


class PersistentSqlQueryPlanStore:
    """Persist encrypted SQL plans with one-time execution semantics."""

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
        self._database_path = prepare_plan_database_path(database_path)
        self._cipher = AESGCM(load_plan_encryption_key(key_path))
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._clock = clock
        self._lock = asyncio.Lock()
        self._execute(
            lambda connection: self._initialize(
                connection,
                recover_interrupted=recover_interrupted,
            )
        )

    async def create(
        self,
        *,
        target: TargetKey,
        query: DatabaseQuery,
    ) -> SqlQueryPlanResult:
        async with self._lock:

            def create_plan(
                connection: sqlite3.Connection,
            ) -> SqlQueryPlanResult:
                self._purge_expired(connection)
                pending = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM sql_query_plans "
                        "WHERE status IN (?, ?)",
                        (
                            SqlQueryPlanStatus.PENDING.value,
                            SqlQueryPlanStatus.EXECUTING.value,
                        ),
                    ).fetchone()[0]
                )
                if pending >= self._max_pending:
                    raise SqlQueryPlanCapacityError(
                        "pending SQL-query-plan capacity was reached"
                    )
                plan = StoredSqlQueryPlan(
                    plan_id=secrets.token_hex(16),
                    plan_digest=_digest(target=target, query=query),
                    target=target,
                    query=query,
                    expires_at=self._clock() + self._ttl,
                )
                nonce, payload = self._encrypt(plan)
                connection.execute(
                    "INSERT INTO sql_query_plans "
                    "(plan_id, plan_digest, environment, expires_at, status, "
                    "nonce, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan.plan_id,
                        plan.plan_digest,
                        plan.target.environment.value,
                        plan.expires_at,
                        plan.status.value,
                        nonce,
                        payload,
                    ),
                )
                return _public(plan, now=self._clock())

            return self._execute(create_plan)

    async def acquire(
        self,
        *,
        plan_id: str,
        plan_digest: str,
        target: TargetKey,
    ) -> StoredSqlQueryPlan:
        async with self._lock:

            def acquire_plan(
                connection: sqlite3.Connection,
            ) -> StoredSqlQueryPlan:
                plan = self._required(connection, plan_id)
                if plan.target != target or not secrets.compare_digest(
                    plan.plan_digest,
                    plan_digest,
                ):
                    raise SqlQueryPlanMismatchError(
                        "SQL query plan does not match the execution request"
                    )
                if plan.status is not SqlQueryPlanStatus.PENDING:
                    raise SqlQueryPlanStateError(
                        "SQL query plan is not available for execution"
                    )
                plan.status = SqlQueryPlanStatus.EXECUTING
                self._update(connection, plan)
                return plan

            return self._execute(acquire_plan)

    async def complete(self, plan_id: str) -> None:
        await self._transition(
            plan_id,
            expected=SqlQueryPlanStatus.EXECUTING,
            target=SqlQueryPlanStatus.EXECUTED,
        )

    async def fail(self, plan_id: str) -> None:
        await self._transition(
            plan_id,
            expected=SqlQueryPlanStatus.EXECUTING,
            target=SqlQueryPlanStatus.PENDING,
        )

    async def get(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> SqlQueryPlanResult:
        async with self._lock:
            return self._execute(
                lambda connection: self._get(connection, plan_id, target)
            )

    async def cancel(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> SqlQueryPlanResult:
        async with self._lock:

            def cancel_plan(
                connection: sqlite3.Connection,
            ) -> SqlQueryPlanResult:
                plan = self._matching(connection, plan_id, target)
                if plan.status is not SqlQueryPlanStatus.PENDING:
                    raise SqlQueryPlanStateError(
                        "only a pending SQL query plan can be cancelled"
                    )
                plan.status = SqlQueryPlanStatus.CANCELLED
                self._update(connection, plan)
                return _public(plan, now=self._clock())

            return self._execute(cancel_plan)

    async def _transition(
        self,
        plan_id: str,
        *,
        expected: SqlQueryPlanStatus,
        target: SqlQueryPlanStatus,
    ) -> None:
        async with self._lock:

            def transition(connection: sqlite3.Connection) -> None:
                plan = self._required(connection, plan_id, check_expiry=False)
                if plan.status is not expected:
                    raise SqlQueryPlanStateError(
                        "SQL query plan has an invalid execution state"
                    )
                plan.status = target
                self._update(connection, plan)

            self._execute(transition)

    def _get(
        self,
        connection: sqlite3.Connection,
        plan_id: str,
        target: TargetKey,
    ) -> SqlQueryPlanResult:
        return _public(
            self._matching(connection, plan_id, target),
            now=self._clock(),
        )

    def _matching(
        self,
        connection: sqlite3.Connection,
        plan_id: str,
        target: TargetKey,
    ) -> StoredSqlQueryPlan:
        plan = self._required(connection, plan_id)
        if plan.target != target:
            raise SqlQueryPlanMismatchError(
                "SQL query plan belongs to another target"
            )
        return plan

    def _initialize(
        self,
        connection: sqlite3.Connection,
        *,
        recover_interrupted: bool,
    ) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS sql_query_plans ("
            "plan_id TEXT PRIMARY KEY NOT NULL, "
            "plan_digest TEXT NOT NULL, "
            "environment TEXT NOT NULL, "
            "expires_at REAL NOT NULL, "
            "status TEXT NOT NULL, "
            "nonce BLOB NOT NULL, "
            "payload BLOB NOT NULL"
            ")"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS sql_query_plans_expiry_idx "
            "ON sql_query_plans(expires_at)"
        )
        self._purge_expired(connection)
        if recover_interrupted:
            rows = connection.execute(
                "SELECT * FROM sql_query_plans WHERE status = ?",
                (SqlQueryPlanStatus.EXECUTING.value,),
            ).fetchall()
            for row in rows:
                plan = self._decode(row)
                plan.status = SqlQueryPlanStatus.PENDING
                self._update(connection, plan)

    def _required(
        self,
        connection: sqlite3.Connection,
        plan_id: str,
        *,
        check_expiry: bool = True,
    ) -> StoredSqlQueryPlan:
        row = connection.execute(
            "SELECT * FROM sql_query_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise SqlQueryPlanNotFoundError("SQL query plan was not found")
        plan = self._decode(row)
        if check_expiry and plan.expires_at <= self._clock():
            connection.execute(
                "DELETE FROM sql_query_plans WHERE plan_id = ?",
                (plan_id,),
            )
            raise SqlQueryPlanExpiredError("SQL query plan has expired")
        return plan

    def _purge_expired(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM sql_query_plans WHERE expires_at <= ?",
            (self._clock(),),
        )

    def _update(
        self,
        connection: sqlite3.Connection,
        plan: StoredSqlQueryPlan,
    ) -> None:
        nonce, payload = self._encrypt(plan)
        cursor = connection.execute(
            "UPDATE sql_query_plans SET status = ?, nonce = ?, payload = ? "
            "WHERE plan_id = ?",
            (plan.status.value, nonce, payload, plan.plan_id),
        )
        if cursor.rowcount != 1:
            raise SqlQueryPlanNotFoundError("SQL query plan was not found")

    def _encrypt(self, plan: StoredSqlQueryPlan) -> tuple[bytes, bytes]:
        raw = json.dumps(
            {
                "sql": plan.query.sql,
                "limit": plan.query.limit,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > _MAX_PAYLOAD_BYTES:
            raise SqlQueryPlanPersistenceError(
                "SQL query plan exceeds the storage limit"
            )
        nonce = secrets.token_bytes(_NONCE_BYTES)
        return nonce, self._cipher.encrypt(nonce, raw, _aad(plan))

    def _decode(self, row: sqlite3.Row) -> StoredSqlQueryPlan:
        try:
            plan = StoredSqlQueryPlan(
                plan_id=str(row["plan_id"]),
                plan_digest=str(row["plan_digest"]),
                target=TargetKey(
                    environment=Environment(str(row["environment"]))
                ),
                query=DatabaseQuery(sql="SELECT 1", limit=1),
                expires_at=float(row["expires_at"]),
                status=SqlQueryPlanStatus(str(row["status"])),
            )
            raw = self._cipher.decrypt(
                bytes(row["nonce"]),
                bytes(row["payload"]),
                _aad(plan),
            )
            if len(raw) > _MAX_PAYLOAD_BYTES:
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            # v0.5.x plans included PostgreSQL parameters. Empty legacy
            # parameters are safe to discard; non-empty values cannot be
            # represented by ARGetListSQL and invalidate the old plan.
            legacy_parameters = payload.pop("parameters", ())
            if legacy_parameters not in ((), []):
                raise ValueError
            query = DatabaseQuery.model_validate(payload)
            return StoredSqlQueryPlan(
                plan_id=plan.plan_id,
                plan_digest=plan.plan_digest,
                target=plan.target,
                query=query,
                expires_at=plan.expires_at,
                status=plan.status,
            )
        except (
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ):
            raise SqlQueryPlanPersistenceError(
                "SQL-query-plan database contains invalid encrypted data"
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
        except SqlQueryPlanExpiredError:
            if connection is not None:
                connection.commit()
            raise
        except DatabaseServiceError:
            if connection is not None:
                connection.rollback()
            raise
        except (OSError, sqlite3.Error):
            if connection is not None:
                connection.rollback()
            raise SqlQueryPlanPersistenceError(
                "SQL-query-plan persistence is unavailable"
            ) from None
        finally:
            if connection is not None:
                connection.close()


def _aad(plan: StoredSqlQueryPlan) -> bytes:
    return json.dumps(
        {
            "schema": _AAD_SCHEMA_VERSION,
            "plan_id": plan.plan_id,
            "plan_digest": plan.plan_digest,
            "environment": plan.target.environment.value,
            "expires_at": plan.expires_at,
            "status": plan.status.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
