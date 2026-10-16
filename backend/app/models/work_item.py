"""The work item: the central operational record."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import Priority, Status, WorkItemType, check_in

if TYPE_CHECKING:
    from app.models.team import Team
    from app.models.user import User

# Full-text search over title + description. Declared once so the model and the
# migration cannot drift apart.
SEARCH_VECTOR_EXPRESSION = (
    "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))"
)


class WorkItem(Base):
    """A unit of operational work owned by exactly one team."""

    __tablename__ = "work_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'open'"))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Optimistic concurrency token. Every update bumps it by exactly one, and
    # the bump is part of the same statement as the version comparison, which is
    # what makes stale-write rejection race-free.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(SEARCH_VECTOR_EXPRESSION, persisted=True),
        nullable=False,
    )

    team: Mapped[Team] = relationship(lazy="raise")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="raise")
    creator: Mapped[User] = relationship(foreign_keys=[created_by], lazy="raise")

    __table_args__ = (
        # An assignee must be a member of the item's team. Because PostgreSQL
        # uses MATCH SIMPLE by default, a NULL assignee skips the check, which is
        # exactly the behaviour we want for unassigned items.
        ForeignKeyConstraint(
            ["team_id", "assignee_id"],
            ["memberships.team_id", "memberships.user_id"],
            name="fk_work_items_team_id_assignee_id_memberships",
        ),
        CheckConstraint(check_in("type", WorkItemType), name="type_is_known"),
        CheckConstraint(check_in("priority", Priority), name="priority_is_known"),
        CheckConstraint(check_in("status", Status), name="status_is_known"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("char_length(title) BETWEEN 3 AND 200", name="title_length"),
        CheckConstraint("char_length(description) <= 20000", name="description_length"),
        # Team-scoped, status-filtered lists ordered newest-first. This is the
        # workhorse index for the list and dashboard endpoints.
        Index(
            "ix_work_items_team_status_created",
            "team_id",
            "status",
            text("created_at DESC"),
            text("id DESC"),
        ),
        # "Assigned to me" across the teams the caller belongs to.
        Index(
            "ix_work_items_assignee_created",
            "assignee_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        # "Unassigned urgent" dashboard view. Partial so it stays small: it only
        # covers rows the unassigned-urgent queue can actually return.
        Index(
            "ix_work_items_unassigned_urgent",
            "team_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text(
                "assignee_id IS NULL AND status = 'open' AND priority IN ('high','urgent')"
            ),
        ),
        # Full-text search.
        Index("ix_work_items_search_vector", "search_vector", postgresql_using="gin"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<WorkItem {self.id} {self.status} v{self.version}>"


__all__ = ["SEARCH_VECTOR_EXPRESSION", "WorkItem"]
