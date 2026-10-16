"""Work-item request and response schemas.

Two deliberate choices:

* **A summary and a detail shape.** A list of 25 items does not need 20 000 characters of
  description each; the detail shape adds it for a single-item read. Each view asks the
  database for only what it returns.
* **`available_transitions` is computed on the server.** The interface renders it rather
  than deciding which buttons are legal, so a disabled control and the server's answer are
  the same thing. It is still advisory: every transition is validated again when attempted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import Priority, Status, WorkItemType
from app.policy.workflow import available_transitions
from app.schemas import RequestModel, ResponseModel


class UserRef(ResponseModel):
    """A user as embedded in a work item."""

    id: uuid.UUID
    email: str
    display_name: str


class WorkItemSummary(ResponseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    title: str
    type: WorkItemType
    status: Status
    priority: Priority
    assignee: UserRef | None
    due_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    available_transitions: list[Status]

    @classmethod
    def from_row(cls, row: Any, *, role: Any) -> WorkItemSummary:
        transitions = available_transitions(
            status=Status(row.status), role=role, has_assignee=row.assignee_id is not None
        )
        return cls(
            id=row.id,
            team_id=row.team_id,
            title=row.title,
            type=row.type,
            status=row.status,
            priority=row.priority,
            assignee=_user_ref(row.assignee_id, row.assignee_email, row.assignee_name),
            due_at=row.due_at,
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
            available_transitions=list(transitions),
        )


class WorkItemDetail(WorkItemSummary):
    description: str
    created_by: UserRef

    @classmethod
    def from_row(cls, row: Any, *, role: Any) -> WorkItemDetail:
        summary = WorkItemSummary.from_row(row, role=role)
        return cls(
            **summary.model_dump(),
            description=row.description,
            created_by=_user_ref(row.creator_id, row.creator_email, row.creator_name),
        )


class WorkItemPage(ResponseModel):
    """One page of a team's items.

    `next_cursor` is passed back verbatim to fetch the following page.
    """

    items: list[WorkItemSummary]
    next_cursor: str | None
    has_more: bool
    limit: int


class CreateWorkItemRequest(RequestModel):
    """The create body.

    There is no `team_id`, `status`, `version` or `created_by`: the team comes from the
    path, the status always starts at `open`, and the rest is the server's. Because the
    base class forbids extra fields, sending any of them is a validation error rather
    than a silently ignored field.
    """

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=20000)
    type: WorkItemType
    priority: Priority = Priority.MEDIUM
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None


class UpdateWorkItemRequest(RequestModel):
    """A partial edit.

    Every field is optional and only the ones actually present are applied, so an
    omitted field means "no opinion". Sending a field as `null` clears it — that is how
    an item is unassigned or a due date removed.
    """

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    type: WorkItemType | None = None
    priority: Priority | None = None
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None


class TransitionRequest(RequestModel):
    to: Status
    note: str | None = Field(default=None, max_length=2000)


class ClaimResponse(WorkItemDetail):
    """Claiming returns the item as it now stands."""


def _user_ref(id: uuid.UUID | None, email: str | None, display_name: str | None) -> UserRef | None:
    if id is None:
        return None
    assert email is not None and display_name is not None
    return UserRef(id=id, email=email, display_name=display_name)


__all__ = [
    "ClaimResponse",
    "CreateWorkItemRequest",
    "TransitionRequest",
    "UpdateWorkItemRequest",
    "UserRef",
    "WorkItemDetail",
    "WorkItemPage",
    "WorkItemSummary",
]
