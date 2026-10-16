"""Teams and per-team memberships."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin
from app.models.enums import Role, check_in

if TYPE_CHECKING:
    from app.models.user import User


class Team(Base, CreatedAtMixin):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="team", cascade="all, delete-orphan", lazy="raise"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Team {self.key}>"


class Membership(Base, CreatedAtMixin):
    """Grants a user a role inside one team.

    The unique constraint on ``(team_id, user_id)`` does double duty:

    * it enforces "at most one role per user per team", and
    * it is the unique key that ``work_items`` references with the composite
      foreign key ``(team_id, assignee_id) -> memberships(team_id, user_id)``.

    That foreign key is what makes "an assignee must belong to the item's team"
    a database invariant rather than only an application-level rule.

    A separate non-unique index on ``user_id`` serves "which teams does this user
    belong to", which is the prefix the composite constraint cannot answer.
    """

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise")
    team: Mapped[Team] = relationship(back_populates="memberships", lazy="raise")

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_memberships_team_id_user_id"),
        Index("ix_memberships_user_id", "user_id"),
        CheckConstraint(check_in("role", Role), name="role_is_known"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Membership user={self.user_id} team={self.team_id} role={self.role}>"


__all__ = ["Membership", "Team"]
