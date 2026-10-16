"""Work-item endpoints.

Shape of the API:

* create and list are **team-scoped** (`/teams/{team_id}/items`), so the team boundary is
  explicit and the authorization dependency can do its work;
* single-item operations address the item directly (`/items/{item_id}`) and derive the team
  from the row, because an item id is what a client actually holds. The team is then
  authorized from the *row's* team, never from anything the caller sent.

Three headers carry the correctness guarantees, and all three are enforced here:

* `Idempotency-Key` on create — a retry cannot produce a second item;
* `If-Match` on update and transition — a change based on a stale copy is refused;
* `ETag` on every item response — the version the client needs to send back.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.deps import (
    PrincipalDep,
    SessionDep,
    TeamContext,
    require_team_permission,
)
from app.api.preconditions import etag_for, parse_if_match
from app.errors import IdempotencyKeyRequired, ValidationFailed
from app.models.enums import Priority, Status, WorkItemType
from app.policy.principal import Principal
from app.policy.roles import Permission
from app.schemas.work_items import (
    CreateWorkItemRequest,
    TransitionRequest,
    UpdateWorkItemRequest,
    WorkItemDetail,
    WorkItemPage,
    WorkItemSummary,
)
from app.services import work_items
from app.services.cursors import decode_cursor

router = APIRouter(tags=["work-items"])

CreateItem = Annotated[TeamContext, Depends(require_team_permission(Permission.CREATE_ITEM))]
ViewTeam = Annotated[TeamContext, Depends(require_team_permission(Permission.VIEW_TEAM))]

IDEMPOTENCY_KEY_MAX_LENGTH = 255


@router.post(
    "/teams/{team_id}/items",
    response_model=WorkItemDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a work item (requires an Idempotency-Key)",
)
async def create_item(
    payload: CreateWorkItemRequest,
    context: CreateItem,
    session: SessionDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkItemDetail:
    """Create an item, exactly once even if the request is delivered twice.

    `Idempotency-Key` is **required**. Without it there is no way to tell a retry from a
    genuine second item, so the API refuses to guess.

    The status is `201 Created` for the request that created the item, and `200 OK` when
    the same request is replayed — the same item, clearly marked with
    `Idempotent-Replay: true`. Reusing a key with a *different* body is refused with
    `409 idempotency_key_reuse` rather than returning the first item.
    """
    key = _validate_idempotency_key(idempotency_key)

    outcome = await work_items.create_item(
        session,
        principal=context.principal,
        team_id=context.team_id,
        request=payload,
        idempotency_key=key,
    )

    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
    response.headers["ETag"] = etag_for(outcome.item.version)
    return WorkItemDetail.from_row(outcome.item, role=context.role)


@router.get(
    "/teams/{team_id}/items",
    response_model=WorkItemPage,
    summary="List a team's work items, filtered and paginated on the server",
)
async def list_items(
    context: ViewTeam,
    session: SessionDep,
    item_status: Annotated[list[Status] | None, Query(alias="status")] = None,
    priority: Annotated[list[Priority] | None, Query()] = None,
    item_type: Annotated[list[WorkItemType] | None, Query(alias="type")] = None,
    assignee: Annotated[
        str | None,
        Query(description="`me`, `none`, or a user id to filter by assignee"),
    ] = None,
    q: Annotated[str | None, Query(max_length=200, description="Full-text search")] = None,
    cursor: Annotated[str | None, Query(description="`next_cursor` from the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=work_items.MAX_PAGE_SIZE)] = work_items.DEFAULT_PAGE_SIZE,
) -> WorkItemPage:
    """Newest first, with every filter applied in PostgreSQL.

    Filters are optional and combine. `assignee=me` is the "my work" view and
    `assignee=none` the unassigned queue; both are the indexed paths. `status`,
    `priority` and `type` may be repeated to select several values.

    Pagination is keyset, not `OFFSET`: pass `next_cursor` from the previous response.
    A page is bounded to `limit` (maximum 100), so a single request can never pull the
    whole table into a browser.
    """
    assignee_id, unassigned, only_mine = _parse_assignee_filter(assignee, context.principal)

    rows, has_more = await work_items.list_items(
        session,
        team_id=context.team_id,
        statuses=tuple(item_status or ()),
        priorities=tuple(priority or ()),
        types=tuple(item_type or ()),
        assignee_id=assignee_id,
        unassigned=unassigned,
        only_mine=only_mine,
        viewer_id=context.principal.user.id,
        search=q,
        cursor=decode_cursor(cursor) if cursor else None,
        limit=limit,
    )

    return WorkItemPage(
        items=[WorkItemSummary.from_row(row, role=context.role) for row in rows],
        next_cursor=(work_items.next_cursor(rows[-1]) if has_more and rows else None),
        has_more=has_more,
        limit=limit,
    )


@router.get("/items/{item_id}", response_model=WorkItemDetail, summary="Read one work item")
async def get_item(
    item_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    response: Response,
) -> WorkItemDetail:
    """Return the item and its `ETag`.

    An item in a team the caller does not belong to is reported as `404`, exactly as an
    id that was never issued.
    """
    row, role = await work_items.load_visible_item(
        session, principal=principal, item_id=item_id, permission=Permission.VIEW_TEAM
    )
    response.headers["ETag"] = etag_for(row.version)
    return WorkItemDetail.from_row(row, role=role)


@router.patch(
    "/items/{item_id}",
    response_model=WorkItemDetail,
    summary="Edit a work item (requires If-Match)",
)
async def update_item(
    item_id: uuid.UUID,
    payload: UpdateWorkItemRequest,
    session: SessionDep,
    principal: PrincipalDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> WorkItemDetail:
    """Apply a partial edit, but only to the version the caller has seen.

    `If-Match` is required: without it the API would be doing exactly what the brief asks
    it not to do — letting a stale copy overwrite newer data. A missing header is
    `428 precondition_required`, and a version that no longer matches is
    `412 stale_version`, whose body names the current version so the client can refresh
    and retry.

    Only the fields actually present are changed; a field sent as `null` is cleared. A
    request that changes nothing succeeds without bumping the version or writing history.
    """
    expected_version = parse_if_match(if_match)

    row = await work_items.update_item(
        session,
        principal=principal,
        item_id=item_id,
        request=payload,
        expected_version=expected_version,
    )
    team_role = principal.require_team(row.team_id)
    response.headers["ETag"] = etag_for(row.version)
    return WorkItemDetail.from_row(row, role=team_role)


@router.post(
    "/items/{item_id}/claim",
    response_model=WorkItemDetail,
    summary="Claim an unassigned item",
)
async def claim_item(
    item_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    response: Response,
) -> WorkItemDetail:
    """Take ownership of an unassigned, open item.

    No `If-Match` is needed, and none is asked for: the condition that decides a race
    here is `assignee_id IS NULL`, evaluated inside the `UPDATE` itself. A version check
    would be the weaker guard — it can pass while somebody else is claiming the same
    item — so the API does not pretend otherwise.

    Losing the race is `409 item_already_claimed`. Claiming an item you already own is a
    no-op that returns the current state, so a double-click is harmless.
    """
    row = await work_items.claim_item(session, principal=principal, item_id=item_id)
    team_role = principal.require_team(row.team_id)
    response.headers["ETag"] = etag_for(row.version)
    return WorkItemDetail.from_row(row, role=team_role)


@router.post(
    "/items/{item_id}/transitions",
    response_model=WorkItemDetail,
    summary="Move an item through the workflow (requires If-Match)",
)
async def transition_item(
    item_id: uuid.UUID,
    payload: TransitionRequest,
    session: SessionDep,
    principal: PrincipalDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> WorkItemDetail:
    """Change status through an explicit, server-enforced transition.

    There is deliberately no endpoint that sets `status` directly: a generic update
    would be a way around the approval and closure rules. Legality, the caller's
    permission and the version precondition are all checked before anything changes,
    and the refusal names the statuses that *are* reachable so the client can
    reconcile instead of guessing.
    """
    expected_version = parse_if_match(if_match)

    row = await work_items.transition_item(
        session,
        principal=principal,
        item_id=item_id,
        target=payload.to,
        expected_version=expected_version,
        note=payload.note,
    )
    team_role = principal.require_team(row.team_id)
    response.headers["ETag"] = etag_for(row.version)
    return WorkItemDetail.from_row(row, role=team_role)


def _validate_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise IdempotencyKeyRequired()
    key = value.strip()
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValidationFailed(
            f"Idempotency-Key must be at most {IDEMPOTENCY_KEY_MAX_LENGTH} characters."
        )
    return key


def _parse_assignee_filter(
    value: str | None, principal: Principal
) -> tuple[uuid.UUID | None, bool, bool]:
    """Turn `assignee=me|none|<id>` into the flags the query builder wants."""
    if value is None or value == "":
        return None, False, False
    if value == "me":
        return None, False, True
    if value == "none":
        return None, True, False
    try:
        return uuid.UUID(value), False, False
    except ValueError as exc:
        raise ValidationFailed("The assignee filter must be 'me', 'none', or a user id.") from exc
