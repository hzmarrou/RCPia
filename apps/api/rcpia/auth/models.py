"""Request and response models for the auth API.

The database tables (`users`, `invitations`, `auth_sessions`) live in
`rcpia.core.models`; these are the shapes crossing the HTTP boundary.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from rcpia.core.models import UserRole

SESSION_COOKIE_NAME = "rcpia_session"


class InvitationCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.uploader


class InvitationRead(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    expires_at: datetime

    model_config = {"from_attributes": True}


class InvitationIssued(InvitationRead):
    """Returned once, at creation.

    The raw token is shown a single time so an administrator can pass it to the invitee.
    Only its hash is stored, so it cannot be recovered later — a lost invitation is
    reissued, not looked up.
    """

    token: str


class InvitationLookup(BaseModel):
    """Tokens travel in request bodies, never in URLs, so they stay out of browser
    history, proxy logs, and referrer headers."""

    token: str = Field(min_length=16, max_length=200)


class ActivationRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    password: str = Field(min_length=12, max_length=1024)
    # Bounded to the database column width, so an over-long name is a 422 and not a 500.
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("full_name")
    @classmethod
    def strip_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.split())
        return stripped or None


class LoginRequest(BaseModel):
    email: EmailStr
    # Bounded like the activation password: an unbounded field is free Argon2 work.
    password: str = Field(max_length=1024)


class UserRead(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class SessionRead(BaseModel):
    user: UserRead
    expires_at: datetime
