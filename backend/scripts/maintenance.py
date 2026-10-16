"""Small operational commands.

These are deliberately not HTTP endpoints: they are things an operator does, not
things a user does, and putting them behind a normal login would widen the API
surface for no reason.

    uv run python -m scripts.maintenance revoke-sessions --email someone@example.com
    uv run python -m scripts.maintenance purge-expired-sessions
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.config import get_settings
from app.db import Database, create_database
from app.models import AuthSession, User
from app.services import auth as auth_service
from sqlalchemy import func, select


async def revoke_sessions(database: Database, email: str) -> int | None:
    """Revoke every live session for one account.

    Returns the number revoked, or ``None`` when no such account exists - so an
    operator can tell a typo from a user who simply had no sessions.

    This is the "sessions may be compromised" lever: it takes effect on the next
    request, because sessions are validated against the database and never cached
    in the process.
    """
    async with database.session_factory() as session:
        user_id = (
            await session.execute(select(User.id).where(User.email == email.strip().lower()))
        ).scalar_one_or_none()
        if user_id is None:
            return None
        return await auth_service.revoke_all_for_user(session, user_id=user_id)


async def purge_expired_sessions(database: Database) -> tuple[int, int]:
    """Delete sessions that expired more than a week ago.

    Expired rows are already refused on read, so this is purely to stop the table
    growing without bound. The grace period exists so a support question about a
    recent session can still be answered from the table.
    """
    async with database.session_factory() as session:
        removed = await auth_service.delete_expired_sessions(session)
        remaining = (
            await session.execute(select(func.count()).select_from(AuthSession))
        ).scalar_one()
    return removed, remaining


async def _run(args: argparse.Namespace) -> int:
    database = create_database(get_settings().database_url)
    try:
        if args.command == "revoke-sessions":
            revoked = await revoke_sessions(database, args.email)
            if revoked is None:
                print(f"No account with the email {args.email!r}.")
                return 1
            print(f"Revoked {revoked} session(s) for {args.email}.")
            return 0

        removed, remaining = await purge_expired_sessions(database)
        print(f"Removed {removed} expired session(s); {remaining} session(s) remain.")
        return 0
    finally:
        # Disposed inside the same event loop that opened the connections: closing
        # the loop first leaves asyncpg's pool holding sockets it can no longer
        # release.
        await database.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operational maintenance commands.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    revoke = subcommands.add_parser(
        "revoke-sessions", help="revoke every live session for one account"
    )
    revoke.add_argument("--email", required=True, help="the account's email address")

    subcommands.add_parser(
        "purge-expired-sessions", help="delete sessions expired more than a week ago"
    )

    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
