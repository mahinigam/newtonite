"""Application errors and the HTTP error envelope.

Every failure the API returns deliberately has the same shape:

```json
{"error": {"code": "invalid_credentials", "message": "Email or password is incorrect."}}
```

`code` is a stable, machine-readable identifier the frontend switches on; `message`
is safe to show a user. Neither ever contains a stack trace, SQL, a table name or a
constraint name.

The mapping from situation to status code is fixed and deliberate:

| Status | Meaning here |
| --- | --- |
| 401 | Not authenticated (missing, malformed, expired or revoked session) |
| 403 | Authenticated and inside the team, but the role is not permitted |
| 404 | The resource does not exist *or* belongs to a team the caller is not in |
| 409 | The request conflicts with current state (bad transition, already claimed, key reused) |
| 412 | An `If-Match` version precondition failed; the caller's copy is stale |
| 422 | The request body failed validation |

403 and 404 are deliberately distinct. Returning 404 for another team's resource
avoids leaking whether it exists, while 403 is only used when the caller is already
inside the team and the role is the problem.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for every deliberate failure."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        self.details = details
        self.headers = headers or {}

    def as_body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


# --- authentication ------------------------------------------------------- #


class NotAuthenticated(AppError):
    """No usable bearer token was supplied."""

    status_code = 401
    code = "not_authenticated"
    message = "Authentication is required."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class InvalidCredentials(AppError):
    """Login failed.

    Deliberately identical for an unknown email, a wrong password and a disabled
    account: the caller learns only that the credentials are wrong, never which
    half of it was correct.
    """

    status_code = 401
    code = "invalid_credentials"
    message = "Email or password is incorrect."


class InvalidSession(AppError):
    """The token is unknown or has been revoked."""

    status_code = 401
    code = "invalid_session"
    message = "Your session is no longer valid. Please sign in again."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class SessionExpired(AppError):
    """The session passed its absolute or idle lifetime."""

    status_code = 401
    code = "session_expired"
    message = "Your session has expired. Please sign in again."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


# --- authorization ------------------------------------------------------- #


class Forbidden(AppError):
    """The caller is in the team but their role does not permit this action."""

    status_code = 403
    code = "forbidden"
    message = "Your role in this team does not allow this action."


class NotFound(AppError):
    """The resource does not exist, or is not visible to the caller.

    Also used when a team id is unknown or the caller is not a member, so that the
    two cases are indistinguishable from outside.
    """

    status_code = 404
    code = "not_found"
    message = "Not found."


# --- conflicts ----------------------------------------------------------- #


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class InvalidTransition(Conflict):
    code = "invalid_transition"
    message = "That status change is not allowed from the item's current status."


class AlreadyClaimed(Conflict):
    code = "item_already_claimed"
    message = "This item has already been claimed."


class IdempotencyKeyReuse(Conflict):
    code = "idempotency_key_reuse"
    message = (
        "This Idempotency-Key was already used with a different request body. "
        "Use a new key for a different request."
    )


class IdempotencyKeyInProgress(Conflict):
    message = "An identical request is still being processed. Retry shortly."
    code = "idempotency_key_in_progress"


class AssigneeRequired(Conflict):
    """Work cannot start without an owner."""

    code = "assignee_required"
    message = "Assign the item to somebody before starting it."


class InvalidAssignee(AppError):
    """The chosen assignee is not eligible for this item's team."""

    status_code = 422
    code = "invalid_assignee"
    message = "That person is not a member of this team."


class IdempotencyKeyRequired(AppError):
    status_code = 400
    code = "idempotency_key_required"
    message = (
        "Creating a work item requires an 'Idempotency-Key' header, so that a retry "
        "cannot create a duplicate."
    )


class PreconditionRequired(AppError):
    """A conditional update arrived without its precondition.

    `428 Precondition Required` (RFC 6585) rather than a silent last-write-wins: the
    API will not overwrite a value the caller has not demonstrated they have seen.
    """

    status_code = 428
    code = "precondition_required"
    message = (
        "Send an 'If-Match' header with the item's current version, so that a change "
        "based on a stale copy is rejected instead of silently overwriting newer data."
    )


class LastAdmin(Conflict):
    code = "last_admin"
    message = "A team must keep at least one admin."


class MemberHasAssignedItems(Conflict):
    code = "member_has_assigned_items"
    message = "Reassign this member's open work items before removing them from the team."


class StaleVersion(AppError):
    """The `If-Match` version did not match the stored version."""

    status_code = 412
    code = "stale_version"
    message = "This item was changed by somebody else. Refresh and try again."


# --- validation ---------------------------------------------------------- #


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
    message = "The request could not be validated."


# --- wiring -------------------------------------------------------------- #


def register_exception_handlers(app: FastAPI) -> None:
    """Install the error envelope on `app`."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.as_body(), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages are safe and useful; the raw input is not
        # echoed back, because it may contain a password.
        details = [
            {"field": ".".join(str(part) for part in error["loc"][1:]), "message": error["msg"]}
            for error in exc.errors()
        ]
        failure = ValidationFailed(details=details)
        return JSONResponse(status_code=failure.status_code, content=failure.as_body())

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Covers framework-level errors such as 404 for an unknown route and 405
        # for a wrong method, so clients always parse the same shape.
        codes = {404: "not_found", 405: "method_not_allowed", 401: "not_authenticated"}
        code = codes.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(
            status_code=exc.status_code, content={"error": {"code": code, "message": message}}
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Logged in full, returned as nothing: an unexpected failure must not leak
        # implementation detail to a client.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        failure = AppError()
        return JSONResponse(status_code=failure.status_code, content=failure.as_body())
