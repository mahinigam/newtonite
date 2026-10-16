"""Comments: writing one, reading them back, and the rules around both.

The interesting parts are not the happy path. They are:

* a comment and its history entry are one transaction — proven by breaking the insert on
  purpose in `test_activity_atomicity.py`;
* a retried comment is one comment, and a reused key with a *different* body is refused
  rather than answered with the first comment;
* a viewer may read the discussion but not add to it, and a non-member is told the item
  does not exist;
* paging a long thread neither duplicates nor skips a row, even when every row shares a
  timestamp and the `id` tie-breaker is doing all the work.
"""

from __future__ import annotations

import asyncio
import uuid

from app.models.enums import Role
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    COMMENTS_URL,
    activity_events,
    activity_types,
    api_comment,
    comment_ok,
    count_comments,
    create_item_via_api,
    idempotency_headers,
    insert_comments,
    login,
    move_to,
    raw_item,
    read_comments,
    scenario,
    walk_pages,
)


async def test_a_member_can_comment_and_the_timeline_records_it(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Needs a note")

    response = await api_comment(
        client, token=token, item_id=item["id"], body="Looks like a bad deploy."
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["body"] == "Looks like a bad deploy."
    assert body["work_item_id"] == item["id"]
    # The author comes from the session, never from the request body.
    assert body["author"]["email"] == users["member"].email
    assert body["created_at"]

    assert await count_comments(db, uuid.UUID(item["id"])) == 1
    events = await activity_events(db, uuid.UUID(item["id"]))
    assert [event["event_type"] for event in events] == ["created", "commented"]
    commented = events[-1]
    assert commented["meta"]["comment_id"] == body["id"]
    assert commented["meta"]["preview"] == "Looks like a bad deploy."
    assert commented["actor_id"] == users["member"].id
    # A comment changes no field of the item, so there is no "from" or "to".
    assert commented["from_value"] is None
    assert commented["to_value"] is None


async def test_a_comment_requires_an_idempotency_key(client: AsyncClient, db: AsyncSession) -> None:
    """Without a key there is no way to tell a retry from a second comment, so we refuse."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Guarded")

    response = await api_comment(client, token=token, item_id=item["id"], omit_key=True)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"
    assert await count_comments(db, uuid.UUID(item["id"])) == 0
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created"]


async def test_a_retried_comment_is_one_comment(client: AsyncClient, db: AsyncSession) -> None:
    """A double-click or a retry after a timeout must not post the note twice."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Retried")
    key = str(uuid.uuid4())

    first = await api_comment(client, token=token, item_id=item["id"], body="Same note", key=key)
    second = await api_comment(client, token=token, item_id=item["id"], body="Same note", key=key)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json()["id"] == first.json()["id"]

    assert await count_comments(db, uuid.UUID(item["id"])) == 1
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created", "commented"]


async def test_the_same_key_with_a_different_body_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Reusing a key for a different comment is a client bug, not a replay."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Careful")
    key = str(uuid.uuid4())

    await comment_ok(client, token=token, item_id=item["id"], body="The first note")
    first = await api_comment(
        client, token=token, item_id=item["id"], body="The first note", key=key
    )
    assert first.status_code == 201, first.text

    reused = await api_comment(
        client, token=token, item_id=item["id"], body="A different note", key=key
    )

    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reuse"
    assert await count_comments(db, uuid.UUID(item["id"])) == 2, "the first two still stand"


