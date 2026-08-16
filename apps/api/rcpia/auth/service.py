"""Invitation, activation, and session logic.

RCPia is invite-only: there is no public sign-up path, and an account can only come into
existence from an invitation issued by an administrator of the organization it will join.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.auth import security
from rcpia.core.models import AuthSession, Invitation, InvitationStatus, User, UserRole

INVITATION_TTL = timedelta(days=7)
SESSION_TTL = timedelta(hours=12)


class AuthError(Exception):
    """Base class for authentication failures."""


class InvalidInvitation(AuthError):
    """The invitation does not exist, was already used, was revoked, or has expired."""


class InvalidCredentials(AuthError):
    """Wrong email or password, or a deactivated account."""


class EmailAlreadyRegistered(AuthError):
    """An account already exists for this address."""


def _now() -> datetime:
    return datetime.now(UTC)


async def create_invitation(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    role: UserRole,
    invited_by_user_id: uuid.UUID | None,
    ttl: timedelta = INVITATION_TTL,
) -> tuple[Invitation, str]:
    """Issue an invitation. Returns the record and the raw token, shown only once."""
    email = security.normalize_email(email)

    existing_user = await session.scalar(sa.select(User).where(User.email == email))
    if existing_user is not None:
        raise EmailAlreadyRegistered(f"{email} already has an account")

    token = security.generate_token()
    invitation = Invitation(
        organization_id=organization_id,
        email=email,
        role=role,
        token_hash=security.hash_token(token),
        status=InvitationStatus.pending,
        expires_at=_now() + ttl,
        invited_by_user_id=invited_by_user_id,
    )
    session.add(invitation)
    await session.flush()
    return invitation, token


async def read_invitation(session: AsyncSession, token: str) -> Invitation:
    """Resolve a raw token to a usable invitation, or raise."""
    invitation = await session.scalar(
        sa.select(Invitation).where(Invitation.token_hash == security.hash_token(token))
    )
    if invitation is None:
        raise InvalidInvitation("Unknown invitation token")
    if invitation.status is not InvitationStatus.pending:
        raise InvalidInvitation(f"Invitation is {invitation.status.value}")
    if invitation.expires_at <= _now():
        invitation.status = InvitationStatus.expired
        await session.flush()
        raise InvalidInvitation("Invitation has expired")
    return invitation


async def activate_invitation(
    session: AsyncSession, *, token: str, password: str, full_name: str | None = None
) -> User:
    """Turn an invitation into an account. This is the only way a user is created."""
    invitation = await read_invitation(session, token)

    existing_user = await session.scalar(sa.select(User).where(User.email == invitation.email))
    if existing_user is not None:
        raise EmailAlreadyRegistered(f"{invitation.email} already has an account")

    user = User(
        organization_id=invitation.organization_id,
        email=invitation.email,
        full_name=full_name,
        role=invitation.role,
        password_hash=security.hash_password(password),
        is_active=True,
    )
    session.add(user)

    invitation.status = InvitationStatus.accepted
    invitation.accepted_at = _now()
    await session.flush()
    return user


async def revoke_invitation(session: AsyncSession, invitation_id: uuid.UUID) -> None:
    result = cast(
        sa.CursorResult[Any],
        await session.execute(
            sa.update(Invitation)
            .where(Invitation.id == invitation_id, Invitation.status == InvitationStatus.pending)
            .values(status=InvitationStatus.revoked)
        ),
    )
    if not result.rowcount:
        raise InvalidInvitation("No pending invitation with that id")


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    """Verify credentials. Every failure mode raises the same error, deliberately."""
    user = await session.scalar(
        sa.select(User).where(User.email == security.normalize_email(email))
    )

    if not security.verify_password(password, user.password_hash if user else None):
        raise InvalidCredentials("Invalid email or password")

    assert user is not None  # verify_password only succeeds for a real hash
    if not user.is_active:
        raise InvalidCredentials("Invalid email or password")

    if security.needs_rehash(user.password_hash or ""):
        user.password_hash = security.hash_password(password)
        await session.flush()

    return user


async def start_session(
    session: AsyncSession,
    user: User,
    *,
    ttl: timedelta = SESSION_TTL,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[AuthSession, str]:
    """Open a session. Returns the record and the raw token for the cookie."""
    token = security.generate_token()
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=security.hash_token(token),
        expires_at=_now() + ttl,
        last_seen_at=_now(),
        user_agent=(user_agent or "")[:400] or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    session.add(auth_session)
    await session.flush()
    return auth_session, token


async def resolve_session(session: AsyncSession, token: str) -> tuple[AuthSession, User] | None:
    """Return the live session and its user, or None if the token is not usable."""
    row = (
        await session.execute(
            sa.select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == security.hash_token(token))
        )
    ).one_or_none()

    if row is None:
        return None

    auth_session, user = row
    if auth_session.revoked_at is not None or auth_session.expires_at <= _now():
        return None
    if not user.is_active:
        return None

    auth_session.last_seen_at = _now()
    return auth_session, user


async def end_session(session: AsyncSession, token: str) -> None:
    await session.execute(
        sa.update(AuthSession)
        .where(
            AuthSession.token_hash == security.hash_token(token),
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )


async def end_all_sessions_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Used when an account is deactivated: existing sessions must not outlive access."""
    result = cast(
        sa.CursorResult[Any],
        await session.execute(
            sa.update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        ),
    )
    return result.rowcount or 0
