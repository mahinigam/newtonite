"""Comments on work items."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

MAX_COMMENT_LENGTH = 4000


class Comment(Base):
    """A human note attached to a work item.

    Comments are append-only. There is no edit or delete path, so a comment can
    never contradict the activity history it produced.
    """

    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    author: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint(
            f"char_length(btrim(body)) BETWEEN 1 AND {MAX_COMMENT_LENGTH}",
            name="body_length",
        ),
        # Chronological reads of a single item's comments.
        Index("ix_comments_work_item_id_created_at", "work_item_id", "created_at", "id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Comment {self.id} item={self.work_item_id}>"


__all__ = ["MAX_COMMENT_LENGTH", "Comment"]
