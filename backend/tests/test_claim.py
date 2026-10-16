"""Claiming: atomicity is proven in `test_items_concurrency.py`; this file is the rest.

What is checked here is that claim does exactly and only what it says — assign the item,
bump the version, write one history entry — and that every refusal is the *right* refusal
rather than a generic conflict.
"""

from __future__ import annotations

import uuid

from app.models.enums import Role, Status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    activity_types,
    api_claim,
    api_transition,
    claim_ok,
    create_item_via_api,
    login,
    move_to,
    raw_item,
    read_item,
    scenario,
)


async def test_claiming_assigns_the_item_and_records_it(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(
        db, roles={"member": Role.MEMBER, "other": Role.MEMBER}, team_key="alpha"
    )
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Pick me up")

    response = await api_claim(client, token=token, item_id=item["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assignee"]["email"] == users["member"].email
    assert body["version"] == 2
    assert response.headers["etag"] == '"2"'
    # Claiming does not start the work: that is a separate, explicitly recorded step.
    assert body["status"] == "open"
    # And now that it has an owner, starting it becomes available.
    assert body["available_transitions"] == ["in_progress"]

    stored = await raw_item(db, uuid.UUID(item["id"]))
    assert stored["assignee_id"] == users["member"].id
    assert stored["version"] == 2
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created", "claimed"]


async def test_claiming_your_own_item_is_a_harmless_no_op(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A double-click must not bump the version or duplicate the history entry."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Mine")

    first = await claim_ok(client, token=token, item_id=item["id"])
    second = await claim_ok(client, token=token, item_id=item["id"])

    assert first["version"] == 2
    assert second["version"] == 2, "a no-op must not look like a change"
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created", "claimed"]


async def test_a_second_person_cannot_claim_an_owned_item(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(
        db, roles={"one": Role.MEMBER, "two": Role.MEMBER}, team_key="alpha"
    )
    first_token = await login(client, users["one"].email)
    second_token = await login(client, users["two"].email)
    item = await create_item_via_api(client, token=first_token, team_id=team.id, title="Taken")
    await claim_ok(client, token=first_token, item_id=item["id"])

    response = await api_claim(client, token=second_token, item_id=item["id"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "item_already_claimed"
    stored = await raw_item(db, uuid.UUID(item["id"]))
    assert stored["assignee_id"] == users["one"].id, "a refused claim must change nothing"


async def test_an_item_already_in_progress_cannot_be_claimed(
    client: AsyncClient, db: AsyncSession
) -> None:
    """An unassigned item that is already under way is not simply up for grabs.

    The state is reached through the API — start the work, then have a lead unassign it —
    so this is a real situation, not one manufactured in the database.
    """
    team, users = await scenario(
        db,
        roles={"lead": Role.LEAD, "one": Role.MEMBER, "two": Role.MEMBER},
        team_key="alpha",
    )
    lead_token = await login(client, users["lead"].email)
    first_token = await login(client, users["one"].email)
    second_token = await login(client, users["two"].email)

    item = await create_item_via_api(
        client,
        token=lead_token,
        team_id=team.id,
        title="Started then orphaned",
        assignee_id=str(users["one"].id),
    )
    await move_to(client, token=first_token, item_id=item["id"], target="in_progress")

    current = await read_item(client, token=lead_token, item_id=item["id"])
    unassigned = await client.patch(
        f"/api/v1/items/{item['id']}",
        json={"assignee_id": None},
        headers={
            "Authorization": f"Bearer {lead_token}",
            "If-Match": current.headers["etag"],
        },
    )
    assert unassigned.status_code == 200, unassigned.text
    assert unassigned.json()["assignee"] is None
    assert unassigned.json()["status"] == "in_progress"

    response = await api_claim(client, token=second_token, item_id=item["id"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"
    state = await raw_item(db, uuid.UUID(item["id"]))
    assert state["assignee_id"] is None


async def test_viewer_cannot_claim(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(
        db, roles={"member": Role.MEMBER, "viewer": Role.VIEWER}, team_key="alpha"
    )
    member_token = await login(client, users["member"].email)
    viewer_token = await login(client, users["viewer"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Read only")

    response = await api_claim(client, token=viewer_token, item_id=item["id"])

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    state = await raw_item(db, uuid.UUID(item["id"]))
    assert state["assignee_id"] is None


async def test_claiming_another_teams_item_is_not_found(
    client: AsyncClient, db: AsyncSession
) -> None:
    alpha, alpha_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    _beta, beta_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="beta")
    alpha_token = await login(client, alpha_users["member"].email)
    beta_token = await login(client, beta_users["member"].email)
    item = await create_item_via_api(
        client, token=alpha_token, team_id=alpha.id, title="Alpha only"
    )

    response = await api_claim(client, token=beta_token, item_id=item["id"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    state = await raw_item(db, uuid.UUID(item["id"]))
    assert state["assignee_id"] is None


async def test_claiming_an_unknown_item_is_the_same_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A refused claim on a real item and on an invented id must look identical."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Real")

    unknown = await api_claim(client, token=token, item_id=str(uuid.uuid4()))
    other_teams = await scenario(db, roles={"member": Role.MEMBER}, team_key="beta")
    foreign_token = await login(client, other_teams[1]["member"].email)
    foreign = await api_claim(client, token=foreign_token, item_id=item["id"])

    assert unknown.status_code == foreign.status_code == 404
    assert unknown.json() == foreign.json(), "the two cases must be indistinguishable"


async def test_claiming_requires_authentication(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Guarded")

    response = await client.post(f"/api/v1/items/{item['id']}/claim")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_starting_work_requires_an_owner(client: AsyncClient, db: AsyncSession) -> None:
    """The workflow refuses to start unassigned work, and says why."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Unowned")
    # An unowned item has nothing to start, so the transition is not even offered.
    assert item["available_transitions"] == []

    response = await api_transition(
        client, token=token, item_id=item["id"], target="in_progress", etag='"1"'
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "assignee_required"
    state = await raw_item(db, uuid.UUID(item["id"]))
    assert state["status"] == Status.OPEN.value
