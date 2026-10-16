"""Reusable FastAPI dependencies.

Authentication lives here rather than in each route: a route that declares
``principal: PrincipalDep`` is protected, and a route that forgets to is
conspicuous in review. Authorization is expressed as a dependency too, so the check
cannot be skipped by accident inside a handler.

Nothing in this module reads a role or a team membership from the request. The
caller's roles are loaded from the database on every request.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.errors import NotAuthenticated
from app.models.enums import Role
from app.policy.principal import Principal
from app.policy.roles import Permission, require_permission
from app.services import auth as auth_service

BEARER_SCHEME = "bearer"


def get_app_settings(request: Request) -> Settings:
    """The settings the running application was built with."""
    settings: Settings = request.app.state.settings
    return settings


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def extract_bearer_token(header_value: str | None) -> str:
    """Pull the token out of an `Authorization` header.

    Rejects a missing header, a different scheme, and an empty token with the same
    401, so a client that sends `Basic ...` gets the same answer as one that sends
    nothing.
    """
    if header_value is None:
        raise NotAuthenticated()
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != BEARER_SCHEME or not token.strip():
        raise NotAuthenticated("Expected an 'Authorization: Bearer <token>' header.")
    return token.strip()


async def get_principal(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve the bearer token to the authenticated caller."""
    token = extract_bearer_token(authorization)
    return await auth_service.authenticate(session, token=token, settings=settings)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


@dataclass(frozen=True, slots=True)
class TeamContext:
    """A request that has already been authorized against one team."""

    principal: Principal
    team_id: uuid.UUID
    role: Role

    def require(self, permission: Permission) -> None:
        require_permission(self.role, permission)


def require_team_permission(permission: Permission) -> Callable[..., object]:
    """Build a dependency that authorizes the caller against ``{team_id}``.

    Used as ``Depends(require_team_permission(Permission.MANAGE_MEMBERS))``. The
    dependency declares ``team_id`` as a path parameter, so the route must expose
    one; a route without it fails at request time rather than silently skipping the
    check.
    """

    async def dependency(team_id: uuid.UUID, principal: PrincipalDep) -> TeamContext:
        role = principal.require_permission(team_id, permission)
        return TeamContext(principal=principal, team_id=team_id, role=role)

    return dependency


__all__ = [
    "PrincipalDep",
    "SessionDep",
    "SettingsDep",
    "TeamContext",
    "extract_bearer_token",
    "get_app_settings",
    "get_principal",
    "require_team_permission",
]
