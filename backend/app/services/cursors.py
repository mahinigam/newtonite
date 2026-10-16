"""Opaque keyset cursors.

List endpoints are paginated by keyset, not by `OFFSET`, for two reasons: skipping
thousands of rows costs the same as reading the first page (the index is walked from the
cursor position), and a row inserted while somebody is paging cannot cause an item to be
shown twice or skipped entirely — which `OFFSET` does, because it counts rows rather than
identifying a position.

The position is the pair `(created_at, id)`. `id` is what makes it a *total* order: two
items created in the same microsecond still have exactly one correct sequence.

The cursor is base64url so that clients treat it as opaque. It is not signed: it contains
no secret and no permission decision, only a position within a result set the caller is
already authorized to read. Tampering can only produce a different valid position (or a
validation error), never access to another team's rows, because the team filter is applied
separately from the cursor.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.errors import ValidationFailed

_SEPARATOR = "|"


@dataclass(frozen=True, slots=True)
class Cursor:
    created_at: datetime
    id: uuid.UUID


def encode_cursor(created_at: datetime, id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}{_SEPARATOR}{id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> Cursor:
    """Parse a cursor, or raise a validation error.

    A malformed cursor is the caller's mistake and is reported as such rather than
    being silently ignored, which would quietly restart the list from the beginning.
    """
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailed("The pagination cursor is not valid.") from exc

    created_at_text, separator, id_text = decoded.partition(_SEPARATOR)
    if not separator:
        raise ValidationFailed("The pagination cursor is not valid.")

    try:
        return Cursor(created_at=datetime.fromisoformat(created_at_text), id=uuid.UUID(id_text))
    except ValueError as exc:
        raise ValidationFailed("The pagination cursor is not valid.") from exc


__all__ = ["Cursor", "decode_cursor", "encode_cursor"]
