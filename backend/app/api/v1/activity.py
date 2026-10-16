"""Activity history endpoints.

Two views of the same append-only table:

* `/items/{item_id}/activity` — one item's timeline, authorized against the item's team;
* `/teams/{team_id}/activity` — a team's recent activity, authorized against the team.

Both are **read-only by construction**: there is no route that writes an event, and the
database trigger behind `activity_events` refuses `UPDATE` and `DELETE` outright. History
is produced by the operations it describes, never by a client.

Each view paginates with its own cursor, independently of any item list — a timeline is
not a page of items, and the two must not share a position.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.activity import ActivityEventResponse, ActivityPage
from app.services import activity
from app.services.cursors import decode_cursor
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, cursor_for

router = APIRouter(tags=["activity"])


@router.get(
    "/items/{item_id}/activity",
    response_model=ActivityPage,
    summary="Read one work item's history, newest first",
)
async def item_activity(
    item_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    cursor: Annotated[str | None, Query(description="`next_cursor` from the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ActivityPage:
    """The item's timeline: what happened, who did it, and what changed.

    Newest first, bounded by `limit`, and paged with a cursor that is independent of the
    item list's. An item in a team the caller does not belong to is `404`.
    """
    rows, has_more = await activity.list_item_activity(
        session,
        principal=principal,
        item_id=item_id,
        cursor=decode_cursor(cursor, id_kind="int") if cursor else None,
        limit=limit,
    )

    return ActivityPage(
        items=[ActivityEventResponse.from_row(row) for row in rows],
        next_cursor=cursor_for(rows, has_more=has_more),
        has_more=has_more,
        limit=limit,
    )


@router.get(
    "/teams/{team_id}/activity",
    response_model=ActivityPage,
    summary="Read a team's recent activity, newest first",
)
async def team_activity(
    team_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    cursor: Annotated[str | None, Query(description="`next_cursor` from the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ActivityPage:
    """Everything that has happened in one team, newest first.

    Built from the event's denormalised `team_id`, so the feed needs no join back to
    `work_items` and cannot be assembled from a query that forgot the team filter. A team
    the caller is not a member of is `404`, not `403`.
    """
    rows, has_more = await activity.list_team_activity(
        session,
        principal=principal,
        team_id=team_id,
        cursor=decode_cursor(cursor, id_kind="int") if cursor else None,
        limit=limit,
    )

    return ActivityPage(
        items=[ActivityEventResponse.from_row(row) for row in rows],
        next_cursor=cursor_for(rows, has_more=has_more),
        has_more=has_more,
        limit=limit,
    )
