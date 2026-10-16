"""Team and membership endpoints.

Every route here is scoped to ``{team_id}`` and authorized against the caller's
membership in that team. `list_my_teams` is the one exception and is inherently
self-scoped: it can only ever return the caller's own teams.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import PrincipalDep, SessionDep, TeamContext, require_team_permission
from app.policy.roles import Permission
from app.schemas.teams import MemberSummary, SetRoleRequest, TeamSummary
from app.services import memberships as membership_service

router = APIRouter(prefix="/teams", tags=["teams"])

# Any member of the team may read it, including a viewer.
ViewTeam = Annotated[TeamContext, Depends(require_team_permission(Permission.VIEW_TEAM))]
# Only an admin may change who is in the team or what they can do.
ManageMembers = Annotated[TeamContext, Depends(require_team_permission(Permission.MANAGE_MEMBERS))]


@router.get("", response_model=list[TeamSummary], summary="Teams the caller belongs to")
async def list_my_teams(session: SessionDep, principal: PrincipalDep) -> list[TeamSummary]:
    teams = await membership_service.list_teams_for_user(session, user_id=principal.user.id)
    return [TeamSummary.model_validate(team) for team in teams]


@router.get(
    "/{team_id}/members",
    response_model=list[MemberSummary],
    summary="The team's roster",
)
async def list_members(context: ViewTeam, session: SessionDep) -> list[MemberSummary]:
    members = await membership_service.list_members(session, team_id=context.team_id)
    return [MemberSummary.model_validate(member) for member in members]


@router.put(
    "/{team_id}/members/{user_id}",
    response_model=MemberSummary,
    summary="Grant or change a member's role (admin only)",
)
async def set_member_role(
    payload: SetRoleRequest,
    context: ManageMembers,
    session: SessionDep,
    user_id: uuid.UUID,
) -> MemberSummary:
    """Set a member's role.

    The role in the request body is the *only* thing taken from the client: which
    team, and whether the caller may act on it, come from the path and from the
    caller's stored memberships. A team must keep at least one admin, so the last
    admin cannot be demoted.
    """
    member = await membership_service.set_role(
        session,
        principal=context.principal,
        team_id=context.team_id,
        target_user_id=user_id,
        role=payload.role,
    )
    return MemberSummary.model_validate(member)


@router.delete(
    "/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member (admin only)",
)
async def remove_member(
    context: ManageMembers,
    session: SessionDep,
    user_id: uuid.UUID,
) -> None:
    """Remove a membership.

    Refused with 409 when the member is the team's last admin, or when they are
    still the assignee of work items in that team. Nothing is deleted implicitly:
    the caller has to reassign the work first.
    """
    await membership_service.remove_member(session, team_id=context.team_id, target_user_id=user_id)
