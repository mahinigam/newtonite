"""Editing a work item, and refusing to edit a stale one.

The centrepiece is `test_a_stale_edit_is_rejected_and_changes_nothing`. The rest is the
field-by-field behaviour, the permission split between editing and assigning, and the
guarantee that a request which changes nothing is not recorded as a change.
"""

from __future__ import annotations

import asyncio
import uuid

from app.models.enums import Role
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    activity_events,
    activity_types,
    api_patch,
    claim_ok,
    create_item_via_api,
    login,
    raw_item,
    read_item,
    scenario,
)


async def test_a_stale_edit_is_rejected_and_changes_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Two writers, one version. The second must lose, visibly and harmlessly.

    A real client would have fetched version 1. Meanwhile somebody else edited the item,
    making it version 2. The first client's request still says `If-Match: "1"`, and it is
    refused rather than overwriting the newer title.
    """
    team, users = await scenario(
        db,
        roles={"one": Role.MEMBER, "two": Role.MEMBER},
        team_key="alpha",
    )
    first_token = await login(client, users["one"].email)
    second_token = await login(client, users["two"].email)
    item = await create_item_via_api(client, token=first_token, team_id=team.id, title="Original")
    item_id = item["id"]

    # Both readers see version 1.
    assert item["version"] == 1
    assert (await read_item(client, token=first_token, item_id=item_id)).headers["etag"] == '"1"'

    # The second writer edits with a current precondition and wins.
    won = await api_patch(
        client, token=second_token, item_id=item_id, etag='"1"', title="Changed by the other"
    )
    assert won.status_code == 200, won.text
    assert won.json()["version"] == 2

    # The first writer now edits from the stale copy it still holds.
    lost = await api_patch(
        client, token=first_token, item_id=item_id, etag='"1"', title="Changed by me"
    )

    assert lost.status_code == 412
    error = lost.json()["error"]
    assert error["code"] == "stale_version"
    # Enough information to reconcile without guessing at the new version.
    assert error["details"]["current"]["version"] == 2
    assert error["details"]["current"]["id"] == item_id

    # The decisive assertion: the newer value survived and no history was written for
    # the refused attempt.
    assert (await raw_item(db, uuid.UUID(item_id)))["title"] == "Changed by the other"
    assert await activity_types(db, uuid.UUID(item_id)) == ["created", "title_changed"]


async def test_an_edit_without_a_precondition_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """No `If-Match` means the API cannot tell whether the caller has seen the item."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Unguarded")

    response = await api_patch(client, token=token, item_id=item["id"], title="Changed")

    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"
    assert (await raw_item(db, uuid.UUID(item["id"])))["title"] == "Unguarded"


