"""Page bounds and the cursor that continues a listing.

Every list endpoint in the API pages the same way: a bounded page, keyset ordering with
`id` as a deterministic tie-breaker, and a `next_cursor` the client hands back verbatim.
Putting the bound and the cursor helper here means the four list endpoints cannot drift
into four slightly different conventions.

The *ordering* is deliberately not here — it belongs to the query, because it differs:
items and comments are read newest first, and the cursor's meaning depends on that.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_

from app.services.cursors import Cursor, encode_cursor

#: How many rows a list returns when the caller does not say.
DEFAULT_PAGE_SIZE = 25

#: The most a caller can ask for. A page is bounded so no request can pull a whole
#: table into a browser, which is the point of doing this on the server.
MAX_PAGE_SIZE = 100


def next_cursor(row: Any) -> str | None:
    """A cursor pointing just past `row`.

    The position is the row's `(created_at, id)`; which way the next page walks is
    decided by the query's ordering, not by the cursor. Every paginated table here has
    both columns, and every list query selects them, so one helper serves them all.
    """
    return encode_cursor(row.created_at, row.id)


def cursor_for(rows: list[Any], *, has_more: bool) -> str | None:
    """The `next_cursor` for a page, or `None` when the page is the last one.

    Three conditions, and all three matter: there must be more rows, and the page must
    be non-empty, or there is no position to continue from. Returning a cursor for an
    empty page would invite a client to loop forever on it.
    """
    return next_cursor(rows[-1]) if has_more and rows else None


def descending_keyset(created_at: Any, id: Any, cursor: Cursor) -> Any:
    """The keyset predicate for a list ordered `(created_at DESC, id DESC)`.

    Written out as the expanded `OR`/`AND` rather than as a row comparison, because this
    is the shape the composite indexes can walk directly: `(team_id, created_at DESC,
    id DESC)` and `(work_item_id, created_at DESC, id DESC)`.

    Every list in the API orders newest first and pages with this predicate, so "paging
    works" is one implementation rather than one per endpoint.
    """
    return or_(
        created_at < cursor.created_at,
        and_(created_at == cursor.created_at, id < cursor.id),
    )


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "cursor_for",
    "descending_keyset",
    "next_cursor",
]
