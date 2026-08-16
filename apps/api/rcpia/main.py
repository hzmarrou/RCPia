"""RCPia API entrypoint.

RCPia prepares *provisional* RCP/MDT-style outputs for urological oncology cases.
It does not replace a human RCP. PostgreSQL is the clinical source of truth.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rcpia.auth.routes import router as auth_router
from rcpia.core.config import get_settings

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


app.include_router(auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
