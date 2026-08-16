"""Invite-only authentication tests.

Three properties matter clinically: nobody can create an account without an invitation,
a session can be revoked immediately, and clinical routes refuse callers without the
right role.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.auth import security, service
from rcpia.auth.models import SESSION_COOKIE_NAME
from rcpia.auth.throttle import LOGIN_ATTEMPT_LIMIT, login_limiter
from rcpia.core.db import get_session
from rcpia.core.models import (
    AuthSession,
    Invitation,
    InvitationStatus,
    Organization,
    User,
    UserRole,
)
from rcpia.main import app

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def reset_login_throttle():
    """The limiter is process-global; tests would otherwise throttle each other."""
    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]):
    """An HTTP client wired to the test database instead of the application's own."""

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    app.dependency_overrides.clear()


async def make_admin(
    factory: async_sessionmaker[AsyncSession], *, role: UserRole = UserRole.admin
) -> tuple[User, uuid.UUID]:
    async with factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:10]}")
        session.add(organization)
        await session.flush()
        user = User(
            organization_id=organization.id,
            email=f"admin-{uuid.uuid4().hex[:8]}@example.org",
            role=role,
            password_hash=security.hash_password(PASSWORD),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user, organization.id


async def login(client: AsyncClient, email: str, password: str = PASSWORD):
    return await client.post("/auth/login", json={"email": email, "password": password})


# --------------------------------------------------------------------------------------
# Password and token handling
# --------------------------------------------------------------------------------------


def test_password_hashing_round_trip() -> None:
    hashed = security.hash_password(PASSWORD)
    assert hashed != PASSWORD
    assert security.verify_password(PASSWORD, hashed)
    assert not security.verify_password("wrong-password-entirely", hashed)


def test_password_policy_rejects_short_passwords() -> None:
    with pytest.raises(security.WeakPasswordError):
        security.hash_password("short")


def test_verify_password_handles_users_without_a_hash() -> None:
    assert not security.verify_password(PASSWORD, None)
    assert not security.verify_password(PASSWORD, "")


def test_tokens_are_stored_only_as_hashes() -> None:
    token = security.generate_token()
    digest = security.hash_token(token)
    assert token not in digest
    assert security.tokens_match(token, digest)
    assert not security.tokens_match(security.generate_token(), digest)


# --------------------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------------------


async def test_admin_can_invite_and_the_token_is_shown_once(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)

    response = await client.post(
        "/auth/invitations", json={"email": "New.Doctor@example.org", "role": "uploader"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.doctor@example.org", "addresses are normalized"
    assert body["role"] == "uploader"
    assert body["token"]

    async with session_factory() as session:
        stored = await session.scalar(
            sa.select(Invitation).where(Invitation.id == uuid.UUID(body["id"]))
        )
        assert stored is not None
        assert stored.token_hash == security.hash_token(body["token"])
        assert body["token"] not in stored.token_hash


async def test_non_admin_cannot_invite(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uploader, _ = await make_admin(session_factory, role=UserRole.uploader)
    await login(client, uploader.email)

    response = await client.post("/auth/invitations", json={"email": "x@example.org"})

    assert response.status_code == 403


async def test_anonymous_cannot_invite(client: AsyncClient) -> None:
    response = await client.post("/auth/invitations", json={"email": "x@example.org"})
    assert response.status_code == 401


async def test_inviting_an_existing_account_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)

    response = await client.post("/auth/invitations", json={"email": admin.email})

    assert response.status_code == 409


async def test_invitation_can_be_revoked_and_then_fails_activation(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post("/auth/invitations", json={"email": "revoked@example.org"})
    ).json()

    revoked = await client.delete(f"/auth/invitations/{issued['id']}")
    assert revoked.status_code == 204

    activation = await client.post(
        "/auth/activate", json={"token": issued["token"], "password": PASSWORD}
    )
    assert activation.status_code == 400


async def test_expired_invitation_cannot_be_used(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin, organization_id = await make_admin(session_factory)

    async with session_factory() as session:
        _, token = await service.create_invitation(
            session,
            organization_id=organization_id,
            email="late@example.org",
            role=UserRole.uploader,
            invited_by_user_id=admin.id,
            ttl=timedelta(seconds=-1),
        )
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(service.InvalidInvitation):
            await service.read_invitation(session, token)
        await session.commit()

    async with session_factory() as session:
        invitation = await session.scalar(
            sa.select(Invitation).where(Invitation.email == "late@example.org")
        )
        assert invitation is not None
        assert invitation.status is InvitationStatus.expired


# --------------------------------------------------------------------------------------
# Activation — the only way an account is created
# --------------------------------------------------------------------------------------


async def test_activation_creates_the_account_and_logs_in(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, organization_id = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post(
            "/auth/invitations", json={"email": "invitee@example.org", "role": "uploader"}
        )
    ).json()
    await client.post("/auth/logout")

    response = await client.post(
        "/auth/activate",
        json={"token": issued["token"], "password": PASSWORD, "full_name": "Dr Invitee"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "invitee@example.org"
    assert body["role"] == "uploader"
    assert body["organization_id"] == str(organization_id), "joins the inviting organization"
    assert client.cookies.get(SESSION_COOKIE_NAME), "activation logs the new user in"

    assert (await client.get("/auth/me")).json()["email"] == "invitee@example.org"


async def test_uninvited_visitor_cannot_create_an_account(client: AsyncClient) -> None:
    """There is no public sign-up path: a fabricated token is the only thing to try."""
    response = await client.post(
        "/auth/activate", json={"token": security.generate_token(), "password": PASSWORD}
    )

    assert response.status_code == 400
    assert not client.cookies.get(SESSION_COOKIE_NAME)


async def test_invitation_cannot_be_used_twice(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post("/auth/invitations", json={"email": "once@example.org"})
    ).json()
    await client.post("/auth/logout")

    first = await client.post(
        "/auth/activate", json={"token": issued["token"], "password": PASSWORD}
    )
    assert first.status_code == 201

    second = await client.post(
        "/auth/activate", json={"token": issued["token"], "password": PASSWORD}
    )
    assert second.status_code == 400


async def test_activation_rejects_an_over_long_name(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The column is VARCHAR(200); an over-long name is a validation error, not a 500."""
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post("/auth/invitations", json={"email": "long@example.org"})
    ).json()

    response = await client.post(
        "/auth/activate",
        json={"token": issued["token"], "password": PASSWORD, "full_name": "x" * 201},
    )

    assert response.status_code == 422


async def test_activation_normalizes_whitespace_in_the_name(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post("/auth/invitations", json={"email": "spaces@example.org"})
    ).json()
    await client.post("/auth/logout")

    response = await client.post(
        "/auth/activate",
        json={"token": issued["token"], "password": PASSWORD, "full_name": "  Dr   Spaces  "},
    )

    assert response.status_code == 201
    assert response.json()["full_name"] == "Dr Spaces"


async def test_revocation_during_activation_cannot_produce_an_account(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The invitation is consumed by one conditional UPDATE, so revoke and activate
    cannot both succeed."""
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post("/auth/invitations", json={"email": "race@example.org"})
    ).json()

    async with session_factory() as session:
        await service.revoke_invitation(
            session, uuid.UUID(issued["id"]), organization_id=admin.organization_id
        )
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(service.InvalidInvitation):
            await service.activate_invitation(
                session, token=issued["token"], password=PASSWORD
            )
        await session.rollback()

    async with session_factory() as session:
        created = await session.scalar(sa.select(User).where(User.email == "race@example.org"))
        assert created is None


async def test_activation_rejects_a_weak_password(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post("/auth/invitations", json={"email": "weak@example.org"})
    ).json()

    response = await client.post(
        "/auth/activate", json={"token": issued["token"], "password": "short"}
    )

    assert response.status_code == 422


async def test_invitation_lookup_shows_who_it_is_for(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    issued = (
        await client.post("/auth/invitations", json={"email": "lookup@example.org"})
    ).json()

    response = await client.post(
        "/auth/invitations/lookup", json={"token": issued["token"]}
    )

    assert response.status_code == 200
    assert response.json()["email"] == "lookup@example.org"
    assert "token" not in response.json()


async def test_unknown_invitation_lookup_is_not_found(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/invitations/lookup", json={"token": security.generate_token()}
    )
    assert response.status_code == 404


async def test_invitation_material_is_not_cached_or_referred(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Tokens travel in bodies; responses carrying them must not be cached or leak a
    referrer to whatever page the user visits next."""
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)

    response = await client.post("/auth/invitations", json={"email": "cache@example.org"})

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_admin_cannot_revoke_another_organizations_invitation(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Tenancy: an id alone must never be enough to act on another organization's data."""
    victim_admin, _ = await make_admin(session_factory)
    await login(client, victim_admin.email)
    victim_invitation = (
        await client.post("/auth/invitations", json={"email": "victim@example.org"})
    ).json()
    await client.post("/auth/logout")

    attacker_admin, _ = await make_admin(session_factory)
    await login(client, attacker_admin.email)

    response = await client.delete(f"/auth/invitations/{victim_invitation['id']}")

    assert response.status_code == 404
    async with session_factory() as session:
        untouched = await session.get(Invitation, uuid.UUID(victim_invitation["id"]))
        assert untouched is not None
        assert untouched.status is InvitationStatus.pending, "the invitation still works"


# --------------------------------------------------------------------------------------
# Login and sessions
# --------------------------------------------------------------------------------------


async def test_login_sets_an_httponly_session_cookie(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)

    response = await login(client, admin.email)

    assert response.status_code == 200
    cookie_header = response.headers.get("set-cookie", "")
    assert "httponly" in cookie_header.lower()
    assert "samesite=lax" in cookie_header.lower()


async def test_login_rejects_a_wrong_password(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)

    response = await login(client, admin.email, "not-the-right-password")

    assert response.status_code == 401
    assert not client.cookies.get(SESSION_COOKIE_NAME)


async def test_login_for_an_unknown_email_looks_identical(client: AsyncClient) -> None:
    response = await login(client, "ghost@example.org")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


async def test_deactivated_user_cannot_log_in(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    async with session_factory() as session:
        await session.execute(sa.update(User).where(User.id == admin.id).values(is_active=False))
        await session.commit()

    assert (await login(client, admin.email)).status_code == 401


async def test_session_cookie_stores_only_a_hash(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None

    async with session_factory() as session:
        stored = await session.scalar(sa.select(AuthSession).where(AuthSession.user_id == admin.id))
        assert stored is not None
        assert stored.token_hash == security.hash_token(token)
        assert stored.token_hash != token


async def test_logout_revokes_the_session_immediately(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    token = client.cookies.get(SESSION_COOKIE_NAME)

    assert (await client.post("/auth/logout")).status_code == 204

    # Even replaying the original cookie fails: the session is revoked server-side.
    client.cookies.set(SESSION_COOKIE_NAME, token or "")
    assert (await client.get("/auth/me")).status_code == 401


async def test_expired_session_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)

    async with session_factory() as session:
        await session.execute(
            sa.update(AuthSession)
            .where(AuthSession.user_id == admin.id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    assert (await client.get("/auth/me")).status_code == 401


async def test_deactivating_a_user_kills_live_sessions(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)
    assert (await client.get("/auth/me")).status_code == 200

    async with session_factory() as session:
        await session.execute(sa.update(User).where(User.id == admin.id).values(is_active=False))
        revoked = await service.end_all_sessions_for_user(session, admin.id)
        await session.commit()

    assert revoked == 1
    assert (await client.get("/auth/me")).status_code == 401


async def test_forged_session_cookie_is_rejected(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, security.generate_token())
    assert (await client.get("/auth/me")).status_code == 401


async def test_logout_works_without_a_valid_session(client: AsyncClient) -> None:
    """Someone holding a stale cookie still needs a way to be rid of it."""
    client.cookies.set(SESSION_COOKIE_NAME, security.generate_token())

    response = await client.post("/auth/logout")

    assert response.status_code == 204
    assert "rcpia_session=" in response.headers.get("set-cookie", "")


async def test_login_is_throttled_after_repeated_failures(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)

    for _ in range(LOGIN_ATTEMPT_LIMIT):
        assert (await login(client, admin.email, "wrong-password-here")).status_code == 401

    throttled = await login(client, admin.email, "wrong-password-here")

    assert throttled.status_code == 429
    assert throttled.headers["retry-after"]
    # The correct password is refused too: the throttle protects the account, not the
    # guess. It is scoped to this address, so other users are unaffected.
    assert (await login(client, admin.email)).status_code == 429


async def test_throttling_is_scoped_per_account(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    victim, _ = await make_admin(session_factory)
    other, _ = await make_admin(session_factory)

    for _ in range(LOGIN_ATTEMPT_LIMIT + 1):
        await login(client, victim.email, "wrong-password-here")

    assert (await login(client, other.email)).status_code == 200


# --------------------------------------------------------------------------------------
# Route protection
# --------------------------------------------------------------------------------------


async def test_public_endpoints_need_no_session(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


async def test_dashboard_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/auth/dashboard")).status_code == 401


async def test_uploader_can_reach_the_dashboard(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uploader, _ = await make_admin(session_factory, role=UserRole.uploader)
    await login(client, uploader.email)

    response = await client.get("/auth/dashboard")

    assert response.status_code == 200
    assert response.json()["email"] == uploader.email


async def test_viewer_cannot_reach_the_uploader_dashboard(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    viewer, _ = await make_admin(session_factory, role=UserRole.viewer)
    await login(client, viewer.email)

    assert (await client.get("/auth/dashboard")).status_code == 403


async def test_admin_can_reach_the_uploader_dashboard(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin, _ = await make_admin(session_factory)
    await login(client, admin.email)

    assert (await client.get("/auth/dashboard")).status_code == 200


# --------------------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------------------


async def test_bootstrap_creates_the_first_administrator(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The only account creation path that needs no invitation."""
    from rcpia.auth import bootstrap as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "get_session_factory", lambda: session_factory)

    name = f"CHU {uuid.uuid4().hex[:6]}"
    email = f"First-Admin-{uuid.uuid4().hex[:6]}@example.org"

    user = await bootstrap_module.bootstrap(name, email, PASSWORD)

    assert user.role is UserRole.admin
    assert user.email == email.lower()

    async with session_factory() as session:
        created = await session.scalar(sa.select(User).where(User.email == email.lower()))
        assert created is not None
        assert security.verify_password(PASSWORD, created.password_hash)
        organization = await session.get(Organization, created.organization_id)
        assert organization is not None and organization.name == name


async def test_bootstrap_refuses_to_duplicate_an_existing_user(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rcpia.auth import bootstrap as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "get_session_factory", lambda: session_factory)
    admin, _ = await make_admin(session_factory)

    with pytest.raises(SystemExit):
        await bootstrap_module.bootstrap("Another Org", admin.email, PASSWORD)
