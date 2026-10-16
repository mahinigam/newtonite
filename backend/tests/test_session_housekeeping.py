"""Session housekeeping.

Two controls that only an operator uses: revoking every session for an account, and
purging sessions that expired long ago. Both are exercised through the same service
functions the `scripts.maintenance` commands call, so there is no second
implementation to drift.

Revocation is the interesting one: it has to take effect for a token that is
currently valid, which is only true because sessions are validated against the
database on every request and never cached in the process.
"""

from __future__ import annotations

from app.services import auth as auth_service
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import auth_headers, create_user, login

ME = "/api/v1/auth/me"


async def test_revoking_all_sessions_invalidates_every_token(
    client: AsyncClient, db: AsyncSession
) -> None:
    user = await create_user(db, email="nina@newtonite.dev")
    await db.commit()

    # Three live sessions, for example a laptop, a phone and a stale browser tab.
    tokens = [await login(client, "nina@newtonite.dev") for _ in range(3)]
    for token in tokens:
        assert (await client.get(ME, headers=auth_headers(token))).status_code == 200

    revoked = await auth_service.revoke_all_for_user(db, user_id=user.id)

    assert revoked == 3
    for token in tokens:
        response = await client.get(ME, headers=auth_headers(token))
        assert response.status_code == 401, response.text


async def test_revoking_all_sessions_leaves_other_accounts_alone(
    client: AsyncClient, db: AsyncSession
) -> None:
    nina = await create_user(db, email="nina@newtonite.dev")
    await create_user(db, email="noah@newtonite.dev")
    await db.commit()

    nina_token = await login(client, "nina@newtonite.dev")
    noah_token = await login(client, "noah@newtonite.dev")

    await auth_service.revoke_all_for_user(db, user_id=nina.id)

    assert (await client.get(ME, headers=auth_headers(nina_token))).status_code == 401
    assert (await client.get(ME, headers=auth_headers(noah_token))).status_code == 200


async def test_revoking_all_sessions_is_safe_to_repeat(
    client: AsyncClient, db: AsyncSession
) -> None:
    user = await create_user(db, email="nina@newtonite.dev")
    await db.commit()
    await login(client, "nina@newtonite.dev")

    first = await auth_service.revoke_all_for_user(db, user_id=user.id)
    second = await auth_service.revoke_all_for_user(db, user_id=user.id)

    assert (first, second) == (1, 0)


async def test_purge_removes_only_long_expired_sessions(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A week of grace, so a recent session can still be inspected."""
    await create_user(db, email="nina@newtonite.dev")
    await db.commit()
    await login(client, "nina@newtonite.dev")  # the one that should survive
    await login(client, "nina@newtonite.dev")  # expired eight days ago

    await db.execute(
        text(
            "UPDATE auth_sessions SET expires_at = now() - interval '8 days' "
            "WHERE id = (SELECT id FROM auth_sessions ORDER BY created_at DESC LIMIT 1)"
        )
    )
    await db.commit()

    removed = await auth_service.delete_expired_sessions(db)

    assert removed == 1
    remaining = (await db.execute(text("SELECT count(*) FROM auth_sessions"))).scalar_one()
    assert remaining == 1


async def test_purge_keeps_recently_expired_sessions(client: AsyncClient, db: AsyncSession) -> None:
    await create_user(db, email="nina@newtonite.dev")
    await db.commit()
    await login(client, "nina@newtonite.dev")
    await db.execute(text("UPDATE auth_sessions SET expires_at = now() - interval '1 hour'"))
    await db.commit()

    removed = await auth_service.delete_expired_sessions(db)

    assert removed == 0
    # Still refused on read, just not deleted yet.
    assert (await db.execute(text("SELECT count(*) FROM auth_sessions"))).scalar_one() == 1
