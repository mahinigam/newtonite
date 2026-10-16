"""Creating work items, with idempotency.

The behaviour that matters is that a retry cannot produce a second item, and that a
reused key with a *different* body is refused rather than answered with somebody else's
result. Everything else here is the authorization and validation boundary.
"""

from __future__ import annotations

import uuid

from app.models.enums import Role, Status
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    ITEMS_URL,
    activity_types,
    api_create_item,
    auth_headers,
    count_items,
    create_item_via_api,
    idempotency_headers,
    login,
    raw_item,
    scenario,
)

VALID_BODY = {"title": "Investigate the nightly job failure", "type": "incident"}


async def test_create_returns_the_item_and_an_etag(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    response = await api_create_item(client, token=token, team_id=team.id, **VALID_BODY)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == VALID_BODY["title"]
    assert body["type"] == "incident"
    # New items always start open, whatever the caller might have hoped.
    assert body["status"] == "open"
    assert body["version"] == 1
    assert body["assignee"] is None
    assert body["team_id"] == str(team.id)
    assert body["created_by"]["email"] == users["member"].email
    assert response.headers["etag"] == '"1"'
    # The workflow is described by the server, not guessed at by the client.
    assert body["available_transitions"] == []


async def test_create_records_a_created_event(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    body = await create_item_via_api(client, token=token, team_id=team.id, **VALID_BODY)

    assert await activity_types(db, uuid.UUID(body["id"])) == ["created"]
    actor = (
        await db.execute(
            text("SELECT actor_id FROM activity_events WHERE work_item_id = :id"),
            {"id": body["id"]},
        )
    ).scalar_one()
    assert actor == users["member"].id


async def test_create_requires_an_idempotency_key(client: AsyncClient, db: AsyncSession) -> None:
    """Without a key there is no way to tell a retry from a second item, so the API
    refuses to guess."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    response = await client.post(
        ITEMS_URL.format(team_id=team.id), json=VALID_BODY, headers=auth_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"
    assert await count_items(db) == 0


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


async def test_retrying_a_create_with_the_same_key_creates_one_item(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    key = str(uuid.uuid4())

    first = await api_create_item(client, token=token, team_id=team.id, key=key, **VALID_BODY)
    second = await api_create_item(client, token=token, team_id=team.id, key=key, **VALID_BODY)

    assert first.status_code == 201
    # A replay is clearly distinguishable: a different status, the same resource, and
    # an explicit header saying so.
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert await count_items(db) == 1
    # Crucially, the retry did not append a second history entry either.
    assert await activity_types(db, uuid.UUID(first.json()["id"])) == ["created"]


async def test_reusing_a_key_with_a_different_body_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Returning the first item here would be silently wrong."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    key = str(uuid.uuid4())

    first = await api_create_item(client, token=token, team_id=team.id, key=key, **VALID_BODY)
    assert first.status_code == 201

    different = await api_create_item(
        client,
        token=token,
        team_id=team.id,
        key=key,
        title="A completely different title",
        type="incident",
    )

    assert different.status_code == 409
    assert different.json()["error"]["code"] == "idempotency_key_reuse"
    assert await count_items(db) == 1


async def test_a_field_reordered_body_is_the_same_request(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The fingerprint is canonicalised, so JSON key order does not matter."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    key = str(uuid.uuid4())

    first = await client.post(
        ITEMS_URL.format(team_id=team.id),
        json={"title": "Same body", "type": "task", "priority": "high"},
        headers=idempotency_headers(token, key),
    )
    second = await client.post(
        ITEMS_URL.format(team_id=team.id),
        json={"priority": "high", "type": "task", "title": "Same body"},
        headers=idempotency_headers(token, key),
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert await count_items(db) == 1


async def test_the_same_key_from_a_different_user_is_a_different_operation(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Keys are scoped to the actor, so two people cannot collide by accident."""
    team, users = await scenario(
        db, roles={"member": Role.MEMBER, "other": Role.MEMBER}, team_key="alpha"
    )
    first_token = await login(client, users["member"].email)
    second_token = await login(client, users["other"].email)
    key = "the-same-shared-key"

    first = await api_create_item(client, token=first_token, team_id=team.id, key=key, **VALID_BODY)
    second = await api_create_item(
        client, token=second_token, team_id=team.id, key=key, **VALID_BODY
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert await count_items(db) == 2


# --------------------------------------------------------------------------- #
# Authorization and validation
# --------------------------------------------------------------------------- #


async def test_viewer_cannot_create(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"viewer": Role.VIEWER}, team_key="alpha")
    token = await login(client, users["viewer"].email)

    response = await api_create_item(client, token=token, team_id=team.id, **VALID_BODY)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert await count_items(db) == 0


async def test_creating_in_another_teams_path_is_not_found(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The team id comes from the path, so the membership check is the boundary."""
    alpha, _ = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    _beta, beta_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="beta")
    token = await login(client, beta_users["member"].email)

    response = await api_create_item(client, token=token, team_id=alpha.id, **VALID_BODY)

    assert response.status_code == 404
    assert await count_items(db, team_id=alpha.id) == 0


async def test_a_member_may_assign_an_item_to_themselves(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    body = await create_item_via_api(
        client,
        token=token,
        team_id=team.id,
        assignee_id=str(users["member"].id),
        **VALID_BODY,
    )

    assert body["assignee"]["id"] == str(users["member"].id)
    # Taking the work yourself is a claim, and it is recorded as one.
    assert await activity_types(db, uuid.UUID(body["id"])) == ["created", "claimed"]


async def test_a_member_cannot_assign_an_item_to_somebody_else(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Handing work to a colleague is an assignment, which a member does not have."""
    team, users = await scenario(
        db, roles={"member": Role.MEMBER, "other": Role.MEMBER}, team_key="alpha"
    )
    token = await login(client, users["member"].email)

    response = await api_create_item(
        client,
        token=token,
        team_id=team.id,
        assignee_id=str(users["other"].id),
        **VALID_BODY,
    )

    assert response.status_code == 403
    assert await count_items(db) == 0


async def test_a_lead_may_assign_an_item_to_a_member(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(
        db, roles={"lead": Role.LEAD, "member": Role.MEMBER}, team_key="alpha"
    )
    token = await login(client, users["lead"].email)

    body = await create_item_via_api(
        client,
        token=token,
        team_id=team.id,
        assignee_id=str(users["member"].id),
        **VALID_BODY,
    )

    assert body["assignee"]["email"] == users["member"].email
    assert await activity_types(db, uuid.UUID(body["id"])) == ["created", "assigned"]


async def test_assigning_to_a_non_member_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    """Refused with a clear 422 rather than a raw foreign-key violation.

    The composite foreign key `work_items(team_id, assignee_id) -> memberships` would
    stop this anyway; this test is about the quality of the answer.
    """
    team, users = await scenario(db, roles={"lead": Role.LEAD}, team_key="alpha")
    _outsider_team, outsider_users = await scenario(
        db, roles={"outsider": Role.MEMBER}, team_key="beta"
    )
    token = await login(client, users["lead"].email)

    response = await api_create_item(
        client,
        token=token,
        team_id=team.id,
        assignee_id=str(outsider_users["outsider"].id),
        **VALID_BODY,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_assignee"
    assert await count_items(db) == 0


async def test_server_controlled_fields_cannot_be_set(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`status`, `version` and `created_by` are not the caller's to choose."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    base = dict(VALID_BODY)
    base["status"] = "closed"
    response = await client.post(
        ITEMS_URL.format(team_id=team.id),
        json=base,
        headers=idempotency_headers(token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"

    for field, value in (
        ("version", 99),
        ("created_by", str(uuid.uuid4())),
        ("team_id", str(uuid.uuid4())),
    ):
        response = await client.post(
            ITEMS_URL.format(team_id=team.id),
            json=dict(VALID_BODY) | {field: value},
            headers=idempotency_headers(token),
        )
        assert response.status_code == 422, field

    assert await count_items(db) == 0


async def test_a_short_title_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    response = await api_create_item(client, token=token, team_id=team.id, title="ab", type="task")

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "title"
    assert await count_items(db) == 0


async def test_creating_requires_authentication(client: AsyncClient, db: AsyncSession) -> None:
    team, _ = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")

    response = await client.post(
        ITEMS_URL.format(team_id=team.id),
        json=VALID_BODY,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 401
    assert await count_items(db) == 0


async def test_created_item_is_open_and_unassigned_in_the_database(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The API response is not the evidence; the stored row is."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    body = await create_item_via_api(client, token=token, team_id=team.id, **VALID_BODY)
    stored = await raw_item(db, uuid.UUID(body["id"]))

    assert stored["status"] == Status.OPEN.value
    assert stored["assignee_id"] is None
    assert stored["version"] == 1
