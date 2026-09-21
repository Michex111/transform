# ============================================================================
# Runtime image (API + both workers share this image).
#
# The React SPA is NOT built into this image. It is built and hosted separately
# as a Render static site (`transform-web`, root directory `web/`) that talks to
# the API cross-origin. Keeping the Node toolchain out of this image makes API
# and worker builds faster and smaller.
#
# KNOWN REPRODUCIBILITY / RUNTIME GAPS (documented, NOT fixed in this pass —
# each needs a deliberate change and a working `docker build` to verify):
#   1. `FROM python:3.14-slim` is a floating tag. Pin by digest
#      (`python:3.14-slim@sha256:...`) so rebuilds are reproducible.
#   2. `RUN pip install uv` is unpinned. Pin the version (`pip install
#      uv==<version>`) so the resolver does not change under us.
#   3. `uv sync --frozen --no-dev || uv sync --no-dev` silently re-resolves when
#      uv.lock is stale, which defeats the lock. Prefer `uv sync --frozen
#      --no-dev` alone and fail the build on drift, or gate the fallback behind
#      an explicit build arg.
#   4. `CMD ["uv", "run", ...]` re-syncs at container start, making startup
#      depend on the network and able to rewrite /app/.venv. Prefer
#      `CMD ["python", "-m", "workers.converter_workers.main"]` with the venv on
#      PATH (`ENV PATH="/app/.venv/bin:$PATH"`).
# The `||` fallback and CMD are intentionally left alone: a broken build here is
# not recoverable in this pass.
# ============================================================================
FROM python:3.14-slim

# Unbuffered stdout/stderr: without this, Python buffers log output and a
# SIGKILL (docker stop past stop_grace_period, OOM) discards the last records —
# exactly the ones needed to diagnose why the container died.
ENV PYTHONUNBUFFERED=1

# Install system dependencies for all converter types
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    libreoffice-writer \
    pandoc \
    ffmpeg \
    calibre \
    libcairo2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

RUN pip install uv

# Install dependencies
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Copy application code
COPY . .

ENV PYTHONPATH=/app/src:/app

# Run as a non-root user (security hardening)
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Default command (overridable)
CMD ["uv", "run", "python", "-m", "workers.converter_workers.main"]