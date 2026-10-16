"""Authentication request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.models.enums import Role
from app.schemas import RequestModel, ResponseModel


class LoginRequest(RequestModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Lower-case and trim.

        Addresses are stored normalised, so doing it at the boundary means
        `Ada.Admin@Example.COM ` and `ada.admin@example.com` are the same account
        rather than two.
        """
        return value.strip().lower()


class UserSummary(ResponseModel):
    id: uuid.UUID
    email: str
    display_name: str


class MembershipSummary(ResponseModel):
    team_id: uuid.UUID
    team_key: str
    team_name: str
    role: Role


class LoginResponse(ResponseModel):
    token: str
    token_type: str = "Bearer"
    expires_at: datetime
    user: UserSummary


class MeResponse(ResponseModel):
    user: UserSummary
    memberships: list[MembershipSummary]


class LogoutResponse(ResponseModel):
    revoked: bool
