"""RCPia API entrypoint.

RCPia prepares *provisional* RCP/MDT-style outputs for urological oncology cases.
It does not replace a human RCP. PostgreSQL is the clinical source of truth.
"""

from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from rcpia.agents.routes import router as analysis_router
from rcpia.auth.routes import router as auth_router
from rcpia.cases.ledger_routes import router as ledger_router
from rcpia.cases.routes import router as cases_router
from rcpia.cases.source_routes import router as sources_router
from rcpia.core.config import get_settings
from rcpia.pps.routes import router as pps_router

settings = get_settings()

app = FastAPI(
    title="RCPia API",
    version="0.1.0",
    description="Provisional RCP preparation support. All outputs require human review.",
    # Outside development the schema is not published: it enumerates every endpoint of a
    # clinical system to anyone who asks.
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# Local development only. Tighten per-environment before any deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_store_for_private_routes(request: Request, call_next: Callable) -> Response:
    """Keep credentials and case responses out of caches and referrers.

    Applied as middleware rather than per route because headers set on an injected
    Response are lost when the handler raises: a failed login or a rejected invitation
    would otherwise answer without these headers, which are exactly the responses whose
    URLs and bodies should not be retained.
    """
    response: Response = await call_next(request)
    if (
        request.url.path.startswith("/auth")
        or request.url.path == "/cases"
        or request.url.path.startswith("/cases/")
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


app.include_router(auth_router)
app.include_router(cases_router)
app.include_router(ledger_router)
app.include_router(sources_router)
app.include_router(analysis_router)
app.include_router(pps_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
