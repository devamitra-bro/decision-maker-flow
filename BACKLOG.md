# BACKLOG.md — Brainstorm MCP Sub-Agent

Deferred items with one-line rationale. These are NOT TODOs in production code (per constraint #9).
Items here are tracked for future implementation cycles.

---

## Deferred Items

- **Postgres production readiness (multi-replica, schema migrations, connection pooling).**
  SQLite MVP is single-pod only. Postgres checkpointer exists (`langgraph-checkpoint-postgres==2.0.2`) but is marked EXPERIMENTAL; production use requires Alembic migrations, pooling (asyncpg/pgbouncer), and `replicas > 1` validation against LangGraph in-memory state semantics.

- **R4 gateway re-mint task (gateway-side change to mint tokens without `user_id`/`iat`).**
  Current gateway mints tokens with `user_id` and `iat` in the payload. Brainstorm pops these fields (I6 invariant). The gateway should be updated to issue child-tokens with only `{service_id, session_id, exp}` for cleaner zero-knowledge enforcement, eliminating the PII-bearing fields from the wire entirely.

- **HMAC secret rotation automation (2-secret accept-window middleware).**
  Current rotation requires a rolling restart with a brief window of 401s for tokens signed by the old key. A 2-secret accept-window (accept tokens signed by either current or previous secret) would allow zero-downtime rotation. Requires middleware state management and a secret versioning scheme.

- **LockRegistry for sweeper vs. turn exclusion (Postgres / multi-worker).**
  The current touch-based exclusion (600s safety window) is sufficient for single-worker SQLite MVP. For Postgres multi-worker deployment, a per-session asyncio Lock (or distributed lock via Postgres advisory locks) is required to prevent the sweeper from deleting a session mid-turn under concurrent load.

- **hadolint in CI (Dockerfile lint gate).**
  `tests/deployment/test_dockerfile_lints.py` attempts to run `hadolint` but skips if the binary is absent. Adding hadolint to the CI Docker build image and making the gate non-skippable will catch Dockerfile anti-patterns (e.g., `apt-get` without pinned versions, missing `--no-cache`).

- **kustomize `commonLabels` to `labels` migration (deprecation noted in Slice D QA).**
  `k8s/kustomization.yaml` uses `commonLabels` which is deprecated in kustomize v5 in favour of `labels` with `includeSelectors: false`. Migration is low-risk but requires testing that selector labels on the Service and StatefulSet still match after the change.

- **Replace `cachetools.TTLCache` with Redis when multi-replica (current cache is per-pod).**
  The idempotency cache (`src/server/idempotency.py`) uses `cachetools.TTLCache` which is in-process. For multi-replica deployment, duplicate requests routed to different pods will both be processed (cache miss on the second pod). A shared Redis TTL cache provides cross-pod idempotency. Prerequisite: Postgres checkpointer readiness (items above) and cluster networking for Redis.
