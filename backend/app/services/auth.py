"""Authentication: login, session resolution and logout.

Session model
-------------
A login returns a random opaque token. The database stores only its SHA-256
digest, so the token exists in exactly two places: the client that holds it, and
nowhere else.

A session is rejected when **any** of the following holds:

* the digest is unknown (the token was never issued, or was tampered with),
* `revoked_at` is set (the user signed out),
* `expires_at` has passed (**absolute** lifetime, counted from login),
* `last_used_at` is older than the idle window (**idle** lifetime).

Two clocks, one authority
-------------------------
Every expiry comparison is evaluated against PostgreSQL's `now()`, never the
application's clock. With more than one API process, or a container whose clock
drifts, an application-side comparison would let a session live longer on one
process than another. The database is the single clock.

Why `last_used_at` is not written on every request
-------------------------------------------------
Updating it per request would turn every read into a write. Instead the update is
conditional - it only happens once the stored value is more than
``TOUCH_INTERVAL_SECONDS`` old - so the column is refreshed at most once a minute
per session. The cost is that the idle window can be enforced up to a minute late,
which is recorded in `KNOWN_LIMITATIONS.md` rather than left implicit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, bindparam, delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.config import Settings
from app.errors import InvalidCredentials, InvalidSession, SessionExpired
from app.models import AuthSession, Membership, User
from app.models.enums import Role
from app.policy.principal import Principal
from app.policy.roles import parse_role
from app.security.passwords import spend_dummy_verification, verify_password
from app.security.tokens import generate_token, hash_token

# How stale `last_used_at` is allowed to get before it is refreshed.
TOUCH_INTERVAL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    expires_at: datetime
    session_id: uuid.UUID


def _interval(unit: str, quantity: int) -> TextClause:
    """`interval '1 <unit>' * :quantity`, parameterised.

    `unit` is always a literal from this module ('second' or 'hour'), never
    caller-supplied, so interpolating it is not an injection risk; `quantity`
    stays a bound parameter. The bind name is derived from both, so a statement
    can safely mix intervals of different lengths.
    """
    name = f"{unit}_{quantity}"
    return text(f"interval '1 {unit}' * :{name}").bindparams(
        bindparam(name, value=quantity, type_=Integer)
    )


async def login(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    settings: Settings,
) -> tuple[User, IssuedSession]:
    """Verify credentials and start a session.

    Raises `InvalidCredentials` for an unknown email, a wrong password and a
    disabled account alike, so the response cannot be used to discover which
    addresses have accounts.
    """
    normalised = email.strip().lower()
    user = (
        await session.execute(select(User).where(User.email == normalised))
    ).scalar_one_or_none()

    if user is None:
        # Spend comparable time to a real verification so the response time does
        # not reveal whether the account exists.
        spend_dummy_verification()
        raise InvalidCredentials()

    if not verify_password(password, user.password_hash):
        raise InvalidCredentials()

    if not user.is_active:
        raise InvalidCredentials()

    token = generate_token()
    statement = (
        insert(AuthSession)
        .values(
            user_id=user.id,
            token_hash=hash_token(token),
            # Expiry and the initial activity stamp both come from the database
            # clock, the same clock the checks below use.
            expires_at=func.now() + _interval("hour", settings.session_absolute_ttl_hours),
            last_used_at=func.now(),
        )
        .returning(AuthSession.id, AuthSession.expires_at)
    )
    row = (await session.execute(statement)).one()

    await session.execute(update(User).where(User.id == user.id).values(last_login_at=func.now()))
    await session.commit()

    return user, IssuedSession(token=token, expires_at=row.expires_at, session_id=row.id)


async def authenticate(session: AsyncSession, *, token: str, settings: Settings) -> Principal:
    """Resolve a bearer token to a `Principal`, or raise.

    Fails with `SessionExpired` when the session existed but timed out, and
    `InvalidSession` otherwise. The distinction is safe to expose - the caller
    already holds the token - and lets the frontend say "please sign in again"
    rather than "something is wrong".
    """
    digest = hash_token(token)

    row = (
        await session.execute(
            select(AuthSession.id, AuthSession.user_id, AuthSession.revoked_at)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.token_hash == digest,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > func.now(),
                AuthSession.last_used_at
                > func.now() - _interval("hour", settings.session_idle_ttl_hours),
                User.is_active.is_(True),
            )
        )
    ).one_or_none()

    if row is None:
        raise await _explain_failure(session, digest, settings)

    await _touch(session, row.id, settings)

    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
    return Principal(user=user, session_id=row.id, roles=await _roles_for(session, user.id))


async def revoke(session: AsyncSession, *, session_id: uuid.UUID) -> bool:
    """Sign out. Returns False when the session was already revoked.

    Deleting the row would be simpler, but revocation is recorded instead so the
    session's history survives and a stolen token cannot be resurrected by
    recreating the row.
    """
    revoked_ids = (
        (
            await session.execute(
                update(AuthSession)
                .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=func.now())
                .returning(AuthSession.id)
            )
        )
        .scalars()
        .all()
    )
    await session.commit()
    return bool(revoked_ids)


async def revoke_all_for_user(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Revoke every live session for a user.

    Not reachable over HTTP yet; used by tests and available for "sessions exposed"
    handling. See `docs/OPERATIONS.md`.
    """
    revoked_ids = (
        (
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=func.now())
                .returning(AuthSession.id)
            )
        )
        .scalars()
        .all()
    )
    await session.commit()
    return len(revoked_ids)


