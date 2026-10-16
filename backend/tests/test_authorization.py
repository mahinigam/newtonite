"""Authorization: team boundaries, the role matrix, and the invariants around roles.

The recurring theme is that the server decides. Every test below states what a
caller may and may not do *from their stored membership*, and several of them try to
talk the API into something else — a foreign team id, an unknown role, an extra field
in the body — and check that nothing changes.

Two status codes carry meaning and are asserted explicitly:

* **404** when the caller is not a member of the team, so the API never confirms
  that another team's data exists;
* **403** when the caller *is* in the team and their role is the obstacle.
"""

from __future__ import annotations

import uuid

from app.models.enums import Priority, Role, Status
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    add_member,
    auth_headers,
    create_team,
    create_user,
    create_work_item,
    login,
    scenario,
)

TEAMS = "/api/v1/teams"
DIRECTORY = "/api/v1/users/directory"


def members_path(team_id: uuid.UUID | str) -> str:
    return f"{TEAMS}/{team_id}/members"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


async def test_team_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get(TEAMS)).status_code == 401
    assert (await client.get(members_path(uuid.uuid4()))).status_code == 401
    assert (await client.get(DIRECTORY)).status_code == 401


async def test_teams_lists_only_the_callers_teams(client: AsyncClient, db: AsyncSession) -> None:
    alpha, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    await create_team(db, key="secret")
    await db.commit()

    token = await login(client, users["member"].email)
    response = await client.get(TEAMS, headers=auth_headers(token))

    assert response.status_code == 200
    body = response.json()
    assert [team["team_key"] for team in body] == ["alpha"]
    assert body[0]["role"] == "member"
    assert body[0]["team_id"] == str(alpha.id)


async def test_non_member_is_told_not_found_rather_than_forbidden(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A team the caller is not in must be indistinguishable from one that does not
    exist, so the API does not leak the existence of other teams' data."""
    alpha, users = await scenario(db, roles={"member": Role.MEMBER}, team_key="alpha")
    beta, beta_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="beta")
    await db.commit()

    outsider = await login(client, beta_users["member"].email)

    existing_but_foreign = await client.get(members_path(alpha.id), headers=auth_headers(outsider))
    nonexistent = await client.get(members_path(uuid.uuid4()), headers=auth_headers(outsider))

    assert existing_but_foreign.status_code == 404
    assert nonexistent.status_code == 404
    assert existing_but_foreign.json() == nonexistent.json()
    assert existing_but_foreign.json()["error"]["code"] == "not_found"
    assert users["member"].email not in existing_but_foreign.text
    assert beta.key not in existing_but_foreign.text


async def test_viewer_can_read_the_roster(client: AsyncClient, db: AsyncSession) -> None:
    alpha, users = await scenario(
        db, roles={"viewer": Role.VIEWER, "lead": Role.LEAD}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["viewer"].email)
    response = await client.get(members_path(alpha.id), headers=auth_headers(token))

    assert response.status_code == 200
    roster = {member["email"]: member["role"] for member in response.json()}
    assert roster == {
        users["viewer"].email: "viewer",
        users["lead"].email: "lead",
    }


async def test_directory_shows_only_people_who_share_a_team(
    client: AsyncClient, db: AsyncSession
) -> None:
    _alpha, users = await scenario(
        db, roles={"member": Role.MEMBER, "lead": Role.LEAD}, team_key="alpha"
    )
    beta, beta_users = await scenario(db, roles={"member": Role.MEMBER}, team_key="beta")
    await db.commit()

    token = await login(client, users["member"].email)
    response = await client.get(DIRECTORY, headers=auth_headers(token))

    assert response.status_code == 200
    emails = {user["email"] for user in response.json()}
    assert emails == {users["member"].email, users["lead"].email}
    # Somebody in a team the caller has nothing to do with is invisible.
    assert beta_users["member"].email not in emails
    assert beta.key not in response.text


async def test_directory_filters_in_the_database(client: AsyncClient, db: AsyncSession) -> None:
    _, users = await scenario(
        db, roles={"member": Role.MEMBER, "lead": Role.LEAD}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["member"].email)

    by_name = await client.get(DIRECTORY, params={"query": "Lead"}, headers=auth_headers(token))
    by_email = await client.get(
        DIRECTORY, params={"query": users["lead"].email}, headers=auth_headers(token)
    )
    no_match = await client.get(DIRECTORY, params={"query": "nobody"}, headers=auth_headers(token))

    assert [u["email"] for u in by_name.json()] == [users["lead"].email]
    assert [u["email"] for u in by_email.json()] == [users["lead"].email]
    assert no_match.json() == []


