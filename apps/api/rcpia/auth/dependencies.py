"""FastAPI dependencies for authentication and role checks.

Route protection is opt-in, which is fail-open by nature: a new router that forgets its
dependency is public. Two things guard against that. Clinical routers are built with
`protected_router(...)`, which attaches the role check to every route in the router
rather than to each function, and `tests/test_route_protection.py` walks the live
application and fails if any route outside an explicit public allowlist has no
authentication dependency.
"""

from collections.abc import Callable, Coroutine, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.auth import service
from rcpia.auth.models import SESSION_COOKIE_NAME
from rcpia.core.db import get_session
from rcpia.core.models import User, UserRole

SessionDep = Annotated[AsyncSession, Depends(get_session)]

NOT_AUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
)
FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
)


async def current_user(
    session: SessionDep,
    rcpia_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> User:
    if not rcpia_session:
        raise NOT_AUTHENTICATED

    resolved = await service.resolve_session(session, rcpia_session)
    if resolved is None:
        raise NOT_AUTHENTICATED

    _, user = resolved
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(
    *roles: UserRole,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    """Allow only the listed roles. Admins are never implicitly granted other roles —
    each route states exactly who may call it."""

    async def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise FORBIDDEN
        return user

    return dependency


RequireAdmin = Annotated[User, Depends(require_role(UserRole.admin))]
RequireUploader = Annotated[User, Depends(require_role(UserRole.admin, UserRole.uploader))]
RequireViewer = Annotated[
    User, Depends(require_role(UserRole.admin, UserRole.uploader, UserRole.viewer))
]


def protected_router(
    *roles: UserRole, prefix: str = "", tags: Sequence[str] | None = None
) -> APIRouter:
    """Build a router whose every route requires one of `roles`.

    Preferred over decorating each endpoint: a route added later inherits the protection
    instead of silently being public.
    """
    if not roles:
        raise ValueError("A protected router must name at least one role")
    return APIRouter(
        prefix=prefix, tags=list(tags or []), dependencies=[Depends(require_role(*roles))]
    )


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
