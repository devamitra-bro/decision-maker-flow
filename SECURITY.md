# SECURITY.md — Brainstorm MCP Sub-Agent

Version: 1.0.0 | Last updated: Slice E | Scope: `flows/brainstorm/`

---

## Threat Model

Brainstorm is an internally-facing HTTP service exposed only within the CrabLink k8s cluster. The following threat actors are considered:

**Untrusted callers.** Any pod within the cluster could craft HTTP requests to the brainstorm service. Mitigation: every protected endpoint requires a valid HMAC-SHA256 session token signed by `BRAINSTORM_HMAC_SECRET`. Requests without a token or with a forged/tampered token receive 401.

**Replay attacks.** A valid token captured in transit could be re-submitted after its intended use. Mitigation: every token carries an `exp` field (Unix timestamp). Tokens are rejected if `exp <= now()` at verification time. The TTL is controlled by the gateway at mint time (default 300s). Additional replay protection via the per-request `Idempotency-Key` cache (TTL 600s) prevents duplicate LLM invocations but does not prevent session state access — that is governed by session_id ownership in the gateway layer.

**Token forgery.** An attacker who does not possess `BRAINSTORM_HMAC_SECRET` cannot produce a valid HMAC-SHA256 signature. Attempted forgeries are caught by `hmac.compare_digest` and increment `brainstorm_token_verify_failures_total{reason="bad_signature"}`.

**Information leakage via logs.** Raw secrets, tokens, API keys, and user messages must never appear in logs. All log lines at IMP >= 5 use `sha256:<first-8-hex>` fingerprints instead of raw values (enforced by `_token_fp()` and `verify_zero_knowledge.sh` CI gate).

**Container escape / privilege escalation.** The container runs as uid 10001 (non-root), with `readOnlyRootFilesystem: true` and `capabilities: drop: [ALL]` in the k8s SecurityContext. The only writable mount is the PVC at `/data` (SQLite checkpoint database).

---

## HMAC-SHA256 Wire Format

Tokens are wire-compatible with `crablink-gateway/kernel/sessiontoken/token.go` v1.0.0.

**Format:**
```
v1.<payload_b64u>.<sig_b64u>
```

where:
- `payload_b64u` = base64url (no padding, Go `RawURLEncoding`) of compact JSON
- `sig_b64u` = base64url (no padding) of `HMAC-SHA256(BRAINSTORM_HMAC_SECRET_bytes, payload_bytes)`

**Payload JSON claims (R4 zero-knowledge interpretation):**
```json
{ "service_id": "brainstorm", "session_id": "<uuid-v4>", "exp": <unix_seconds> }
```

Fields `user_id` and `iat` that may be present in the gateway-issued token are explicitly popped after `json.loads` and asserted absent before `TokenClaims` construction (I6 zero-knowledge invariant). The `TokenClaims` frozen dataclass contains exactly `{service_id, session_id, exp}` — never user identity.

**Python verification algorithm (mirrors Go `Validate()`):**
1. Assert `Authorization` header starts with `"Bearer "`.
2. Strip prefix; split on `"."` — must yield exactly 3 parts.
3. Assert `parts[0] == "v1"`.
4. `base64url_decode(parts[1])` → `payload_bytes` (pad-repair: append `"="` until `len % 4 == 0`).
5. `base64url_decode(parts[2])` → `sig_bytes`.
6. `computed = HMAC-SHA256(secret, payload_bytes)`.
7. `hmac.compare_digest(computed, sig_bytes)` — **MUST use constant-time compare**.
8. `json.loads(payload_bytes)` → pop `user_id`, `iat`.
9. Validate `service_id == "brainstorm"`, `session_id` is UUID-v4, `exp > now()`.

---

## Constant-Time Signature Comparison

Python's `==` on bytes short-circuits on the first differing byte, leaking information about how many leading bytes match through response-time variance (timing side-channel). The implementation uses `hmac.compare_digest(computed, sig_bytes)` exclusively, which runs in constant time regardless of where the first difference occurs. This mirrors Go's `crypto/subtle.ConstantTimeCompare`.

**`==` on signature bytes is forbidden** (enforced by code review of `src/server/auth.py`).

---

## Logging Policy

The following values are **NEVER** logged at any IMP level:

- Raw `Authorization` header value
- Raw `BRAINSTORM_HMAC_SECRET`
- Raw `GATEWAY_LLM_API_KEY`
- Raw user `message` body content
- Raw `session_id` (only fingerprint)

Allowed log form for any identifying value: `sha256:<first-8-hex-of-sha256-digest>` produced by `_token_fp()`.