async def test_a_viewer_can_read_but_not_write_comments(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Reading the discussion is `VIEW_TEAM`; adding to it is `COMMENT`."""
    team, users = await scenario(
        db, roles={"member": Role.MEMBER, "viewer": Role.VIEWER}, team_key="alpha"
    )
    member_token = await login(client, users["member"].email)
    viewer_token = await login(client, users["viewer"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Visible")
    await comment_ok(client, token=member_token, item_id=item["id"], body="For the record")

    readable = await read_comments(client, token=viewer_token, item_id=item["id"])
    assert readable.status_code == 200, readable.text
    assert [row["body"] for row in readable.json()["items"]] == ["For the record"]

    refused = await api_comment(client, token=viewer_token, item_id=item["id"], body="Let me in")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden"
    assert await count_comments(db, uuid.UUID(item["id"])) == 1


async def test_a_non_member_sees_the_same_404_as_an_unknown_item(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A refused comment and an invented id must be indistinguishable."""
    alpha, alpha_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    _beta, beta_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="beta")
    alpha_token = await login(client, alpha_users["member"].email)
    beta_token = await login(client, beta_users["member"].email)
    item = await create_item_via_api(
        client, token=alpha_token, team_id=alpha.id, title="Alpha only"
    )

    foreign_read = await read_comments(client, token=beta_token, item_id=item["id"])
    unknown_read = await read_comments(client, token=beta_token, item_id=str(uuid.uuid4()))
    foreign_write = await api_comment(client, token=beta_token, item_id=item["id"], body="Nope")

    assert foreign_read.status_code == unknown_read.status_code == 404
    assert foreign_read.json() == unknown_read.json()
    assert foreign_write.status_code == 404
    assert foreign_write.json()["error"]["code"] == "not_found"
    assert await count_comments(db, uuid.UUID(item["id"])) == 0