async def delete_expired_sessions(session: AsyncSession) -> int:
    """Housekeeping: drop sessions that are long past their absolute expiry.

    Expired rows are already rejected on read, so this is purely to stop the table
    growing without bound.
    """
    removed = (
        (
            await session.execute(
                delete(AuthSession)
                .where(AuthSession.expires_at < func.now() - _interval("hour", 24 * 7))
                .returning(AuthSession.id)
            )
        )
        .scalars()
        .all()
    )
    await session.commit()
    return len(removed)


async def _touch(session: AsyncSession, session_id: uuid.UUID, settings: Settings) -> None:
    """Refresh `last_used_at`, at most once per `TOUCH_INTERVAL_SECONDS`.

    The condition lives in the `WHERE` clause rather than in Python so two
    concurrent requests cannot both decide the row needs updating, and so the
    decision uses the database clock.
    """
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.id == session_id,
            AuthSession.last_used_at < func.now() - _interval("second", TOUCH_INTERVAL_SECONDS),
        )
        .values(last_used_at=func.now())
    )
    # Committed here rather than with the request's work: a read-only request still
    # needs the touch to persist, and the touch has nothing to do with the handler's
    # transaction.
    await session.commit()


async def _explain_failure(session: AsyncSession, digest: str, settings: Settings) -> Exception:
    """Classify why authentication failed, for a clearer client message.

    Runs only on the failure path, so the happy path stays a single query. The
    comparisons happen in SQL so they use the database clock, like the validity
    check that rejected the session.
    """
    existing = (
        await session.execute(
            select(
                (AuthSession.expires_at <= func.now()).label("expired"),
                (
                    AuthSession.last_used_at
                    < func.now() - _interval("hour", settings.session_idle_ttl_hours)
                ).label("idle"),
                AuthSession.revoked_at,
                User.is_active,
            )
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == digest)
        )
    ).one_or_none()

    if existing is None:
        return InvalidSession()
    if existing.revoked_at is not None or not existing.is_active:
        return InvalidSession()
    if existing.expired or existing.idle:
        return SessionExpired()

    # The session looks valid in isolation, so something else filtered it out.
    return InvalidSession()


async def _roles_for(session: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, Role]:
    rows = await session.execute(
        select(Membership.team_id, Membership.role).where(Membership.user_id == user_id)
    )
    return {row.team_id: parse_role(row.role) for row in rows}


__all__ = [
    "TOUCH_INTERVAL_SECONDS",
    "IssuedSession",
    "authenticate",
    "delete_expired_sessions",
    "login",
    "revoke",
    "revoke_all_for_user",
]
