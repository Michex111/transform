# ============================================================================
# Runtime image (API + both workers share this image).
#
# The React SPA is NOT built into this image. It is built and hosted separately
# as a Render static site (`transform-web`, root directory `web/`) that talks to
# the API cross-origin. Keeping the Node toolchain out of this image makes API
# and worker builds faster and smaller.
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

ENV PYTHONPATH=/app/src:/app

# Run as a non-root user (security hardening)
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Default command (overridable)
CMD ["uv", "run", "python", "-m", "workers.converter_workers.main"]