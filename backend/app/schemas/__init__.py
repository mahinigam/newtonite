"""Request and response schemas.

Schemas are kept separate from ORM models on purpose:

* a response never accidentally exposes a password hash or an internal column,
* a request can never set a server-controlled field such as `team_id`,
  `created_by`, `version` or `status` (see the comment on `extra="forbid"`),
* the database layout can change without silently changing the wire format.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    """Base for request bodies.

    ``extra="forbid"`` is the guard against mass assignment: sending
    ``{"team_id": "...", "version": 99}`` alongside a valid body is rejected
    outright rather than silently ignored, so a client cannot influence
    server-controlled fields and cannot be misled into thinking it did.
    """

    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    """Base for response bodies, readable straight from ORM objects."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")