async def test_a_malformed_precondition_is_a_validation_error(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Unguarded")

    for value in ("banana", '"nope"', "0", "-3"):
        response = await api_patch(
            client, token=token, item_id=item["id"], etag=value, title="Changed"
        )
        assert response.status_code == 422, value
        assert response.json()["error"]["code"] == "validation_failed"


async def test_an_edit_records_what_changed(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client, token=token, team_id=team.id, title="Before", priority="low"
    )

    response = await api_patch(
        client,
        token=token,
        item_id=item["id"],
        etag='"1"',
        priority="urgent",
        description="Investigated, it is the connection pool.",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["priority"] == "urgent"
    assert body["version"] == 2
    events = await activity_events(db, uuid.UUID(item["id"]))
    # One event per changed field, in a fixed order: the declaration order of the
    # editable fields, so the same request always produces the same timeline.
    assert [event["event_type"] for event in events] == [
        "created",
        "description_changed",
        "priority_changed",
    ]
    priority_event = events[2]
    assert (priority_event["from_value"], priority_event["to_value"]) == ("low", "urgent")


async def test_every_changed_field_is_one_event_and_one_version_bump(
    client: AsyncClient, db: AsyncSession
) -> None:
    """History is per change, the version is per request."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client, token=token, team_id=team.id, title="Before", type="task"
    )

    response = await api_patch(
        client,
        token=token,
        item_id=item["id"],
        etag='"1"',
        title="After",
        type="incident",
        priority="high",
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert await activity_types(db, uuid.UUID(item["id"])) == [
        "created",
        "title_changed",
        "type_changed",
        "priority_changed",
    ]


async def test_an_edit_that_changes_nothing_is_not_an_event(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Sending the same value back is common in a form-driven UI, and is not history."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client, token=token, team_id=team.id, title="Unchanged", priority="medium"
    )

    response = await api_patch(
        client, token=token, item_id=item["id"], etag='"1"', title="Unchanged", priority="medium"
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created"]


async def test_an_explicit_null_clears_a_field(client: AsyncClient, db: AsyncSession) -> None:
    """Absent means "no opinion"; `null` means "make it empty". They differ."""
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client,
        token=token,
        team_id=team.id,
        title="Has a due date",
        due_at="2026-11-01T09:00:00Z",
    )
    assert item["due_at"] is not None

    response = await api_patch(client, token=token, item_id=item["id"], etag='"1"', due_at=None)

    assert response.status_code == 200, response.text
    assert response.json()["due_at"] is None
    events = await activity_events(db, uuid.UUID(item["id"]))
    assert events[-1]["event_type"] == "due_date_changed"
    assert events[-1]["to_value"] is None


async def test_a_member_may_take_an_unowned_item_but_not_a_colleagues(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Self-assignment is a claim; taking work off somebody else is not."""
    team, users = await scenario(
        db, roles={"one": Role.MEMBER, "two": Role.MEMBER}, team_key="alpha"
    )
    first_token = await login(client, users["one"].email)
    second_token = await login(client, users["two"].email)
    item = await create_item_via_api(client, token=first_token, team_id=team.id, title="Work")

    # Unassigned: taking it is a claim, which a member may do.
    taken = await api_patch(
        client,
        token=second_token,
        item_id=item["id"],
        etag='"1"',
        assignee_id=str(users["two"].id),
    )
    assert taken.status_code == 200, taken.text
    assert taken.json()["assignee"]["id"] == str(users["two"].id)
    assert await activity_types(db, uuid.UUID(item["id"])) == ["created", "claimed"]

    # Now it belongs to the second member, so the first cannot simply take it back.
    stolen = await api_patch(
        client,
        token=first_token,
        item_id=item["id"],
        etag='"2"',
        assignee_id=str(users["one"].id),
    )
    assert stolen.status_code == 403
    assert stolen.json()["error"]["code"] == "forbidden"
    stored = await raw_item(db, uuid.UUID(item["id"]))
    assert stored["assignee_id"] == users["two"].id
    assert stored["version"] == 2


async def test_a_member_may_release_their_own_item(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Mine")
    claimed = await claim_ok(client, token=token, item_id=item["id"])

    response = await api_patch(
        client, token=token, item_id=item["id"], etag='"2"', assignee_id=None
    )

    assert response.status_code == 200, response.text
    assert response.json()["assignee"] is None
    assert claimed["version"] == 2
    assert await activity_types(db, uuid.UUID(item["id"])) == [
        "created",
        "claimed",
        "unassigned",
    ]


async def test_a_lead_may_reassign_to_somebody_else(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(
        db, roles={"lead": Role.LEAD, "member": Role.MEMBER}, team_key="alpha"
    )
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=lead_token, team_id=team.id, title="Hand over")

    response = await api_patch(
        client,
        token=lead_token,
        item_id=item["id"],
        etag='"1"',
        assignee_id=str(users["member"].id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["assignee"]["email"] == users["member"].email
    events = await activity_events(db, uuid.UUID(item["id"]))
    assert events[-1]["event_type"] == "assigned"
    # The stored id is authoritative; the name is a convenience for rendering.
    assert events[-1]["to_value"] == str(users["member"].id)
    assert events[-1]["meta"]["assignee_name"] == users["member"].display_name

    # The assignee can now see the item as theirs in the "my work" filter.
    assert (await read_item(client, token=member_token, item_id=item["id"])).status_code == 200


async def test_a_viewer_cannot_edit(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await scenario(
        db, roles={"member": Role.MEMBER, "viewer": Role.VIEWER}, team_key="alpha"
    )
    member_token = await login(client, users["member"].email)
    viewer_token = await login(client, users["viewer"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Read only")

    response = await api_patch(
        client, token=viewer_token, item_id=item["id"], etag='"1"', title="Changed"
    )

    assert response.status_code == 403
    assert (await raw_item(db, uuid.UUID(item["id"])))["title"] == "Read only"


async def test_editing_another_teams_item_is_not_found(
    client: AsyncClient, db: AsyncSession
) -> None:
    alpha, alpha_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    _beta, beta_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="beta")
    alpha_token = await login(client, alpha_users["member"].email)
    beta_token = await login(client, beta_users["member"].email)
    item = await create_item_via_api(
        client, token=alpha_token, team_id=alpha.id, title="Alpha only"
    )

    response = await api_patch(
        client, token=beta_token, item_id=item["id"], etag='"1"', title="Interloper"
    )

    assert response.status_code == 404
    assert (await raw_item(db, uuid.UUID(item["id"])))["title"] == "Alpha only"


async def test_server_controlled_fields_cannot_be_patched(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=token, team_id=team.id, title="Guarded")

    for field, value in (
        ("status", "closed"),
        ("version", 99),
        ("team_id", str(uuid.uuid4())),
        ("created_by", str(uuid.uuid4())),
    ):
        response = await api_patch(
            client, token=token, item_id=item["id"], etag='"1"', **{field: value}
        )
        assert response.status_code == 422, field
        assert response.json()["error"]["code"] == "validation_failed"

    stored = await raw_item(db, uuid.UUID(item["id"]))
    assert stored["status"] == "open"
    assert stored["version"] == 1


async def test_an_edit_to_a_nonexistent_item_is_not_found(
    client: AsyncClient, db: AsyncSession
) -> None:
    _team, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    token = await login(client, users["member"].email)

    response = await api_patch(
        client, token=token, item_id=str(uuid.uuid4()), etag='"1"', title="Nowhere"
    )

    assert response.status_code == 404


async def test_a_lost_race_between_two_editors_leaves_exactly_one_change(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Concurrent editors of one version: exactly one wins, the other is told so.

    Both requests carry `If-Match: "1"` and are issued together. The precondition is
    evaluated inside the `UPDATE`, so the database — not timing — decides.
    """
    team, users = await scenario(
        db, roles={"one": Role.MEMBER, "two": Role.MEMBER}, team_key="alpha"
    )
    first_token = await login(client, users["one"].email)
    second_token = await login(client, users["two"].email)
    item = await create_item_via_api(client, token=first_token, team_id=team.id, title="Original")
    item_id = item["id"]

    first, second = await asyncio.gather(
        api_patch(client, token=first_token, item_id=item_id, etag='"1"', title="From one"),
        api_patch(client, token=second_token, item_id=item_id, etag='"1"', title="From two"),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 412], (
        first.text,
        second.text,
    )
    stored = await raw_item(db, uuid.UUID(item_id))
    assert stored["title"] in {"From one", "From two"}
    assert stored["version"] == 2
    # One change, one history entry. The loser wrote nothing at all.
    assert await activity_types(db, uuid.UUID(item_id)) == ["created", "title_changed"]
