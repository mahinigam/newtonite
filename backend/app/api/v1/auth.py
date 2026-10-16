"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MembershipSummary,
    MeResponse,
    UserSummary,
)
from app.services import auth as auth_service
from app.services import memberships as membership_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange credentials for a session token",
)
async def login(
    payload: LoginRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> LoginResponse:
    """Start a session.

    The response contains the bearer token exactly once. It is not stored
    server-side in recoverable form, so a client that loses it has to sign in
    again.

    There is no rate limiting here; see `docs/KNOWN_LIMITATIONS.md` for the residual
    risk and the reasoning.
    """
    user, issued = await auth_service.login(
        session, email=payload.email, password=payload.password, settings=settings
    )
    return LoginResponse(
        token=issued.token,
        expires_at=issued.expires_at,
        user=UserSummary.model_validate(user),
    )


@router.post("/logout", response_model=LogoutResponse, summary="Revoke the current session")
async def logout(session: SessionDep, principal: PrincipalDep) -> LogoutResponse:
    """Sign out by revoking the session the request is using.

    Revocation takes effect immediately for every subsequent request, including
    requests made by the same token in parallel.
    """
    revoked = await auth_service.revoke(session, session_id=principal.session_id)
    return LogoutResponse(revoked=revoked)


@router.get("/me", response_model=MeResponse, summary="The caller and their teams")
async def me(session: SessionDep, principal: PrincipalDep) -> MeResponse:
    """Describe the authenticated caller.

    Returns their role in each team they belong to, which is what the frontend uses
    to decide what to offer. The roles are advisory to the UI only: every write is
    authorized again on the server.
    """
    teams = await membership_service.list_teams_for_user(session, user_id=principal.user.id)
    return MeResponse(
        user=UserSummary.model_validate(principal.user),
        memberships=[MembershipSummary.model_validate(team) for team in teams],
    )
