# ============================================================================
# Stage 1 - build the frontend SPA.
# `web/dist` is gitignored, so a fresh clone (e.g. on a PaaS builder) has no
# built assets. Building it here means the runtime image can serve the UI.
# ============================================================================
FROM node:22-slim AS frontend

WORKDIR /web

# Install dependencies first so this layer caches across source-only changes.
COPY web/package.json web/package-lock.json ./
RUN npm ci

# Build the SPA (`tsc -b && vite build`). The app is served same-origin as the
# API, so the default VITE_API_BASE_URL of "/api" is already correct.
COPY web/ ./
RUN npm run build

# ============================================================================
# Stage 2 - runtime image (API + both workers share this image).
# ============================================================================
FROM python:3.14-slim

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

# Bring in the built SPA so the API serves the UI at / (FRONTEND_DIST_DIR
# resolves to /app/web/dist). Without this the API boots normally but / returns
# a JSON 404, because web/dist is gitignored and absent from a fresh clone.
COPY --from=frontend /web/dist ./web/dist

ENV PYTHONPATH=/app/src:/app

# Run as a non-root user (security hardening)
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Default command (overridable)
CMD ["uv", "run", "python", "-m", "workers.converter_workers.main"]