"""Append-only activity history.

Every successful, operationally meaningful mutation of a work item writes one
row here, in the same transaction as the mutation itself. If either write fails
both roll back, so the history can never disagree with the item.

There is no update or delete path in the application, and the migration installs
a database trigger that rejects UPDATE and DELETE outright. History is a record
of what happened, not a cache that can be rewritten.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import ActivityType, check_in

if TYPE_CHECKING:
    from app.models.user import User


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    # A monotonic bigint is the natural append-only key: it gives a strict total
    # order for keyset pagination without depending on timestamp precision.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_items.id", ondelete="RESTRICT"), nullable=False
    )
    # Denormalised from the work item so team-scoped activity queries and
    # authorization checks never need to join back to `work_items`.
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Rendering-ready before/after values. Kept as text so one table can describe
    # status, priority, type and assignee changes alike.
    from_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form structured context (for example a rejection note or the id of a
    # newly attached comment).
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    actor: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint(check_in("event_type", ActivityType), name="event_type_is_known"),
        # Per-item timeline, newest first, with `id` as a deterministic
        # tie-breaker for keyset pagination.
        Index(
            "ix_activity_events_work_item_id_created_at",
            "work_item_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_activity_events_team_id_created_at",
            "team_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ActivityEvent {self.id} {self.event_type}>"


__all__ = ["ActivityEvent"]
