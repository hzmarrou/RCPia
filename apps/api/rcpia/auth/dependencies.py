"""FastAPI dependencies for authentication and role checks.

Clinical routes depend on `require_role(...)`. Anything not behind one of these
dependencies is public, so new routers must opt in explicitly.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Cookie, Depends, HTTPException, Request, status
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


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
