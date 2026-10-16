"""User directory.

Deliberately not a global user list: it returns only people who share at least one
team with the caller, which is exactly what an assignment picker needs and no more
than that.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.teams import DirectoryUser
from app.services import memberships as membership_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/directory",
    response_model=list[DirectoryUser],
    summary="People the caller may need to assign work to",
)
async def directory(
    session: SessionDep,
    principal: PrincipalDep,
    query: Annotated[
        str | None, Query(max_length=100, description="Name or email fragment")
    ] = None,
) -> list[DirectoryUser]:
    """Search teammates by name or email.

    Filtering happens in PostgreSQL and the result is capped, so a single request
    can never return the whole user table.
    """
    users = await membership_service.directory(session, principal=principal, query=query)
    return [DirectoryUser.model_validate(user) for user in users]