# --------------------------------------------------------------------------- #
# Role matrix on writes
# --------------------------------------------------------------------------- #


async def test_viewer_cannot_change_a_role(client: AsyncClient, db: AsyncSession) -> None:
    """Hiding a control in the UI is not authorization. The server refuses it."""
    alpha, users = await scenario(
        db,
        roles={"viewer": Role.VIEWER, "member": Role.MEMBER, "lead": Role.LEAD},
        team_key="alpha",
    )
    await db.commit()

    token = await login(client, users["viewer"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['member'].id}",
        json={"role": "lead"},
        headers=auth_headers(token),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert await _role_of(db, alpha.id, users["member"].id) == "member"


async def test_member_cannot_change_a_role(client: AsyncClient, db: AsyncSession) -> None:
    alpha, users = await scenario(
        db, roles={"member": Role.MEMBER, "lead": Role.LEAD}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["member"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['member'].id}",
        json={"role": "lead"},
        headers=auth_headers(token),
    )

    # A member trying to promote themselves is the case that matters most.
    assert response.status_code == 403
    assert await _role_of(db, alpha.id, users["member"].id) == "member"


async def test_lead_cannot_manage_members(client: AsyncClient, db: AsyncSession) -> None:
    """A lead runs the work; an admin runs the team."""
    alpha, users = await scenario(
        db, roles={"lead": Role.LEAD, "member": Role.MEMBER}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["lead"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['member'].id}",
        json={"role": "lead"},
        headers=auth_headers(token),
    )

    assert response.status_code == 403


async def test_admin_can_change_a_role(client: AsyncClient, db: AsyncSession) -> None:
    alpha, users = await scenario(
        db,
        roles={"admin": Role.ADMIN, "member": Role.MEMBER, "lead": Role.LEAD},
        team_key="alpha",
    )
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['member'].id}",
        json={"role": "viewer"},
        headers=auth_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "viewer"
    assert await _role_of(db, alpha.id, users["member"].id) == "viewer"


async def test_admin_cannot_manage_another_teams_members(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Being an admin is per team. It does not travel."""
    alpha, _ = await scenario(db, roles={"admin": Role.ADMIN}, team_key="alpha")
    beta, beta_users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="beta"
    )
    await db.commit()

    alpha_admin = await login(client, (await _user_email(db, alpha.id, Role.ADMIN)))

    response = await client.put(
        f"{members_path(beta.id)}/{beta_users['member'].id}",
        json={"role": "lead"},
        headers=auth_headers(alpha_admin),
    )

    assert response.status_code == 404
    assert await _role_of(db, beta.id, beta_users["member"].id) == "member"


async def test_admin_can_add_a_colleague_from_their_own_team(
    client: AsyncClient, db: AsyncSession
) -> None:
    """An admin may grant a role to somebody they already share a team with.

    This is what makes a new team reachable at all: pick people out of the directory
    and give them a role.
    """
    _alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="alpha"
    )
    beta = await create_team(db, key="beta")
    await add_member(db, team=beta, user=users["admin"], role=Role.ADMIN)
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.put(
        f"{members_path(beta.id)}/{users['member'].id}",
        json={"role": "lead"},
        headers=auth_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "lead"
    assert await _role_of(db, beta.id, users["member"].id) == "lead"


async def test_admin_cannot_grant_a_role_to_a_stranger(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Somebody with no team in common is not even confirmed to exist."""
    alpha, users = await scenario(db, roles={"admin": Role.ADMIN}, team_key="alpha")
    stranger = await create_user(db, email="stranger@newtonite.dev")
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{stranger.id}",
        json={"role": "member"},
        headers=auth_headers(token),
    )

    assert response.status_code == 404
    assert await _role_of(db, alpha.id, stranger.id) is None


