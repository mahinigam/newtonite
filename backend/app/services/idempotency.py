"""Idempotency records for retryable write operations.

A client that times out cannot tell whether its request succeeded, so retrying is the
right thing to do — and a retry must not produce a second result. The client supplies an
`Idempotency-Key`; this module turns that key into a guarantee.

How the guarantee is made, in the order the code makes it:

1. **The key is claimed** by inserting a row, with a `UNIQUE (actor_id, operation, key)`
   constraint deciding the winner. The insert happens inside a **savepoint**, so the
   expected unique violation is contained: without one, PostgreSQL would abort the whole
   transaction and refuse every later statement in the request.
2. **A concurrent duplicate blocks on the index** rather than reading stale state, and
   proceeds only once the first request commits or rolls back. If it commits, the loser
   replays; if it rolls back, the loser finds no row and becomes the winner.
3. **The stored fingerprint is compared** before anything is replayed. The same key with
   a different body is a client bug, and returning the first request's result would be
   silently wrong, so it is refused.
4. **The outcome is stored in the same transaction as the work**, so a key can never be
   marked complete for a row that was rolled back.

The module is deliberately independent of what is being created: `work_items` and
`comments` both use it, which is why the mechanism is a shared service rather than a
special case inside one of them. Each caller passes its own `Operation`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.errors import (
    IdempotencyKeyInProgress,
    IdempotencyKeyRequired,
    IdempotencyKeyReuse,
    ValidationFailed,
)
from app.models import IdempotencyKey
from app.models.enums import IdempotencyStatus, Operation
from app.models.idempotency import KEY_MAX_LENGTH


def validate_key(value: str | None) -> str:
    """Normalise a client-supplied `Idempotency-Key`, or refuse it.

    The header is **required**: without a key there is no way to tell a retry from a
    genuine second request, so the API will not guess. Refusing is the honest answer, and
    it makes retry safety impossible to overlook by accident.

    Validation lives here rather than in a route because both the item and comment
    endpoints need exactly this rule, and the length limit is a storage fact
    (`KEY_MAX_LENGTH` is the column's width).
    """
    if value is None or not value.strip():
        raise IdempotencyKeyRequired()
    key = value.strip()
    if len(key) > KEY_MAX_LENGTH:
        raise ValidationFailed(f"Idempotency-Key must be at most {KEY_MAX_LENGTH} characters.")
    return key


def fingerprint(request: Any) -> str:
    """A digest of the exact request body.

    Canonicalised with sorted keys, so merely reordering JSON fields is not treated as a
    different request, and hashed, so the stored value is not a copy of the payload.
    """
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def claim_key(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    operation: Operation,
    key: str,
    fingerprint: str,
) -> uuid.UUID | None:
    """Try to become the owner of an idempotency key.

    Returns the new key's id, or ``None`` when the key already belongs to somebody —
    which, because the insert blocks on the unique index, means an earlier request with
    this key has already **committed**.
    """
    try:
        async with session.begin_nested():
            key_id: uuid.UUID = (
                await session.execute(
                    pg_insert(IdempotencyKey)
                    .values(
                        actor_id=actor_id,
                        operation=operation.value,
                        key=key,
                        request_fingerprint=fingerprint,
                        status=IdempotencyStatus.IN_PROGRESS.value,
                    )
                    .returning(IdempotencyKey.id)
                )
            ).scalar_one()
        return key_id
    except IntegrityError:
        return None


async def replay(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    operation: Operation,
    key: str,
    fingerprint: str,
) -> uuid.UUID:
    """Answer a duplicate request from the record of the first one.

    Returns the id of the resource the first request created, so the caller can re-read
    it. The *current* representation is returned rather than a frozen copy of the
    original response: what is replayed is the identity — the same row, not a second
    one — and a fresh read can never be stale.
    """
    existing = (
        await session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.actor_id == actor_id,
                IdempotencyKey.operation == operation.value,
                IdempotencyKey.key == key,
            )
        )
    ).scalar_one()

    if existing.request_fingerprint != fingerprint:
        # The same key with a different body is a client bug. Returning the first
        # resource would be silently wrong, so it is refused.
        raise IdempotencyKeyReuse()

    if existing.status != IdempotencyStatus.COMPLETED.value or existing.resource_id is None:
        # Defensive: a committed key is always completed, because both writes happen in
        # one transaction. Reaching this means a row was left behind by something else.
        raise IdempotencyKeyInProgress()

    return existing.resource_id


async def complete_key(
    session: AsyncSession,
    *,
    key_id: uuid.UUID,
    resource_id: uuid.UUID,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    """Record the outcome so a later duplicate can replay it.

    Called inside the caller's transaction, after the work itself, so the key is never
    completed for something that was rolled back.
    """
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.id == key_id)
        .values(
            status=IdempotencyStatus.COMPLETED.value,
            response_status=response_status,
            resource_id=resource_id,
            response_body=response_body,
            completed_at=func.now(),
        )
    )


__all__ = ["claim_key", "complete_key", "fingerprint", "replay", "validate_key"]
