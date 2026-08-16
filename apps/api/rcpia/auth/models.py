"""Request and response models for the auth API.

The database tables (`users`, `invitations`, `auth_sessions`) live in
`rcpia.core.models`; these are the shapes crossing the HTTP boundary.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

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


class ActivationRequest(BaseModel):
    token: str
    password: str = Field(min_length=12)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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
