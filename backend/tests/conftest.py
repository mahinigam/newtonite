"""Shared test fixtures.

Design notes
------------
* Tests run against a **real PostgreSQL** database (`TEST_DATABASE_URL`).
  Several suites exist specifically to prove PostgreSQL behaviour (conditional
  atomic updates, unique-index blocking, composite foreign keys), so
  substituting SQLite or a mock would invalidate them.
* The schema is built once per session by running Alembic to head. Data is
  cleared between tests with `TRUNCATE ... RESTART IDENTITY CASCADE`, which is
  fast and - unlike `DELETE` - does not fire the append-only trigger on
  `activity_events`.
* The test application uses a `NullPool` engine. Concurrency tests open several
  independent sessions, and a connection pool could otherwise hand out a
  connection bound to a different event loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from app.config import Settings, get_settings
from app.db import Database
from app.main import create_app
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.support import TRUNCATE_STATEMENT, alembic_config


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return get_settings().test_database_url


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> str:
    """Bring the test database to head once per session."""
    command.upgrade(alembic_config(test_database_url), "head")
    return test_database_url


@pytest.fixture(scope="session")
def app(migrated_database: str) -> Iterator[FastAPI]:
    """The application under test, wired to the test database."""
    application = create_app(
        Settings(environment="test", database_url=migrated_database, log_level="WARNING")
    )
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    application.state.db = Database(
        engine=engine,
        session_factory=async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
    )
    yield application


@pytest.fixture
async def db(app: FastAPI) -> AsyncIterator[AsyncSession]:
    """A session for arranging or asserting on data directly.

    Each test starts from an empty database.
    """
    async with app.state.db.engine.begin() as connection:
        await connection.execute(TRUNCATE_STATEMENT)
    async with app.state.db.session_factory() as session:
        yield session


@pytest.fixture
async def client(app: FastAPI, db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A cookie-free HTTP client speaking directly to the ASGI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
