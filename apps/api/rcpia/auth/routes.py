"""Authentication endpoints.

Public: login, logout, invitation lookup, activation. Everything else requires a session,
and clinical data requires a role.
"""

import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status

from rcpia.auth import service
from rcpia.auth.dependencies import (
    CurrentUser,
    RequireAdmin,
    RequireUploader,
    SessionDep,
    client_ip,
)
from rcpia.auth.models import (
    SESSION_COOKIE_NAME,
    ActivationRequest,
    InvitationCreate,
    InvitationIssued,
    InvitationRead,
    LoginRequest,
    UserRead,
)
from rcpia.auth.security import WeakPasswordError
from rcpia.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age_seconds,
        httponly=True,
        samesite="lax",
        # Plain HTTP is only tolerable on a developer machine.
        secure=settings.app_env != "local",
        path="/",
    )


@router.post(
    "/invitations", response_model=InvitationIssued, status_code=status.HTTP_201_CREATED
)
async def create_invitation(
    payload: InvitationCreate, session: SessionDep, admin: RequireAdmin
) -> InvitationIssued:
    """Issue an invitation for the administrator's own organization."""
    try:
        invitation, token = await service.create_invitation(
            session,
            organization_id=admin.organization_id,
            email=payload.email,
            role=payload.role,
            invited_by_user_id=admin.id,
        )
    except service.EmailAlreadyRegistered as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await session.commit()
    return InvitationIssued(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        token=token,
    )


@router.get("/invitations/{token}", response_model=InvitationRead)
async def read_invitation(token: str, session: SessionDep) -> InvitationRead:
    """Let the activation page show who the invitation is for before a password is set."""
    try:
        invitation = await service.read_invitation(session, token)
    except service.InvalidInvitation as exc:
        await session.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return InvitationRead.model_validate(invitation)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID, session: SessionDep, admin: RequireAdmin
) -> None:
    try:
        await service.revoke_invitation(session, invitation_id)
    except service.InvalidInvitation as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await session.commit()


@router.post("/activate", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def activate(
    payload: ActivationRequest, response: Response, request: Request, session: SessionDep
) -> UserRead:
    """Create an account from an invitation and log the new user straight in."""
    try:
        user = await service.activate_invitation(
            session,
            token=payload.token,
            password=payload.password,
            full_name=payload.full_name,
        )
    except service.InvalidInvitation as exc:
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except service.EmailAlreadyRegistered as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    auth_session, token = await service.start_session(
        session,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    await session.commit()

    _set_session_cookie(response, token, int(service.SESSION_TTL.total_seconds()))
    return UserRead.model_validate(user)


@router.post("/login", response_model=UserRead)
async def login(
    payload: LoginRequest, response: Response, request: Request, session: SessionDep
) -> UserRead:
    try:
        user = await service.authenticate(
            session, email=payload.email, password=payload.password
        )
    except service.InvalidCredentials as exc:
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password") from exc

    _, token = await service.start_session(
        session,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    await session.commit()

    _set_session_cookie(response, token, int(service.SESSION_TTL.total_seconds()))
    return UserRead.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, response: Response, session: SessionDep, user: CurrentUser
) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await service.end_session(session, token)
        await session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.get("/dashboard", response_model=UserRead)
async def dashboard(user: RequireUploader) -> UserRead:
    """Stand-in for the case dashboard. It exists now so that role-based protection is
    exercised before Phase 4 adds the real clinical routes."""
    return UserRead.model_validate(user)
