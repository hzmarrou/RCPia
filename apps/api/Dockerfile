# RCPia API image. Python deps are installed with uv (no pip).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Optional private/corporate PyPI index (e.g. an internal package feed proxy).
# Leave empty to use the public index.
ARG UV_DEFAULT_INDEX=https://pypi.org/simple

ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependency layer: cached until pyproject.toml/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Application layer.
# Runtime image ships application code only; tests stay out of the image.
COPY README.md ./
COPY rcpia ./rcpia
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uvicorn", "rcpia.main:app", "--host", "0.0.0.0", "--port", "8000"]
