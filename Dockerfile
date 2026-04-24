# syntax=docker/dockerfile:1.7
# FILE: Dockerfile
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Multi-stage Docker build for the brainstorm MCP sub-agent service.
#          Produces a minimal python:3.12-slim runtime image with the brainstorm
#          FastAPI application running under a non-root uid 10001.
# SCOPE: Two-stage build: builder (compiles/installs deps with build-essential) +
#        runtime (copies installed packages, app source, sets security hardening).
# INPUT: requirements.txt (pinned deps), src/ (application source).
# OUTPUT: OCI image exposing port 8000; CMD runs uvicorn with --factory --workers 1.
# KEYWORDS: [DOMAIN(10): Deployment; TECH(10): Docker_Multistage; TECH(9): uvicorn_factory;
#            CONCEPT(9): NonRootContainer; CONCEPT(9): PinnedDeps; TECH(8): python3.12-slim]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.4 (Slice D scope), §9.5 (workers hardlock),
#   §6 positive invariants I1 (stdout only), I2 (exact CMD).
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - I2: CMD is exactly ["uvicorn","src.server.app_factory:create_app","--factory",
#        "--host","0.0.0.0","--port","8000","--workers","1"] — NEVER change worker count.
# - Non-root user uid/gid 10001 (brainstorm); USER directive precedes CMD.
# - /data is a named VOLUME for sqlite checkpoint persistence.
# - HEALTHCHECK probes /healthz (liveness) on the container-local port 8000.
# - I1: Application logs to stdout only (PYTHONUNBUFFERED=1); no log files created in /data.
# - PYTHONDONTWRITEBYTECODE=1 keeps the image clean.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why two stages?
# A: build-essential (~200 MB) is only needed to compile C-extensions in some deps.
#    The runtime stage copies only the installed .local packages, dropping the compiler
#    and keeping the final image small (typically ~350 MB vs ~550 MB single-stage).
# Q: Why --workers 1 hardlocked?
# A: sqlite is a single-writer database. Multiple uvicorn workers sharing the same
#    sqlite file would cause WAL lock contention and potential checkpoint corruption.
#    LangGraph in-memory state is also not shared across processes.
#    Scale-out requires switching to the Postgres checkpointer (see BACKLOG.md).
# Q: Why uid 10001?
# A: Kubernetes PSP / Pod Security Standards (restricted) require runAsNonRoot.
#    uid 10001 avoids collision with common system UIDs (1000 = ubuntu user) and
#    is documented in k8s/deployment.yaml securityContext for k8s parity.
# Q: Why VOLUME ["/data"]?
# A: Declares the mount point for sqlite checkpoints. In k8s, the StatefulSet
#    volumeClaimTemplate mounts a PVC here. In local dev, a bind-mount supplies it.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice D: multi-stage build, non-root uid 10001,
#               --workers 1 hardlock, /healthz probe, /data volume.]
# END_CHANGE_SUMMARY

# =============================================================================
# Stage 1: builder — install Python dependencies using build-essential
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build tools required by some Python packages (e.g. aiosqlite C-ext, cryptography).
# Cleaned up immediately to reduce layer size.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install all pinned deps into the user-local path so the runtime stage can COPY
# /root/.local without carrying pip itself or the build toolchain.
RUN pip install --no-cache-dir --user -r requirements.txt

# =============================================================================
# Stage 2: runtime — minimal image with non-root user and app source
# =============================================================================
FROM python:3.12-slim AS runtime

# Create non-root group and user (uid/gid 10001) matching k8s securityContext.
# --no-log-init prevents large sparse lastlog files; /usr/sbin/nologin forbids shell login.
RUN groupadd --gid 10001 brainstorm \
    && useradd --uid 10001 --gid brainstorm --shell /usr/sbin/nologin --create-home brainstorm

WORKDIR /app

# Copy installed packages from the builder stage into the brainstorm user's home.
COPY --from=builder /root/.local /home/brainstorm/.local

# Runtime environment:
#   PATH           — ensures pip-installed CLI entry-points are found
#   PYTHONUNBUFFERED=1  — stdout/stderr flushed immediately (I1: stdout-only logging)
#   PYTHONDONTWRITEBYTECODE=1 — no .pyc files written (cleaner image + read-only FS compat)
#   BRAINSTORM_SQLITE_PATH — default sqlite path inside the /data volume
ENV PATH="/home/brainstorm/.local/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BRAINSTORM_SQLITE_PATH=/data/checkpoints.sqlite

# Copy application source (chown to brainstorm so the non-root process can read it).
COPY --chown=brainstorm:brainstorm src/ /app/src/

# Create the /data directory for sqlite persistence and give it to brainstorm.
# In k8s the StatefulSet PVC is mounted here; in local dev a bind-mount or named volume.
RUN mkdir -p /data && chown brainstorm:brainstorm /data

# Drop to non-root before declaring the volume so the volume inherits correct ownership.
USER 10001:10001

# Declare /data as a named volume mount-point (sqlite checkpoints persist here).
VOLUME ["/data"]

# Expose the uvicorn HTTP port.
EXPOSE 8000

# Liveness probe: verifies the application is alive by hitting /healthz.
# --start-period=10s: grace period for startup (checkpointer setup + sweeper init).
# --interval=15s: check every 15 seconds.
# --timeout=3s: fail the check if no response within 3 seconds.
# --retries=3: declare unhealthy after 3 consecutive failures.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2); sys.exit(0 if r.status==200 else 1)" || exit 1

# I2 — INVARIANT: CMD is hardlocked to exactly these arguments.
# --factory: uvicorn calls create_app() to obtain the FastAPI instance (no module-level app).
# --workers 1: sqlite single-writer + LangGraph in-memory state require single worker.
#              DO NOT increase without switching to Postgres checkpointer (see BACKLOG.md).
CMD ["uvicorn","src.server.app_factory:create_app","--factory","--host","0.0.0.0","--port","8000","--workers","1"]
