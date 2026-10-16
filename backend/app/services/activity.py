"""Writing activity history.

Every operationally meaningful change to a work item goes through `record`, which does
exactly one thing: insert a row into `activity_events` **in the caller's transaction**.

That last detail is the whole point. `record` never commits, so the history row and the
state change it describes commit together or not at all. If the caller rolls back — a lost
version check, a constraint violation, an unexpected error — the history entry disappears
with it, and the timeline cannot claim something that did not happen.

Conventions for the rendered values, shared with the seed data so a reviewer sees one
consistent timeline:

* `from_value` / `to_value` hold the raw stored value: a status, a priority, a type, or a
  user id for assignment changes. They are the fact.
* `meta` holds whatever helps render it — a display name, a rejection note, the id of a
  comment. They are convenience, and clients must work without them.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityEvent
from app.models.enums import ActivityType


async def record(
    session: AsyncSession,
    *,
    work_item_id: uuid.UUID,
    team_id: uuid.UUID,
    actor_id: uuid.UUID,
    event_type: ActivityType,
    from_value: str | None = None,
    to_value: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Append one history event. Does not commit.

    Callers are expected to be inside a transaction that also contains the change
    this event describes.
    """
    await session.execute(
        insert(ActivityEvent).values(
            work_item_id=work_item_id,
            team_id=team_id,
            actor_id=actor_id,
            event_type=event_type.value,
            from_value=from_value,
            to_value=to_value,
            meta=meta or {},
        )
    )


__all__ = ["record"]
