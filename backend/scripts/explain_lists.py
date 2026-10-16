"""Print the query plans behind the numbers in `docs/DATA_MODEL.md`.

This is a measuring instrument, not part of the application or the test suite. It exists
so the performance claims in the documentation can be reproduced rather than taken on
trust:

    uv run python -m scripts.explain_lists

It imports `list_statement` from the service layer, so the SQL it explains is the SQL the
API actually executes. A hand-written approximation would be easier to write and would
drift the first time a filter was added.

Run it against the seeded development database. On an empty database every plan is a
sequential scan, because the tables are too small for an index to win — the numbers only
mean something once there is a few thousand rows to walk past.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from app.config import get_settings
from app.db import create_database
from app.models.enums import Priority, Status
from app.services.cursors import Cursor
from app.services.work_items import list_statement
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.ext.asyncio import AsyncSession

# Named with an explicit signature because SQLAlchemy's stubs leave `PGDialect.__init__`
# untyped, and mypy refuses an untyped call from annotated code. Calling it is the
# documented way to obtain a dialect instance for compilation.
POSTGRES_DIALECT: Callable[[], Dialect] = postgresql.dialect

# Taken from the seed rather than hard-coded as UUIDs, so this keeps working if the seed
# is regenerated and identifiers change.
TEAM_KEY = "platform"
VIEWER_EMAIL = "sam.multi@newtonite.dev"


def compile_sql(statement: Any) -> str:
    """Render a statement as SQL with values inlined, ready for `EXPLAIN`.

    Inlining is deliberate: a parameterised `EXPLAIN` cannot be asked about a specific
    value, and the point here is the plan for a realistic one. The statements measured
    come from the service layer, never from user input.
    """
    return str(
        statement.compile(dialect=POSTGRES_DIALECT(), compile_kwargs={"literal_binds": True})
    )


async def resolve_seed_ids(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    team_id = (
        await session.execute(text("SELECT id FROM teams WHERE key = :key"), {"key": TEAM_KEY})
    ).scalar_one()
    viewer_id = (
        await session.execute(
            text("SELECT id FROM users WHERE email = :email"), {"email": VIEWER_EMAIL}
        )
    ).scalar_one()
    return team_id, viewer_id


async def deep_cursor(session: AsyncSession, team_id: uuid.UUID) -> Cursor:
    """A cursor from 300 rows into the team's items.

    A hand-picked timestamp would make the "deep page" measurement meaningless — it would
    either match nothing or everything. Reading a real position means the plan has
    somewhere genuine to start walking from.
    """
    row = (
        await session.execute(
            text(
                "SELECT created_at, id FROM work_items WHERE team_id = :team "
                "ORDER BY created_at DESC, id DESC OFFSET 300 LIMIT 1"
            ),
            {"team": team_id},
        )
    ).one()
    return Cursor(created_at=row[0], id=row[1])


async def explain(session: AsyncSession, label: str, sql: str) -> None:
    lines = (await session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {sql}"))).scalars().all()
    plan = "\n".join(str(line) for line in lines)
    timing = next(
        (line.strip() for line in plan.splitlines() if "Execution Time" in line),
        "no timing reported",
    )
    print("=" * 78)
    print(label)
    print("=" * 78)
    print(plan)
    print(f"--> {timing}")
    print()


async def main() -> None:
    database = create_database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            team_id, viewer_id = await resolve_seed_ids(session)

            cases: list[tuple[str, Mapping[str, Any]]] = [
                ("team list, first page (no filter)", {}),
                ("team list, filtered by status", {"statuses": (Status.OPEN,)}),
                (
                    "team list, filtered by status and priority",
                    {
                        "statuses": (Status.IN_PROGRESS,),
                        "priorities": (Priority.HIGH, Priority.URGENT),
                    },
                ),
                (
                    "queue: unassigned high-or-urgent items",
                    {"unassigned": True, "priorities": (Priority.HIGH, Priority.URGENT)},
                ),
                (
                    "queue: unassigned high-or-urgent OPEN items (the partial index's shape)",
                    {
                        "unassigned": True,
                        "statuses": (Status.OPEN,),
                        "priorities": (Priority.HIGH, Priority.URGENT),
                    },
                ),
                ("dashboard: assigned to me", {"only_mine": True}),
                ("search: full text", {"search": "payment failed"}),
                (
                    "team list, deep page by cursor",
                    {"cursor": await deep_cursor(session, team_id)},
                ),
            ]

            for label, filters in cases:
                statement = list_statement(
                    team_id=team_id, viewer_id=viewer_id, limit=25, **filters
                )
                await explain(session, label, compile_sql(statement))
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
