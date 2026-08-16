"""Guard against fail-open routing.

Protection is opt-in, so the risk is a future router that simply forgets it. This test
walks the live application and requires every route to be either explicitly public or
behind an authentication dependency. Adding a clinical route without protection fails
here rather than in production.
"""

from fastapi.routing import APIRoute

from rcpia.auth.dependencies import current_user, protected_router
from rcpia.core.models import UserRole
from rcpia.main import app

# Routes that must remain reachable without a session, with the reason they are safe.
PUBLIC_ROUTES = {
    ("/health", "GET"),  # liveness probe, no data
    ("/auth/login", "POST"),  # the way in
    ("/auth/logout", "POST"),  # must work with an expired or forged cookie
    ("/auth/activate", "POST"),  # invitation token is the credential
    ("/auth/invitations/lookup", "POST"),  # invitation token is the credential
    ("/docs", "GET"),
    ("/docs/oauth2-redirect", "GET"),
    ("/redoc", "GET"),
    ("/openapi.json", "GET"),
}


def api_routes(routes: object) -> list[APIRoute]:
    """Collect every APIRoute, descending into included routers.

    FastAPI keeps an included router as a single nested object rather than flattening
    its routes into `app.routes`, so a non-recursive walk sees only the routes declared
    directly on the application — and would pass while missing everything that matters.
    """
    found: list[APIRoute] = []
    for route in routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute):
            found.append(route)
            continue

        # FastAPI >= 0.141 stores an included router as `_IncludedRouter`, which keeps
        # the original APIRouter rather than exposing `.routes` directly.
        included = getattr(route, "original_router", None)
        nested = getattr(included, "routes", None) or getattr(route, "routes", None)
        if nested:
            found.extend(api_routes(nested))
    return found


def _is_protected(route: APIRoute) -> bool:
    """A route is protected when resolving the current user is somewhere in its graph."""
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if dependency.call is current_user:
            return True
        stack.extend(dependency.dependencies)
    return False


def test_the_walk_actually_finds_the_routes() -> None:
    """Guard the guard: a walk that finds nothing would pass every check below."""
    paths = {route.path for route in api_routes(app.routes)}

    assert "/health" in paths
    assert "/auth/login" in paths
    assert "/auth/me" in paths


def test_every_route_is_protected_or_explicitly_public() -> None:
    unprotected: list[str] = []

    for route in api_routes(app.routes):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            if (route.path, method) in PUBLIC_ROUTES:
                continue
            if not _is_protected(route):
                unprotected.append(f"{method} {route.path}")

    assert not unprotected, (
        "These routes have no authentication dependency. Put them behind "
        "protected_router(...) or add them to PUBLIC_ROUTES with a justification: "
        f"{unprotected}"
    )


def test_public_allowlist_has_no_stale_entries() -> None:
    """A removed or renamed route must not leave a permanent hole in the allowlist."""
    live = {(route.path, method) for route in api_routes(app.routes) for method in route.methods}
    # FastAPI's own documentation routes are plain Starlette routes, not APIRoutes.
    documentation = {
        ("/docs", "GET"),
        ("/docs/oauth2-redirect", "GET"),
        ("/redoc", "GET"),
        ("/openapi.json", "GET"),
    }

    stale = PUBLIC_ROUTES - live - documentation
    assert not stale, f"PUBLIC_ROUTES lists routes that no longer exist: {stale}"


def test_protected_router_applies_its_role_to_every_route() -> None:
    router = protected_router(UserRole.uploader, prefix="/example")

    @router.get("/thing")
    async def _thing() -> dict[str, str]:  # pragma: no cover - never called
        return {}

    route = next(r for r in router.routes if isinstance(r, APIRoute))
    assert _is_protected(route)


def test_protected_router_requires_at_least_one_role() -> None:
    try:
        protected_router()
    except ValueError:
        return
    raise AssertionError("protected_router() must refuse to build an unprotected router")
