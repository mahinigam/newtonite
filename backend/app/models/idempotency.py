"""Idempotency records for retryable write operations.

A client that retries a create (double click, flaky network, "did it work?")
sends the same `Idempotency-Key`. The key row and the work item are written in
the same transaction, so a concurrent duplicate blocks on the unique index until
the first attempt commits, then replays the stored outcome instead of creating a
second item.

Reusing a key with a *different* payload is rejected rather than silently
returning an unrelated result.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import IdempotencyStatus, check_in

# SHA-256 rendered as lower-case hex.
FINGERPRINT_LENGTH = 64

# The storage limit for a client-supplied key, enforced by the column below and checked
# at the API boundary so an over-long key is a `422` rather than a database error.
KEY_MAX_LENGTH = 255


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The logical operation, for example "create_work_item". Keys are scoped to
    # the actor *and* the operation: two different endpoints can use the same
    # key string without colliding.
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH), nullable=False)

    # Digest of the request body. A different body under the same key is a
    # client bug and must not return the first request's result.
    request_fingerprint: Mapped[str] = mapped_column(String(FINGERPRINT_LENGTH), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The concurrency control for duplicate requests: the database, not the
        # application, decides which attempt owns a key.
        UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_keys_scope"),
        CheckConstraint(check_in("status", IdempotencyStatus), name="status_is_known"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<IdempotencyKey {self.operation}:{self.key} {self.status}>"


__all__ = ["FINGERPRINT_LENGTH", "KEY_MAX_LENGTH", "IdempotencyKey"]