Enforcement: `scripts/verify_zero_knowledge.sh` runs in `tests/server/conftest.py::pytest_sessionstart` and fails the test session if forbidden patterns appear in `src/server/`.

LDD log lines at IMP >= 5 that carry fingerprints:
- `[IMP:9][Auth][Verify-Failed] reason=<X> token_fp=sha256:<8hex>`
- `[IMP:9][Auth][Verify-OK] token_fp=sha256:<8hex> session_fp=sha256:<8hex>`
- `[IMP:7][Turn][Start] session_fp=sha256:<8hex>`
- `[IMP:7][Turn][End] session_fp=sha256:<8hex> token_fp=sha256:<8hex>`
- `[IMP:5][Sweep][Cleanup] session_fp=sha256:<8hex>`

---

## Race Analysis: Sweeper vs. Turn

The sweeper coroutine deletes stale checkpoint sessions. A race exists if the sweeper deletes a session while a `/turn` call is mid-execution for that session.

**Mitigation design (touch-based exclusion, §9.4):**
- Every `/turn` and `/done` call updates a `last_touched` timestamp for the `thread_id` in a `_brainstorm_meta` table (sqlite) immediately on checkpoint access.
- The sweeper predicate is `last_touched < now - SWEEP_THRESHOLD_SECS` where `SWEEP_THRESHOLD_SECS = 600` (default).
- Config validator enforces `SWEEP_THRESHOLD_SECS >= 5 * TURN_TIMEOUT_SEC` (default: `600 >= 5 * 120`). A turn running at its maximum allowed timeout (120s) finishes with `5x` safety margin before the sweeper can delete its session.

**Accepted race window:**
- Turn running <= 600s: **safe** — session preserved by touch timestamp.
- Turn running > 600s: session may be swept mid-flight. Next checkpoint write creates a new session (context loss, not data corruption). This is documented as acceptable degradation for the MVP.

**Deferred:** `LockRegistry` approach (per-session asyncio Lock shared between sweeper and turn handler) is deferred to BACKLOG.md pending Postgres/multi-worker readiness.

---

## Operational Secrets

**`BRAINSTORM_HMAC_SECRET`**
- Injected via k8s Secret (`k8s/secret.example.yaml` → operator creates `k8s/secret.yaml`).
- Must be >= 32 random bytes. Generate: `openssl rand -hex 32`.
- Per-service key: MUST differ from the gateway uber-key (`GATEWAY_HMAC_SECRET`).
- Rotation procedure (current): update k8s Secret, trigger rolling restart (`kubectl rollout restart deployment/brainstorm-mcp`). Brief window of tokens signed with old secret being rejected — acceptable for MVP. Two-secret accept-window middleware deferred to BACKLOG.md.

**`GATEWAY_LLM_API_KEY`**
- Injected via k8s Secret.
- Used only at LLM client construction time. Never logged.
- Rotation: update k8s Secret, rolling restart.

---

## Container Hardening

The brainstorm container implements the following security controls:

- **Non-root user:** `USER 10001` in Dockerfile; `runAsUser: 10001` in k8s SecurityContext.
- **Read-only root filesystem:** `readOnlyRootFilesystem: true` in SecurityContext. Only `/data` (PVC) and `/tmp` are writable.
- **Capabilities drop:** `capabilities: drop: [ALL]` — no Linux capabilities granted.
- **No privilege escalation:** `allowPrivilegeEscalation: false`.
- **Replicas = 1 + Workers = 1 hardlock:** SQLite checkpoint is not safe for concurrent writes from multiple processes or pods. `replicas: 1` is hardlocked in `k8s/deployment.yaml` with a comment. `--workers 1` is hardlocked in the Dockerfile CMD. Scaling requires switching to the Postgres checkpointer (see BACKLOG.md).

---

## Zero-Knowledge Domain Invariant

Brainstorm is a **zero-knowledge biller**: it does not receive, store, or log any user identity (`user_id`), energy balance, billing credits, or CrabLink-internal topology information. The only external knowledge it holds:

1. Its own HMAC secret (`BRAINSTORM_HMAC_SECRET`) — for verifying incoming session tokens.
2. The LLM proxy URL and API key — for outgoing LLM calls.
3. The SQLite (or Postgres) DSN — for checkpoint persistence.

This constraint is structurally enforced via:
- `TokenClaims` frozen dataclass with exactly `{service_id, session_id, exp}` (I5 invariant).
- Immediate `raw.pop("user_id", None)` after token payload decode (I6 invariant).
- `verify_zero_knowledge.sh` CI gate checking `src/server/` for forbidden import patterns.
- Import ban on `BalanceReader`, `User`, `energy_`, and any gateway billing symbol.
