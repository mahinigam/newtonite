"""Comments: a person's note on a work item.

Three properties matter, and each is a decision rather than an accident:

* **A comment and its history entry are one transaction.** The `commented` event is
  written through `activity.record`, which never commits, so a comment cannot exist
  without the timeline entry that explains it — the same coupling the item mutations
  have. `tests/test_activity_atomicity.py` breaks the history insert on purpose to
  check it.
* **Creation is idempotent.** A double-click or a retry carries the same
  `Idempotency-Key`, and the second request replays the first comment instead of adding
  a duplicate. This reuses `services/idempotency.py`, which is why that mechanism is a
  service rather than something buried in the item code.
* **Comments are append-only.** There is no edit or delete path, so a comment can never
  contradict the history it produced. A correction is another comment.

Reading is `VIEW_TEAM` — a viewer may read the discussion — while writing is `COMMENT`,
which every member holds.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.errors import NotFound
from app.models import Comment, User
from app.models.enums import ActivityType, Operation
from app.policy.principal import Principal
from app.policy.roles import Permission
from app.schemas.comments import CreateCommentRequest
from app.services import activity, idempotency, work_items
from app.services.cursors import Cursor
from app.services.pagination import DEFAULT_PAGE_SIZE, descending_keyset

# The author is joined, not lazily loaded: a page of 25 comments must not cost 25 extra
# queries, and `lazy="raise"` on the relationship makes that mistake loud rather than slow.
_AUTHOR = aliased(User, name="comment_author")

COMMENT_QUERY = select(
    Comment.id,
    Comment.work_item_id,
    Comment.body,
    Comment.created_at,
    _AUTHOR.id.label("author_id"),
    _AUTHOR.email.label("author_email"),
    _AUTHOR.display_name.label("author_name"),
).join(_AUTHOR, _AUTHOR.id == Comment.author_id)

#: How much of the body is copied into the history event. A convenience for rendering a
#: timeline without fetching every comment; clients must not depend on it, and the
#: comment itself is the record.
PREVIEW_LENGTH = 200


@dataclass(frozen=True, slots=True)
class CommentOutcome:
    comment: Any
    #: True when this response is the replay of an earlier identical request.
    replayed: bool


async def get_comment(session: AsyncSession, comment_id: uuid.UUID) -> Any | None:
    return (await session.execute(COMMENT_QUERY.where(Comment.id == comment_id))).one_or_none()


def comment_list_statement(
    *,
    work_item_id: uuid.UUID,
    cursor: Cursor | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> Any:
    """Build one page of an item's comments, newest first.

    Newest first matches the item list and the activity timeline, so a client paging any
    of them does the same thing. The index is `(work_item_id, created_at, id)`; a
    backward scan serves this ordering, so no second index is needed.

    Pure, like the item list builder, so the exact SQL can be measured on its own.
    """
    statement = COMMENT_QUERY.where(Comment.work_item_id == work_item_id)
    if cursor is not None:
        statement = statement.where(descending_keyset(Comment.created_at, Comment.id, cursor))
    return statement.order_by(Comment.created_at.desc(), Comment.id.desc()).limit(limit + 1)


async def list_comments(
    session: AsyncSession,
    *,
    principal: Principal,
    item_id: uuid.UUID,
    cursor: Cursor | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[Any], bool]:
    """One page of an item's comments, after authorizing against the item's team."""
    item, _role = await work_items.load_visible_item(
        session, principal=principal, item_id=item_id, permission=Permission.VIEW_TEAM
    )
    rows = list(
        (
            await session.execute(
                comment_list_statement(work_item_id=item.id, cursor=cursor, limit=limit)
            )
        ).all()
    )
    # One extra row answers "is there another page?" without a second count query.
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def create_comment(
    session: AsyncSession,
    *,
    principal: Principal,
    item_id: uuid.UUID,
    request: CreateCommentRequest,
    idempotency_key: str,
) -> CommentOutcome:
    """Add a comment exactly once, even if the request is delivered twice.

    The `Idempotency-Key` row, the comment and its history event are written in the same
    transaction, so a retry either replays a comment that definitely exists or creates
    one; it can never leave a key pointing at a comment that was rolled back.
    """
    item, _role = await work_items.load_visible_item(
        session, principal=principal, item_id=item_id, permission=Permission.COMMENT
    )
    digest = idempotency.fingerprint(request)

    key_id = await idempotency.claim_key(
        session,
        actor_id=principal.user.id,
        operation=Operation.CREATE_COMMENT,
        key=idempotency_key,
        fingerprint=digest,
    )

    if key_id is None:
        resource_id = await idempotency.replay(
            session,
            actor_id=principal.user.id,
            operation=Operation.CREATE_COMMENT,
            key=idempotency_key,
            fingerprint=digest,
        )
        comment = await get_comment(session, resource_id)
        if comment is None:  # pragma: no cover - comments are never deleted
            raise NotFound("Comment not found.")
        return CommentOutcome(comment=comment, replayed=True)

    comment_id = (
        await session.execute(
            insert(Comment)
            .values(
                work_item_id=item.id,
                # Never taken from the request: the server decides who wrote it.
                author_id=principal.user.id,
                body=request.body,
            )
            .returning(Comment.id)
        )
    ).scalar_one()

    await activity.record(
        session,
        work_item_id=item.id,
        team_id=item.team_id,
        actor_id=principal.user.id,
        event_type=ActivityType.COMMENTED,
        meta={"comment_id": str(comment_id), "preview": preview(request.body)},
    )

    await idempotency.complete_key(
        session,
        key_id=key_id,
        resource_id=comment_id,
        response_status=201,
        response_body={"comment_id": str(comment_id)},
    )
    await session.commit()

    comment = await get_comment(session, comment_id)
    assert comment is not None
    return CommentOutcome(comment=comment, replayed=False)


def preview(body: str) -> str:
    """The first `PREVIEW_LENGTH` characters, with runs of whitespace collapsed.

    A comment body is prose and may contain newlines; the timeline shows one line, so the
    value is normalised where it is stored rather than in every client.
    """
    return " ".join(body.split())[:PREVIEW_LENGTH]


__all__ = [
    "COMMENT_QUERY",
    "CommentOutcome",
    "comment_list_statement",
    "create_comment",
    "get_comment",
    "list_comments",
    "preview",
]
