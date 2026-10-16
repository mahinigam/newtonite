"""Authentication sessions.

The API issues an opaque random bearer token at login. Only a SHA-256 digest of
that token is stored here, so a database disclosure does not hand an attacker
usable credentials.

A fast digest is the right choice for session tokens specifically: the token is
256 bits of `secrets` randomness, so there is nothing to brute force, unlike a
low-entropy user password which gets Argon2id.

Sessions are rejected when any of the following holds:

* the digest is unknown,
* `expires_at` has passed (absolute lifetime),
* `last_used_at` is older than the idle window,
* `revoked_at` is set (logout).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# SHA-256 rendered as lower-case hex.
TOKEN_HASH_LENGTH = 64


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (
        # Supports "revoke every session for this user" and per-user auditing.
        Index("ix_auth_sessions_user_id", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuthSession {self.id} user={self.user_id}>"


from app.models.user import User  # noqa: E402  (resolves the relationship target)

__all__ = ["TOKEN_HASH_LENGTH", "AuthSession", "User"]
