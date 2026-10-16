"""The workflow: legal moves, illegal moves, and who is allowed to make them.

There is no endpoint that writes `status` directly, so every one of these tests goes
through the same door a client must use. The state machine is asserted as a whole — the
happy path, each refusal, and the *reason* for each refusal — because "the workflow is
enforced" is only meaningful if the refusals are specific.
"""

from __future__ import annotations

import uuid

from app.models.enums import Role
from app.models.team import Team
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    activity_events,
    activity_types,
    api_transition,
    claim_ok,
    create_item_via_api,
    login,
    move_to,
    raw_item,
    read_item,
    scenario,
)


async def _team_with_lead_and_member(
    db: AsyncSession, *, team_key: str = "alpha"
) -> tuple[Team, dict[str, User]]:
    return await scenario(
        db,
        roles={"lead": Role.LEAD, "member": Role.MEMBER, "viewer": Role.VIEWER},
        team_key=team_key,
    )


async def test_the_whole_journey_from_open_to_closed(client: AsyncClient, db: AsyncSession) -> None:
    """Claim, start, submit, approve, close — each step by the right role.

    This is the vertical slice of the workflow: if it holds, the state machine is
    wired to the API correctly. The history is asserted event by event, including who
    did each thing, because an audit trail that loses the actor is not an audit trail.
    """
    team, users = await _team_with_lead_and_member(db)
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)

    item = await create_item_via_api(
        client, token=member_token, team_id=team.id, title="A job that runs the gauntlet"
    )
    item_id = item["id"]
    assert item["status"] == "open"

    await claim_ok(client, token=member_token, item_id=item_id)
    started = await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    assert started["status"] == "in_progress"

    submitted = await move_to(
        client, token=member_token, item_id=item_id, target="awaiting_approval"
    )
    assert submitted["status"] == "awaiting_approval"

    approved = await move_to(client, token=lead_token, item_id=item_id, target="resolved")
    assert approved["status"] == "resolved"

    closed = await move_to(client, token=lead_token, item_id=item_id, target="closed")
    assert closed["status"] == "closed"
    assert closed["version"] == 6
    # Nothing left to do, and the server says so rather than the client guessing.
    assert closed["available_transitions"] == []

    events = await activity_events(db, uuid.UUID(item_id))
    assert [event["event_type"] for event in events] == [
        "created",
        "claimed",
        "status_changed",
        "status_changed",
        "approved",
        "status_changed",
    ]
    assert [(event["from_value"], event["to_value"]) for event in events[2:]] == [
        ("open", "in_progress"),
        ("in_progress", "awaiting_approval"),
        ("awaiting_approval", "resolved"),
        ("resolved", "closed"),
    ]
    assert events[4]["actor_id"] == users["lead"].id, "approval is attributed to the lead"
    assert events[2]["actor_id"] == users["member"].id


