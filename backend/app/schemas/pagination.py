"""The shape every paginated list response shares.

One type rather than four identical ones, because the four list endpoints must page the
same way for a client to reuse anything: a bounded page, an opaque `next_cursor` to hand
back verbatim, and `has_more` answering "is there another page?" without a second request.

`limit` is echoed so a client can tell a short page from a page that was clamped.
"""

from __future__ import annotations

from app.schemas import ResponseModel


class Page[T](ResponseModel):
    items: list[T]
    next_cursor: str | None
    has_more: bool
    limit: int


__all__ = ["Page"]
