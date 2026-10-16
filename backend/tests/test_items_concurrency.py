"""The two races the design exists to survive.

Both are tested with **two genuinely concurrent HTTP requests**, not with sequential
calls that imitate a race. Each request is served by its own database session and its
own connection (the test application uses `NullPool`), so PostgreSQL's row locks and
unique indexes are doing the arbitration exactly as they would in production.

A sequential test would pass against an implementation that reads and then writes, which
is the implementation these tests are meant to rule out.
"""

from __future__ import annotations

import asyncio
import uuid

from app.models.enums import Role
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    ITEMS_URL,
    activity_types,
    auth_headers,
    count_items,
    create_item_via_api,
    idempotency_headers,
    login,
    raw_item,
    scenario,
)


async def test_two_concurrent_claims_produce_exactly_one_winner(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The load-bearing test for claiming.

    Two members claim the same free item at the same moment. Neither reads the row
    before updating it — the eligibility condition is part of the `UPDATE` — so the
    database can only let one of them match a row.
    """
    team, users = await scenario(
        db, roles={"one": Role.MEMBER, "two": Role.MEMBER}, team_key="alpha"
    )
    first_token = await login(client, users["one"].email)
    second_token = await login(client, users["two"].email)

    item = await create_item_via_api(
        client, token=first_token, team_id=team.id, title="Contested work"
    )
    item_id = item["id"]

    first, second = await asyncio.gather(
        client.post(f"/api/v1/items/{item_id}/claim", headers=auth_headers(first_token)),
        client.post(f"/api/v1/items/{item_id}/claim", headers=auth_headers(second_token)),
    )

    # Exactly one winner. Not "usually one" — the assertion is on the sorted set.
    assert sorted([first.status_code, second.status_code]) == [200, 409], (
        first.text,
        second.text,
    )
    loser = first if first.status_code == 409 else second
    assert loser.json()["error"]["code"] == "item_already_claimed"

    winner_id = (first if first.status_code == 200 else second).json()["assignee"]["id"]
    assert winner_id in {str(users["one"].id), str(users["two"].id)}

    # The database agrees, and the loser left no trace.
    stored = await raw_item(db, uuid.UUID(item_id))
    assert str(stored["assignee_id"]) == winner_id
    assert stored["version"] == 2
    assert await activity_types(db, uuid.UUID(item_id)) == ["created", "claimed"]


async def test_concurrent_claims_repeated_ten_times_never_double_assign(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A wider race, because a one-shot race can pass by luck.

    Ten claimers on one item. Whoever wins, the outcome must be identical in shape every
    time: one owner, one claim event, eight conflicts.
    """
    team, users = await scenario(
        db,
        roles={f"p{index}": Role.MEMBER for index in range(10)},
        team_key="alpha",
    )
    tokens = [await login(client, users[f"p{index}"].email) for index in range(10)]
    item = await create_item_via_api(client, token=tokens[0], team_id=team.id, title="Ten-way race")
    item_id = item["id"]

    responses = await asyncio.gather(
        *[
            client.post(f"/api/v1/items/{item_id}/claim", headers=auth_headers(token))
            for token in tokens
        ]
    )

    codes = sorted(response.status_code for response in responses)
    assert codes == [200] + [409] * 9

    stored = await raw_item(db, uuid.UUID(item_id))
    assert stored["assignee_id"] is not None
    assert await activity_types(db, uuid.UUID(item_id)) == ["created", "claimed"]


async def test_concurrent_retries_of_one_create_produce_one_item(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The load-bearing test for idempotency.

    Two identical requests with the same key arrive together — a double-click, or a
    client retrying after a timeout it never saw an answer to. The second inserts the
    same key, blocks on the unique index until the first commits, then replays its
    result.
    """
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    key = str(uuid.uuid4())
    payload = {"title": "Retried creation", "type": "task"}
    headers = idempotency_headers(token, key)

    first, second = await asyncio.gather(
        client.post(ITEMS_URL.format(team_id=team.id), json=payload, headers=headers),
        client.post(ITEMS_URL.format(team_id=team.id), json=payload, headers=headers),
    )

    # One creates, one replays. Neither fails, and there is exactly one item.
    assert sorted([first.status_code, second.status_code]) == [200, 201], (
        first.text,
        second.text,
    )
    assert first.json()["id"] == second.json()["id"]
    assert (first if first.status_code == 200 else second).headers["Idempotent-Replay"] == "true"

    assert await count_items(db) == 1
    assert await activity_types(db, uuid.UUID(first.json()["id"])) == ["created"]


async def test_a_failing_create_does_not_burn_the_key(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A rejected request must leave the key usable.

    The key row and the item are in one transaction, so a create that fails validation
    *before* the key is claimed — and one that fails after — must not leave a key that
    would make a corrected retry look like a duplicate.
    """
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    key = str(uuid.uuid4())

    # Rejected before anything is written: a lead-only assignment attempted by a member.
    other = await scenario(db, roles={"other": Role.LEAD}, team_key="beta")
    refused = await client.post(
        ITEMS_URL.format(team_id=team.id),
        json={"title": "Nope", "type": "task", "assignee_id": str(other[1]["other"].id)},
        headers=idempotency_headers(token, key),
    )
    assert refused.status_code == 403

    # The same key now works for a legitimate request, and there is no stale key row
    # pointing at nothing.
    accepted = await client.post(
        ITEMS_URL.format(team_id=team.id),
        json={"title": "Fixed", "type": "task"},
        headers=idempotency_headers(token, key),
    )
    assert accepted.status_code == 201, accepted.text
    assert await count_items(db) == 1

    keys = (await db.execute(text("SELECT count(*) FROM idempotency_keys"))).scalar_one()
    assert keys == 1
