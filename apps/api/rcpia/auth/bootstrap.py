"""Create the first organization and administrator.

Invite-only auth has a chicken-and-egg problem: invitations come from an administrator,
and administrators come from invitations. This command breaks the cycle, and is the only
path that creates a user without an invitation. Run it once per deployment:

    uv run python -m rcpia.auth.bootstrap "CHU Mohammed VI" admin@example.org

The password is read from RCPIA_BOOTSTRAP_PASSWORD, or prompted for interactively.
"""

import argparse
import asyncio
import getpass
import os
import sys

import sqlalchemy as sa

from rcpia.auth import security
from rcpia.core.db import get_session_factory
from rcpia.core.models import Organization, User, UserRole


async def bootstrap(organization_name: str, email: str, password: str) -> User:
    email = security.normalize_email(email)
    factory = get_session_factory()

    async with factory() as session:
        if await session.scalar(sa.select(User).where(User.email == email)):
            raise SystemExit(f"A user already exists for {email}")

        organization = await session.scalar(
            sa.select(Organization).where(Organization.name == organization_name)
        )
        if organization is None:
            organization = Organization(name=organization_name)
            session.add(organization)
            await session.flush()

        user = User(
            organization_id=organization.id,
            email=email,
            role=UserRole.admin,
            password_hash=security.hash_password(password),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the first RCPia administrator.")
    parser.add_argument("organization", help="Organization name, e.g. 'CHU Mohammed VI'")
    parser.add_argument("email", help="Administrator email address")
    args = parser.parse_args(argv)

    password = os.environ.get("RCPIA_BOOTSTRAP_PASSWORD") or getpass.getpass(
        "Administrator password: "
    )

    try:
        security.validate_password(password)
    except security.WeakPasswordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    user = asyncio.run(bootstrap(args.organization, args.email, password))
    print(f"Created administrator {user.email} in organization {args.organization!r}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
