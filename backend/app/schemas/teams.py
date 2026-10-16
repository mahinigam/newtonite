"""Team and membership schemas."""

from __future__ import annotations

import uuid

from app.models.enums import Role
from app.schemas import RequestModel, ResponseModel


class TeamSummary(ResponseModel):
    """A team as the caller sees it.

    Team fields are prefixed (`team_id`, `team_key`) so a team is described the same
    way here and inside a membership, and so a nested `id` can never be mistaken for
    the enclosing resource.
    """

    team_id: uuid.UUID
    team_key: str
    team_name: str
    description: str
    # The caller's own role in this team. Never accepted on input.
    role: Role


class MemberSummary(ResponseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: Role


class DirectoryUser(ResponseModel):
    id: uuid.UUID
    email: str
    display_name: str


class SetRoleRequest(RequestModel):
    role: Role
