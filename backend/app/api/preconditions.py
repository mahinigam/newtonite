"""`ETag` / `If-Match` handling.

Conditional updates are how a stale write is rejected rather than silently
overwriting newer data. The precondition is the item's `version`, exposed as a strong
`ETag`:

```
GET /api/v1/items/{id}          ->  ETag: "7"
PATCH /api/v1/items/{id}        If-Match: "7"   ->  succeeds, version becomes 8
PATCH /api/v1/items/{id}        If-Match: "6"   ->  412 stale_version
PATCH /api/v1/items/{id}        (no header)     ->  428 precondition_required
```

Parsing is deliberately liberal about quoting — `7`, `"7"` and `W/"7"` all mean version 7 —
because rejecting a syntactically valid but unquoted value would be pedantic with no safety
benefit. The comparison itself happens inside the `UPDATE` statement, so it cannot be raced:
the check and the bump are one operation, not a read followed by a write.
"""

from __future__ import annotations

from app.errors import PreconditionRequired, ValidationFailed


def etag_for(version: int) -> str:
    """The strong ETag for a version. Quoted, as the header requires."""
    return f'"{version}"'


def parse_if_match(header: str | None) -> int:
    """Return the expected version, or raise."""
    if header is None or not header.strip():
        raise PreconditionRequired()

    value = header.strip()
    if value.startswith("W/"):
        # A weak validator is acceptable here: we do not have byte-identical
        # representations to protect, only a monotonic version counter.
        value = value[2:].strip()
    value = value.strip('"')

    try:
        version = int(value)
    except ValueError as exc:
        raise ValidationFailed(
            'The If-Match header must be the item\'s version number, for example "7".'
        ) from exc

    if version < 1:
        raise ValidationFailed("The If-Match header must be a positive version number.")
    return version


__all__ = ["etag_for", "parse_if_match"]
