"""Comment request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import StringConstraints

from app.models.comment import MAX_COMMENT_LENGTH
from app.schemas import RequestModel, ResponseModel
from app.schemas.pagination import Page
from app.schemas.work_items import UserRef


class CommentResponse(ResponseModel):
    id: uuid.UUID
    work_item_id: uuid.UUID
    body: str
    author: UserRef
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> CommentResponse:
        return cls(
            id=row.id,
            work_item_id=row.work_item_id,
            body=row.body,
            author=UserRef(
                id=row.author_id,
                email=row.author_email,
                display_name=row.author_name,
            ),
            created_at=row.created_at,
        )


class CreateCommentRequest(RequestModel):
    """The comment body.

    There is no `author_id`, `work_item_id` or timestamp: the work item comes from the
    path and the author from the session, so neither can be claimed by a client.

    Whitespace is stripped **before** the length is checked, which is what makes a body
    of spaces a validation error here rather than a database constraint violation later.
    The database still enforces `char_length(btrim(body)) BETWEEN 1 AND 4000`; the point
    is that a client gets a useful `422` naming the field instead of a `500`.
    """

    body: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_COMMENT_LENGTH),
    ]


class CommentPage(Page[CommentResponse]):
    """One page of an item's comments, newest first."""


__all__ = ["CommentPage", "CommentResponse", "CreateCommentRequest"]
