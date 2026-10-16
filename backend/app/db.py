"""Database engine and session management.

The engine is created once per application instance and stored on `app.state`,
which keeps tests free to point at a different database without monkeypatching
module-level globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(slots=True)
class Database:
    """A paired engine and session factory."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database(url: str, *, echo: bool = False) -> Database:
    """Build an async engine and session factory for `url`."""
    engine = create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        # Bounded pool: the API must not open an unbounded number of backend
        # connections under load. Tune per deployment rather than per request.
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
    )
    return Database(
        engine=engine,
        session_factory=async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
    )


def get_database(request: Request) -> Database:
    """Return the `Database` attached to the running application."""
    database: Database = request.app.state.db
    return database


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session.

    The session is closed when the request finishes. Callers are responsible
    for committing; nothing is auto-committed.
    """
    database = get_database(request)
    async with database.session_factory() as session:
        yield session
