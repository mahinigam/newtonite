"""History and state change are one transaction.

The claim in the design is strong — "every important mutation writes its history entry in
the same transaction, so if either write fails both roll back" — and a test that only
checks that history is normally written proves nothing about the coupling. So these tests
**force the history insert to fail** with a trigger and then check that the state change
disappeared with it.

The trigger is installed *after* the fixture data exists, because it blocks every insert
into `activity_events`, including the one that recording a new item writes. An autouse
fixture removes it afterwards regardless of how the test ended, since a leftover trigger
would break every later test in the session — the truncate fixture clears rows, not
triggers.

Two things are asserted each time: the item is exactly as it was, and there is no
half-written history row. The response body is checked as well, because a database-level
failure is exactly the situation in which leaking a stack trace or a constraint name is
most tempting.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from app.models.enums import Role
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    activity_types,
    api_comment,
    api_patch,
    auth_headers,
    claim_ok,
    count_comments,
    create_item_via_api,
    etag_headers,
    login,
    raw_item,
    scenario,
)

# asyncpg prepares one statement per round trip, so these run individually rather than as
# a single script.
BREAK_HISTORY = (
    """
    CREATE OR REPLACE FUNCTION test_fail_activity_insert() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'activity_events insert deliberately failed (test)';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS test_fail_activity_insert ON activity_events",
    """
    CREATE TRIGGER test_fail_activity_insert
        BEFORE INSERT ON activity_events
        FOR EACH ROW EXECUTE FUNCTION test_fail_activity_insert()
    """,
)

REPAIR_HISTORY = (
    "DROP TRIGGER IF EXISTS test_fail_activity_insert ON activity_events",
    "DROP FUNCTION IF EXISTS test_fail_activity_insert()",
)


async def _run(db: AsyncSession, statements: tuple[str, ...]) -> None:
    for statement in statements:
        await db.execute(text(statement))
    await db.commit()


async def break_history(db: AsyncSession) -> None:
    await _run(db, BREAK_HISTORY)


async def repair_history(db: AsyncSession) -> None:
    await _run(db, REPAIR_HISTORY)


@pytest.fixture(autouse=True)
async def _never_leak_the_trigger(db: AsyncSession) -> AsyncIterator[None]:
    yield
    await repair_history(db)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client that receives error responses instead of the exception itself.

    This deliberately overrides the shared `client` fixture. The shared one uses
    `raise_app_exceptions=True`, which re-raises an unhandled server error into the test —
    very useful when debugging, but not what a real HTTP client sees. These tests are
    about what the *caller* is told when history cannot be written, so the transport is
    configured to deliver the 500 the application produced.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


async def test_a_failed_history_write_rolls_back_an_edit(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The edit and its history entry are one unit of work."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Original")
    item_id = item["id"]
    before = await raw_item(db, uuid.UUID(item_id))
    history_before = await activity_types(db, uuid.UUID(item_id))
    await break_history(db)

    response = await api_patch(
        client, token=token, item_id=item_id, etag='"1"', title="Should never persist"
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    # The failure is reported generically: no SQL, no trigger name, no table name.
    for leak in (
        "activity_events",
        "test_fail_activity_insert",
        "RAISE",
        "INSERT INTO",
        "asyncpg",
        "Traceback",
    ):
        assert leak not in response.text, leak

    after = await raw_item(db, uuid.UUID(item_id))
    assert after == before, "the item changed even though its history could not be written"
    assert await activity_types(db, uuid.UUID(item_id)) == history_before


async def test_a_failed_history_write_rolls_back_a_claim(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A claim is not a claim until it has been recorded.

    If the assignment survived without its history entry, the timeline would say nobody
    owns the item while the item said somebody did — and the two would disagree forever,
    because history is append-only.
    """
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Never claimed")
    await break_history(db)

    response = await client.post(f"/api/v1/items/{item['id']}/claim", headers=auth_headers(token))

    assert response.status_code == 500
    stored = await raw_item(db, uuid.UUID(item["id"]))
    assert stored["assignee_id"] is None, "the claim survived without its history entry"
    assert stored["version"] == 1
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created"]


async def test_a_failed_history_write_rolls_back_a_transition(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The same coupling for the workflow: no history, no status change."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Does not move")
    item_id = item["id"]
    await claim_ok(client, token=token, item_id=item_id)
    before = await raw_item(db, uuid.UUID(item_id))
    await break_history(db)

    response = await client.post(
        f"/api/v1/items/{item_id}/transitions",
        json={"to": "in_progress"},
        headers=etag_headers(token, '"2"'),
    )

    assert response.status_code == 500
    after = await raw_item(db, uuid.UUID(item_id))
    assert after == before
    assert after["status"] == "open"
    assert await activity_types(db, uuid.UUID(item_id)) == ["created", "claimed"]


async def test_a_failed_history_write_rolls_back_a_comment(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A comment is not a comment until it has been recorded.

    The comment insert and its `commented` event are one transaction, so a comment can
    never exist without the timeline entry that explains it. The retry at the end also
    shows that a *failed* attempt does not burn the idempotency key: the key row rolled
    back with the comment it would have pointed at.
    """
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="No note")
    item_id = item["id"]
    key = str(uuid.uuid4())
    await break_history(db)

    response = await api_comment(
        client, token=token, item_id=item_id, body="Should not persist", key=key
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    for leak in ("activity_events", "test_fail_activity_insert", "RAISE", "asyncpg", "Traceback"):
        assert leak not in response.text, leak
    assert await count_comments(db, uuid.UUID(item_id)) == 0, "the comment outlived its history"
    assert await activity_types(db, uuid.UUID(item_id)) == ["created"]

    await repair_history(db)
    retried = await api_comment(
        client, token=token, item_id=item_id, body="Should not persist", key=key
    )
    assert retried.status_code == 201, retried.text
    assert await count_comments(db, uuid.UUID(item_id)) == 1
    assert await activity_types(db, uuid.UUID(item_id)) == ["created", "commented"]


async def test_repairing_the_trigger_restores_normal_behaviour(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Proves the failures above came from the trigger, not from something else.

    The same request that just failed with a 500 succeeds once the trigger is gone, which
    also shows the rolled-back transaction left nothing behind that blocks a retry.
    """
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Recoverable")

    await break_history(db)
    failed = await api_patch(
        client, token=token, item_id=item["id"], etag='"1"', title="First attempt"
    )
    assert failed.status_code == 500

    await repair_history(db)

    succeeded = await api_patch(
        client, token=token, item_id=item["id"], etag='"1"', title="Second attempt"
    )
    assert succeeded.status_code == 200, succeeded.text
    assert succeeded.json()["title"] == "Second attempt"
    assert succeeded.json()["version"] == 2
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created", "title_changed"]