async def test_approval_is_a_lead_action(client: AsyncClient, db: AsyncSession) -> None:
    """A member may submit work for approval but may not grant it."""
    team, users = await _team_with_lead_and_member(db)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client, token=member_token, team_id=team.id, title="Needs a nod"
    )
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    await move_to(client, token=member_token, item_id=item_id, target="awaiting_approval")

    response = await api_transition(
        client, token=member_token, item_id=item_id, target="resolved", etag='"4"'
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert (await raw_item(db, uuid.UUID(item_id)))["status"] == "awaiting_approval"


async def test_closing_is_a_lead_action(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await _team_with_lead_and_member(db)
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client, token=member_token, team_id=team.id, title="Almost done"
    )
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    await move_to(client, token=member_token, item_id=item_id, target="awaiting_approval")
    await move_to(client, token=lead_token, item_id=item_id, target="resolved")

    response = await api_transition(
        client, token=member_token, item_id=item_id, target="closed", etag='"5"'
    )

    assert response.status_code == 403
    assert (await raw_item(db, uuid.UUID(item_id)))["status"] == "resolved"


async def test_a_rejection_sends_the_item_back_with_the_reason(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Rejection is a first-class outcome, not a delete and not a silent rewind."""
    team, users = await _team_with_lead_and_member(db)
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Not right")
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    await move_to(client, token=member_token, item_id=item_id, target="awaiting_approval")

    rejected = await move_to(
        client,
        token=lead_token,
        item_id=item_id,
        target="in_progress",
        note="The customer was never contacted.",
    )

    assert rejected["status"] == "in_progress"
    events = await activity_events(db, uuid.UUID(item_id))
    assert events[-1]["event_type"] == "rejected"
    assert events[-1]["meta"]["note"] == "The customer was never contacted."
    # The assignee still owns it: a rejection returns the work, it does not orphan it.
    assert rejected["assignee"]["email"] == users["member"].email


async def test_rejecting_is_a_lead_action(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await _team_with_lead_and_member(db)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Waiting")
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    await move_to(client, token=member_token, item_id=item_id, target="awaiting_approval")

    response = await api_transition(
        client, token=member_token, item_id=item_id, target="in_progress", etag='"4"'
    )

    assert response.status_code == 403


async def test_an_illegal_transition_names_the_ones_that_are_reachable(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A refusal a client can act on, instead of a bare 409.

    Skipping approval is refused, and the response says what *is* possible so the
    interface can correct itself rather than guess.
    """
    team, users = await _team_with_lead_and_member(db)
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client, token=member_token, team_id=team.id, title="Jumping ahead"
    )
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)

    # open -> resolved is not a transition, and the item is already owned so the only
    # legal move is to start it.
    response = await api_transition(
        client, token=lead_token, item_id=item_id, target="resolved", etag='"2"'
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["current_status"] == "open"
    assert error["details"]["available_transitions"] == ["in_progress"]
    assert (await raw_item(db, uuid.UUID(item_id)))["status"] == "open"


async def test_closed_is_terminal(client: AsyncClient, db: AsyncSession) -> None:
    """Reopening is deliberately not implemented, and the API says so by refusing."""
    team, users = await _team_with_lead_and_member(db)
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Done")
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    await move_to(client, token=member_token, item_id=item_id, target="awaiting_approval")
    await move_to(client, token=lead_token, item_id=item_id, target="resolved")
    closed = await move_to(client, token=lead_token, item_id=item_id, target="closed")

    for target in ("open", "in_progress", "awaiting_approval", "resolved"):
        response = await api_transition(
            client, token=lead_token, item_id=item_id, target=target, etag='"6"'
        )
        assert response.status_code == 409, target
        assert response.json()["error"]["code"] == "invalid_transition"
        assert response.json()["error"]["details"]["available_transitions"] == []

    assert closed["status"] == "closed"
    assert await activity_types(db, uuid.UUID(item_id)) == [
        "created",
        "claimed",
        "status_changed",
        "status_changed",
        "approved",
        "status_changed",
    ]


async def test_a_transition_without_a_precondition_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A decision taken from a status that may have moved must not be applied."""
    team, users = await _team_with_lead_and_member(db)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Unguarded")
    await claim_ok(client, token=member_token, item_id=item["id"])

    response = await api_transition(
        client, token=member_token, item_id=item["id"], target="in_progress"
    )

    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"
    assert (await raw_item(db, uuid.UUID(item["id"])))["status"] == "open"


async def test_a_stale_transition_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await _team_with_lead_and_member(db)
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Racy")
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")

    # The lead acts on the copy it read earlier, which is now two versions old.
    response = await api_transition(
        client, token=lead_token, item_id=item_id, target="awaiting_approval", etag='"1"'
    )

    assert response.status_code == 412
    assert response.json()["error"]["code"] == "stale_version"
    assert response.json()["error"]["details"]["current"]["version"] == 3
    assert (await raw_item(db, uuid.UUID(item_id)))["status"] == "in_progress"


async def test_a_viewer_cannot_move_work(client: AsyncClient, db: AsyncSession) -> None:
    """A viewer can read the item but the workflow is closed to them."""
    team, users = await _team_with_lead_and_member(db)
    member_token = await login(client, users["member"].email)
    viewer_token = await login(client, users["viewer"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Hands off")
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    # They can see it...
    assert (await read_item(client, token=viewer_token, item_id=item_id)).status_code == 200

    # ...but not advance it, and they are not even offered the option.
    response = await api_transition(
        client, token=viewer_token, item_id=item_id, target="in_progress", etag='"2"'
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert (await read_item(client, token=viewer_token, item_id=item_id)).json()[
        "available_transitions"
    ] == []


async def test_moving_another_teams_item_is_not_found(
    client: AsyncClient, db: AsyncSession
) -> None:
    alpha, alpha_users = await scenario(db, roles={"lead": Role.LEAD}, team_key="alpha")
    _beta, beta_users = await scenario(db, roles={"lead": Role.LEAD}, team_key="beta")
    alpha_token = await login(client, alpha_users["lead"].email)
    beta_token = await login(client, beta_users["lead"].email)
    item = await create_item_via_api(
        client, token=alpha_token, team_id=alpha.id, title="Alpha only"
    )

    response = await api_transition(
        client, token=beta_token, item_id=item["id"], target="in_progress", etag='"1"'
    )

    assert response.status_code == 404
    assert (await raw_item(db, uuid.UUID(item["id"])))["status"] == "open"


async def test_an_unknown_target_status_is_a_validation_error(
    client: AsyncClient, db: AsyncSession
) -> None:
    team, users = await _team_with_lead_and_member(db)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Nonsense")
    await claim_ok(client, token=member_token, item_id=item["id"])

    response = await api_transition(
        client, token=member_token, item_id=item["id"], target="archived", etag='"2"'
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_a_transition_requires_authentication(client: AsyncClient, db: AsyncSession) -> None:
    team, users = await _team_with_lead_and_member(db)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(client, token=member_token, team_id=team.id, title="Guarded")

    response = await client.post(
        f"/api/v1/items/{item['id']}/transitions",
        json={"to": "in_progress"},
        headers={"If-Match": '"1"'},
    )

    assert response.status_code == 401


async def test_a_member_may_advance_a_colleagues_work(
    client: AsyncClient, db: AsyncSession
) -> None:
    """An intentional rule, asserted so that it is a decision rather than an accident.

    The role matrix gives every member `transition_work` for their team. A member may
    therefore move an item that somebody else owns — which is what makes a small
    operations team able to cover for each other. Changing *ownership* is a different
    permission and is still restricted.
    """
    team, users = await scenario(
        db,
        roles={"one": Role.MEMBER, "two": Role.MEMBER},
        team_key="alpha",
    )
    first_token = await login(client, users["one"].email)
    second_token = await login(client, users["two"].email)
    item = await create_item_via_api(client, token=first_token, team_id=team.id, title="Cover me")
    item_id = item["id"]
    await claim_ok(client, token=first_token, item_id=item_id)

    response = await api_transition(
        client, token=second_token, item_id=item_id, target="in_progress", etag='"2"'
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"
    # Ownership is unchanged: advancing work is not taking it.
    assert response.json()["assignee"]["id"] == str(users["one"].id)


async def test_available_transitions_are_honest_about_the_role(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The same item offers different moves to different people, and the server decides."""
    team, users = await _team_with_lead_and_member(db)
    lead_token = await login(client, users["lead"].email)
    member_token = await login(client, users["member"].email)
    viewer_token = await login(client, users["viewer"].email)
    item = await create_item_via_api(
        client, token=member_token, team_id=team.id, title="Whose move?"
    )
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    await move_to(client, token=member_token, item_id=item_id, target="awaiting_approval")

    member_view = (await read_item(client, token=member_token, item_id=item_id)).json()
    lead_view = (await read_item(client, token=lead_token, item_id=item_id)).json()
    viewer_view = (await read_item(client, token=viewer_token, item_id=item_id)).json()

    # The person who did the work cannot approve it.
    assert member_view["available_transitions"] == []
    # The lead can approve or send it back.
    assert sorted(lead_view["available_transitions"]) == ["in_progress", "resolved"]
    # The viewer is offered nothing, which matches what the server would do.
    assert viewer_view["available_transitions"] == []


async def test_a_second_approver_loses_cleanly(client: AsyncClient, db: AsyncSession) -> None:
    """Two leads approve at once: one succeeds, the other is told the version moved."""
    import asyncio

    team, users = await scenario(
        db,
        roles={"lead": Role.LEAD, "other_lead": Role.LEAD, "member": Role.MEMBER},
        team_key="alpha",
    )
    lead_token = await login(client, users["lead"].email)
    other_token = await login(client, users["other_lead"].email)
    member_token = await login(client, users["member"].email)
    item = await create_item_via_api(
        client, token=member_token, team_id=team.id, title="Double approval"
    )
    item_id = item["id"]
    await claim_ok(client, token=member_token, item_id=item_id)
    await move_to(client, token=member_token, item_id=item_id, target="in_progress")
    await move_to(client, token=member_token, item_id=item_id, target="awaiting_approval")

    first, second = await asyncio.gather(
        api_transition(client, token=lead_token, item_id=item_id, target="resolved", etag='"4"'),
        api_transition(client, token=other_token, item_id=item_id, target="resolved", etag='"4"'),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 412], (
        first.text,
        second.text,
    )
    assert (await raw_item(db, uuid.UUID(item_id)))["status"] == "resolved"
    # Approved once, not twice.
    assert await activity_types(db, uuid.UUID(item_id)) == [
        "created",
        "claimed",
        "status_changed",
        "status_changed",
        "approved",
    ]
