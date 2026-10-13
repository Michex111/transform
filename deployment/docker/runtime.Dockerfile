# Base runtime image for Transform cloud workspaces.
#
# Build (one image, tagged by the revision the domain expects):
#   docker build -f deployment/docker/runtime.Dockerfile \
#     -t transform-python-runtime:1.0.0-py3.14 .
#
# The tag MUST match `RuntimeConfig.base_image_revision`
# (src/domain/workspaces/value_object/runtime_config.py). The API stores that
# revision with every workspace and refuses to reuse an environment snapshot
# built from a different one, so bumping this tag is how a base-image change is
# rolled out — a floating `latest` would defeat exactly that check.
#
# Two layout decisions carry the whole persistence design; read them before
# changing anything here.
#
# 1. `/workspace` is the mount point of a per-workspace Docker volume. It holds
#    the user's files, `pyproject.toml` and `uv.lock`.
# 2. The virtual environment and the uv cache live at `/opt/venv` and
#    `/opt/uv-cache` — deliberately OUTSIDE that volume. `docker commit` snapshots
#    the container filesystem and does NOT capture volume mounts, so dependency
#    state must live in the image filesystem to be snapshot-able, while user files
#    must live in the volume to survive the container being replaced. Moving the
#    venv into `/workspace` would make environment snapshots capture nothing.
#
# Known gaps, stated rather than implied:
#   * the base image is pinned by TAG, not by digest (matching the existing
#     worker.Dockerfile). Pinning the digest is the next hardening step.
#   * this image is intentionally NOT the API/worker image: it carries a build
#     toolchain for user dependencies and none of the converter binaries.

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Both point outside /workspace on purpose -- see the header.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/opt/uv-cache \
    # `uv` resolves the project in the working directory; fail loudly rather
    # than silently creating an environment somewhere unexpected.
    UV_NO_SYNC=0 \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"

# Pinned on purpose: the resolver's behaviour and the lockfile format are part of
# an environment's identity, and a floating uv would make two workspaces created
# a week apart quietly different.
RUN pip install --no-cache-dir "uv==0.11.23"

# Build toolchain for source distributions, plus the utilities a person expects
# when they open a terminal in their workspace. No interpreters beyond 3.14: this
# image advertises one Python version, so promising another would be a lie.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        jq \
        less \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Non-root, and a fixed uid so a mounted volume's ownership is predictable.
RUN useradd --create-home --uid 10001 --shell /bin/bash appuser \
    && mkdir -p /workspace /opt/venv /opt/uv-cache \
    && chown -R appuser:appuser /workspace /opt/venv /opt/uv-cache /home/appuser

USER appuser

WORKDIR /workspace

# A long-lived idle process: the container is a host for `docker exec`, and each
# execution starts its own interpreter so no two runs share state.
# NOTE: no ENTRYPOINT, so the provider's `command` is the container's command.
CMD ["sleep", "infinity"]
