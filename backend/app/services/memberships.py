"""Teams, memberships and the rules that protect them.

Two invariants are enforced here because they are business rules rather than
database constraints:

* **A team always keeps at least one admin.** Otherwise a team could be locked out
  of every administrative action permanently, with no way back in through the API.
* **A member with assigned work cannot simply be removed.** The composite foreign
  key `work_items(team_id, assignee_id) -> memberships(team_id, user_id)` would
  refuse the delete anyway, but as a raw integrity error. Checking first turns that
  into a clear instruction: reassign their work, then remove them.

Neither rule is a substitute for the database constraints; they are the good error
messages in front of them.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import LastAdmin, MemberHasAssignedItems, NotFound
from app.models import Membership, Team, User, WorkItem
from app.models.enums import Role
from app.policy.principal import Principal

# Upper bound for the member directory typeahead, so a single request can never
# ask for the whole user table.
DIRECTORY_LIMIT = 50


async def list_teams_for_user(session: AsyncSession, *, user_id: uuid.UUID) -> list[Any]:
    """Teams the user belongs to, with their role in each."""
    result = await session.execute(
        select(
            Team.id.label("team_id"),
            Team.key.label("team_key"),
            Team.name.label("team_name"),
            Team.description,
            Membership.role,
        )
        .join(Membership, Membership.team_id == Team.id)
        .where(Membership.user_id == user_id)
        .order_by(Team.name)
    )
    return list(result.all())


async def list_members(session: AsyncSession, *, team_id: uuid.UUID) -> list[Any]:
    """The team's roster. Readable by every member, including viewers."""
    result = await session.execute(
        select(
            Membership.user_id,
            User.email,
            User.display_name,
            Membership.role,
        )
        .join(User, User.id == Membership.user_id)
        .where(Membership.team_id == team_id)
        .order_by(Membership.role.desc(), User.display_name)
    )
    return list(result.all())


async def directory(
    session: AsyncSession, *, principal: Principal, query: str | None = None
) -> list[Any]:
    """Users the caller could reasonably need to see.

    Restricted to people who share at least one team with the caller. A global
    directory would let any authenticated user enumerate every account in the
    system, including teams they have no relationship with; this is the smallest
    useful answer, and it is what the assignment picker needs.
    """
    statement = (
        select(User.id, User.email, User.display_name)
        .distinct()
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id.in_(principal.team_ids))
        .order_by(User.display_name)
        .limit(DIRECTORY_LIMIT)
    )
    if query:
        # Filtering happens in the database, not by loading rows and discarding
        # them here.
        pattern = f"%{query.strip()}%"
        statement = statement.where(User.display_name.ilike(pattern) | User.email.ilike(pattern))

    return list((await session.execute(statement)).all())


async def set_role(
    session: AsyncSession,
    *,
    principal: Principal,
    team_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role: Role,
) -> Any:
    """Grant `role` to `target_user_id` in `team_id`, creating the membership if needed.

    The caller's admin rights are checked by the route before this runs. What is
    checked here is whether the change itself is legal.
    """
    target = (
        await session.execute(select(User).where(User.id == target_user_id))
    ).scalar_one_or_none()
    if target is None or not await _shares_a_team(session, principal.user.id, target_user_id):
        # Both "no such user" and "a stranger" produce the same 404, so the
        # endpoint cannot be used to probe for accounts.
        raise NotFound("User not found.")

    existing = (
        await session.execute(
            select(Membership).where(
                Membership.team_id == team_id, Membership.user_id == target_user_id
            )
        )
    ).scalar_one_or_none()

    if existing is not None and existing.role == Role.ADMIN.value and role is not Role.ADMIN:
        await _ensure_not_last_admin(session, team_id)

    if existing is None:
        await session.execute(
            insert(Membership).values(team_id=team_id, user_id=target_user_id, role=role.value)
        )
    else:
        await session.execute(
            update(Membership).where(Membership.id == existing.id).values(role=role.value)
        )
    await session.commit()

    return (
        await session.execute(
            select(Membership.user_id, User.email, User.display_name, Membership.role)
            .join(User, User.id == Membership.user_id)
            .where(Membership.team_id == team_id, Membership.user_id == target_user_id)
        )
    ).one()


async def remove_member(
    session: AsyncSession, *, team_id: uuid.UUID, target_user_id: uuid.UUID
) -> None:
    """Remove a member from a team, if nothing depends on them."""
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.team_id == team_id, Membership.user_id == target_user_id
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise NotFound("Membership not found.")

    if membership.role == Role.ADMIN.value:
        await _ensure_not_last_admin(session, team_id)

    assigned = (
        await session.execute(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.team_id == team_id, WorkItem.assignee_id == target_user_id)
        )
    ).scalar_one()
    if assigned:
        raise MemberHasAssignedItems(
            f"This member is the assignee of {assigned} work item(s) in this team. "
            "Reassign them before removing the membership."
        )

    await session.execute(delete(Membership).where(Membership.id == membership.id))
    await session.commit()


async def _ensure_not_last_admin(session: AsyncSession, team_id: uuid.UUID) -> None:
    admins = (
        await session.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.team_id == team_id, Membership.role == Role.ADMIN.value)
        )
    ).scalar_one()
    if admins <= 1:
        raise LastAdmin()


async def _shares_a_team(session: AsyncSession, user_a: uuid.UUID, user_b: uuid.UUID) -> bool:
    """True when the two users have at least one team in common.

    This is the same relationship the member directory exposes, so the rule is
    consistent: you can only act on people you can already see. Adding a complete
    stranger to a team is not possible through the API, which is a deliberate
    privacy choice recorded in `KNOWN_LIMITATIONS.md`.
    """
    common = (
        select(Membership.team_id)
        .where(Membership.user_id == user_b)
        .where(
            Membership.team_id.in_(select(Membership.team_id).where(Membership.user_id == user_a))
        )
        .limit(1)
    )
    return (await session.execute(common)).first() is not None


__all__ = [
    "DIRECTORY_LIMIT",
    "directory",
    "list_members",
    "list_teams_for_user",
    "remove_member",
    "set_role",
]