async def test_unknown_role_value_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['member'].id}",
        json={"role": "superuser"},
        headers=auth_headers(token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert await _role_of(db, alpha.id, users["member"].id) == "member"


async def test_unknown_fields_in_the_body_are_rejected(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`extra="forbid"` means a client cannot smuggle in a server-controlled field and
    be left believing it took effect."""
    alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['member'].id}",
        json={"role": "lead", "team_id": str(uuid.uuid4())},
        headers=auth_headers(token),
    )

    assert response.status_code == 422
    assert await _role_of(db, alpha.id, users["member"].id) == "member"


# --------------------------------------------------------------------------- #
# Invariants that protect the team itself
# --------------------------------------------------------------------------- #


async def test_last_admin_cannot_be_demoted(client: AsyncClient, db: AsyncSession) -> None:
    """Otherwise a team could be locked out of every administrative action, with no
    way back in through the API."""
    alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['admin'].id}",
        json={"role": "member"},
        headers=auth_headers(token),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_admin"
    assert await _role_of(db, alpha.id, users["admin"].id) == "admin"


async def test_last_admin_cannot_be_removed(client: AsyncClient, db: AsyncSession) -> None:
    alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.delete(
        f"{members_path(alpha.id)}/{users['admin'].id}", headers=auth_headers(token)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_admin"


async def test_admin_can_be_demoted_when_another_admin_remains(
    client: AsyncClient, db: AsyncSession
) -> None:
    alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "second": Role.ADMIN}, team_key="alpha"
    )
    await db.commit()

    token = await login(client, users["admin"].email)
    response = await client.put(
        f"{members_path(alpha.id)}/{users['admin'].id}",
        json={"role": "member"},
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert await _role_of(db, alpha.id, users["admin"].id) == "member"


async def test_member_with_assigned_work_cannot_be_removed(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The composite foreign key would refuse this anyway; the API turns a raw
    integrity error into an instruction the caller can act on."""
    alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="alpha"
    )
    item = await create_work_item(
        db,
        team=alpha,
        created_by=users["admin"],
        assignee=users["member"],
        status=Status.IN_PROGRESS,
        priority=Priority.HIGH,
    )
    await db.commit()

    token = await login(client, users["admin"].email)
    blocked = await client.delete(
        f"{members_path(alpha.id)}/{users['member'].id}", headers=auth_headers(token)
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "member_has_assigned_items"
    # Nothing changed, and the membership is still there.
    assert await _role_of(db, alpha.id, users["member"].id) == "member"
    assert (await db.execute(text("SELECT count(*) FROM memberships"))).scalar_one() == 2

    # Reassign the work, and the removal becomes possible.
    await db.execute(
        text("UPDATE work_items SET assignee_id = :admin WHERE id = :item"),
        {"admin": users["admin"].id, "item": item.id},
    )
    await db.commit()

    allowed = await client.delete(
        f"{members_path(alpha.id)}/{users['member'].id}", headers=auth_headers(token)
    )

    assert allowed.status_code == 204
    assert allowed.content == b""
    assert await _role_of(db, alpha.id, users["member"].id) is None


async def test_removing_a_member_does_not_touch_their_recorded_history(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Removing a membership must not delete the record of what they did."""
    alpha, users = await scenario(
        db, roles={"admin": Role.ADMIN, "member": Role.MEMBER}, team_key="alpha"
    )
    await db.execute(
        text(
            "WITH created AS ("
            "  INSERT INTO work_items (team_id, title, type, priority, created_by)"
            "  VALUES (:team, 'An item they made', 'task', 'low', :member)"
            "  RETURNING id, team_id, created_by"
            ")"
            "INSERT INTO activity_events (work_item_id, team_id, actor_id, event_type)"
            "SELECT id, team_id, created_by, 'created' FROM created"
        ),
        {"team": alpha.id, "member": users["member"].id},
    )
    await db.commit()
    events_before = (await db.execute(text("SELECT count(*) FROM activity_events"))).scalar_one()

    token = await login(client, users["admin"].email)
    response = await client.delete(
        f"{members_path(alpha.id)}/{users['member'].id}", headers=auth_headers(token)
    )

    assert response.status_code == 204
    events_after = (await db.execute(text("SELECT count(*) FROM activity_events"))).scalar_one()
    assert events_after == events_before == 1


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _role_of(db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    return (
        await db.execute(
            text("SELECT role FROM memberships WHERE team_id = :t AND user_id = :u"),
            {"t": team_id, "u": user_id},
        )
    ).scalar_one_or_none()


async def _user_email(db: AsyncSession, team_id: uuid.UUID, role: Role) -> str:
    email = (
        await db.execute(
            text(
                "SELECT u.email FROM users u JOIN memberships m ON m.user_id = u.id "
                "WHERE m.team_id = :t AND m.role = :r"
            ),
            {"t": team_id, "r": role.value},
        )
    ).scalar_one()
    return str(email)