async def test_commenting_requires_authentication(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Guarded")

    response = await client.post(COMMENTS_URL.format(item_id=item["id"]), json={"body": "Anon"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_blank_and_oversized_bodies_are_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Validated at the boundary, with the field named, and nothing written."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Strict")

    for bad in ("", "   ", "\n\t ", "x" * 4001):
        response = await api_comment(client, token=token, item_id=item["id"], body=bad)
        assert response.status_code == 422, (bad[:20], response.text)
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert error["details"][0]["field"] == "body"

    assert await count_comments(db, uuid.UUID(item["id"])) == 0


async def test_the_stored_body_is_trimmed(client: AsyncClient, db: AsyncSession) -> None:
    """Whitespace is normalised before it is stored, not after it is displayed."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Tidy")

    body = await comment_ok(client, token=token, item_id=item["id"], body="   padded   ")

    assert body["body"] == "padded"


async def test_comments_are_newest_first_and_page_without_gaps_or_duplicates(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Twelve comments with distinct timestamps, read back in exact order, five at a time."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Busy thread")
    bodies = [f"note {index:02d}" for index in range(12)]
    await insert_comments(
        db,
        item_id=uuid.UUID(item["id"]),
        author_id=users["member"].id,
        bodies=bodies,
        spaced=True,
    )

    rows = await walk_pages(
        lambda **kw: read_comments(client, token=token, item_id=item["id"], **kw), limit=5
    )

    assert [row["body"] for row in rows] == list(reversed(bodies))
    assert len({row["id"] for row in rows}) == 12


async def test_the_order_stays_total_when_every_row_shares_a_timestamp(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The case the `id` tie-breaker exists for.

    Twelve comments written in one transaction all get the same `created_at` (PostgreSQL's
    `now()` is the transaction's start time), so ordering by timestamp alone is not a
    total order. Paging must still visit every row exactly once — and the assertion that
    the timestamps really are identical is what keeps this test honest rather than
    accidentally testing the easy case.
    """
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Same instant")
    bodies = [f"note {index:02d}" for index in range(12)]
    await insert_comments(
        db, item_id=uuid.UUID(item["id"]), author_id=users["member"].id, bodies=bodies
    )

    rows = await walk_pages(
        lambda **kw: read_comments(client, token=token, item_id=item["id"], **kw), limit=4
    )

    assert len(rows) == 12
    assert len({row["id"] for row in rows}) == 12, "a row was returned twice or skipped"
    keys = [(row["created_at"], row["id"]) for row in rows]
    assert len({key[0] for key in keys}) == 1, "the fixture failed to produce equal timestamps"
    assert keys == sorted(keys, reverse=True), "the page order is not the declared order"


async def test_a_long_thread_walks_to_the_end(client: AsyncClient, db: AsyncSession) -> None:
    """A page size that does not divide the total must still finish cleanly."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Long thread")
    bodies = [f"note {index}" for index in range(10)]
    await insert_comments(
        db,
        item_id=uuid.UUID(item["id"]),
        author_id=users["member"].id,
        bodies=bodies,
        spaced=True,
    )

    rows = await walk_pages(
        lambda **kw: read_comments(client, token=token, item_id=item["id"], **kw), limit=3
    )

    assert len(rows) == 10
    assert [row["body"] for row in rows] == list(reversed(bodies))


async def test_comments_belong_to_one_item_only(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    first = await create_item_via_api(client, token=token, team_id=team.id, title="First")
    second = await create_item_via_api(client, token=token, team_id=team.id, title="Second")
    await comment_ok(client, token=token, item_id=first["id"], body="On the first")

    page = await read_comments(client, token=token, item_id=second["id"])

    assert page.status_code == 200, page.text
    assert page.json()["items"] == []
    assert page.json()["has_more"] is False
    assert page.json()["next_cursor"] is None


async def test_a_closed_item_still_accepts_a_comment(client: AsyncClient, db: AsyncSession) -> None:
    """Closure ends the *workflow*, not the discussion.

    A closed item is a record, and adding a note for the record does not change it: no
    field moves, no version bumps. Refusing the comment would only push people to record
    the reason somewhere the item's history cannot see it.
    """
    team, users = await scenario(
        db, roles={"lead": Role.LEAD, "member": Role.MEMBER}, team_key="alpha"
    )
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client,
        token=lead_token,
        team_id=team.id,
        title="Done and dusted",
        assignee_id=str(users["member"].id),
    )
    await move_to(client, token=member_token, item_id=item["id"], target="in_progress")
    await move_to(client, token=member_token, item_id=item["id"], target="awaiting_approval")
    await move_to(client, token=lead_token, item_id=item["id"], target="resolved")
    closed = await move_to(client, token=lead_token, item_id=item["id"], target="closed")
    assert closed["status"] == "closed"

    response = await api_comment(
        client, token=member_token, item_id=item["id"], body="Root cause was the deploy."
    )

    assert response.status_code == 201, response.text
    state = await raw_item(db, uuid.UUID(item["id"]))
    assert state["status"] == "closed"
    assert state["version"] == closed["version"], "a comment must not bump the version"


async def test_a_long_body_is_previewed_not_copied_into_history(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`meta.preview` is rendering help, so it is bounded and whitespace-collapsed."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Wordy")
    body = "line one\n\nline two " + "y" * 500

    created = await comment_ok(client, token=token, item_id=item["id"], body=body)

    events = await activity_events(db, uuid.UUID(item["id"]))
    preview = events[-1]["meta"]["preview"]
    assert "\n" not in preview
    assert len(preview) <= 200
    assert preview.startswith("line one line two")
    # The comment itself keeps the body intact; only the event is abbreviated.
    assert created["body"] == body


async def test_concurrent_identical_comments_create_one(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The same race as the item create, on the comment path.

    Two requests with one key arrive together. One creates, one replays: one comment, one
    `commented` event, and no request that failed.
    """
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Raced note")
    key = str(uuid.uuid4())
    headers = idempotency_headers(token, key)
    url = COMMENTS_URL.format(item_id=item["id"])

    first, second = await asyncio.gather(
        client.post(url, json={"body": "One note"}, headers=headers),
        client.post(url, json={"body": "One note"}, headers=headers),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 201], (
        first.text,
        second.text,
    )
    assert first.json()["id"] == second.json()["id"]
    assert (first if first.status_code == 200 else second).headers["Idempotent-Replay"] == "true"
    assert await count_comments(db, uuid.UUID(item["id"])) == 1
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created", "commented"]


async def test_the_limit_is_bounded_and_a_bad_cursor_is_a_validation_error(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Bounded")

    for limit in (0, 101):
        response = await read_comments(client, token=token, item_id=item["id"], limit=limit)
        assert response.status_code == 422, limit

    garbage = await read_comments(client, token=token, item_id=item["id"], cursor="not-a-cursor")
    assert garbage.status_code == 422
    assert garbage.json()["error"]["code"] == "validation_failed"


async def test_comments_require_a_bearer_token_even_to_read(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Closed door")

    response = await client.get(COMMENTS_URL.format(item_id=item["id"]))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
