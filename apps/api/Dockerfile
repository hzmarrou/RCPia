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
# **`--extra azure` is not optional for a container image.** The extra exists so an
# on-premises install can skip the SDK, and `rcpia.storage.azure_blob` imports it lazily
# so that skipping works. An image, though, is built once and may run against either
# provider: leaving it out produced a healthy API that accepted an upload and answered
# 500 with `No module named 'azure'` from inside `put_bytes`, after the case row had
# already been created. A few megabytes buys one image that runs in both places.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra azure

# Application layer.
# Runtime image ships application code only; tests stay out of the image.
COPY README.md ./
COPY rcpia ./rcpia
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra azure

EXPOSE 8000

CMD ["uvicorn", "rcpia.main:app", "--host", "0.0.0.0", "--port", "8000"]
