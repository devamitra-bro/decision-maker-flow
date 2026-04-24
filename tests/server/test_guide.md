# Test Guide — Brainstorm MCP Server (Slice A + Slice C)

## Version: 2.0.0 (updated for Slice C completion)

## Purpose

This guide enables an independent QA agent or engineer to verify the Brainstorm MCP
server implementation from the test suite alone. It documents required inputs, expected
`[IMP:9-10]` log markers, SQL verification queries, and test execution commands.

Specification reference: `plans/DevelopmentPlan_MCP.md`

---

## Environment Setup

All tests require these environment variables (set automatically by the `server_env` fixture):

| Variable | Value (test) |
|---|---|
| `BRAINSTORM_HMAC_SECRET` | `brainstorm-test-secret-32bytes!!` |
| `GATEWAY_LLM_PROXY_URL` | `https://test-llm-proxy.example.com/v1` |
| `GATEWAY_LLM_API_KEY` | `test-api-key-for-unit-tests` |

SQLite path: each test creates an isolated temp directory via `tmp_path` or `tempfile.mkdtemp()`.
No persistent database state is shared between tests.

---

## Running the Tests

### Full server test suite with coverage:
```bash
cd /Users/a1111/Dev/CrabLink/flows/brainstorm
/opt/homebrew/bin/python3.12 -m pytest tests/server/ -v --cov=src/server \
    --cov-report=term-missing --cov-fail-under=95
```
Expected: **116 passed, 1 skipped, 97%+ coverage**

### Legacy regression (non-breaking):
```bash
/opt/homebrew/bin/python3.12 -m pytest src/features/decision_maker/tests/ tests/ui/ -v
```
Expected: **59 passed**

### ZK gate (R4 zero-knowledge check):
```bash
bash scripts/verify_zero_knowledge.sh
```
Expected: `[ZK_CHECK][PASS] src/server/ clean.`

---

## Test Modules and Coverage

| Test File | Purpose | Key ACs |
|---|---|---|
| `test_auth.py` | HMAC token verification, AuthError taxonomy | AC1 §1.1 |
| `test_auth_go_compat.py` | Go wire-format fixture compatibility | AC1 §1.1 |
| `test_config.py` | Config validation, env vars | §1.3 |
| `test_turn_handler.py` (13 tests) | POST /turn: happy path, auth failures, validation, idempotency | AC1-AC8 |
| `test_done_handler.py` (3 tests) | POST /done: success, idempotent unknown session, no-auth 401 | AC3 |
| `test_health_ready_metrics.py` (5 tests) | GET /healthz, /readyz, /metrics | AC4 §4.3-4.4 |
| `test_sweeper.py` (5 tests) | Sweeper TTL-based cleanup, cancellation, metrics | §4.5 |
| `test_lifespan.py` (3 tests) | App startup/shutdown lifecycle, LDD log marker | §4.6 |
| `test_route_auth_coverage.py` (2 tests) | Structural: all protected routes have auth dep | §1.2 A1 |
| `test_integration_full_stack.py` (2 tests) | Full scenario + AC8 idempotency | §2.3 AC8 |
| `test_coverage_boost.py` (12 tests) | Targeted coverage: errors.py, middleware.py, sweeper._tick | §2.3 |
| `test_zero_knowledge_gate.py` (5 tests) | ZK invariants: no user_id, no billing vocab | §6 AC1 AC7 |

---

## Expected `[IMP:9-10]` Log Markers

The following log lines at IMP:9-10 MUST appear in a successful run. They represent
the AI Belief State — evidence that the business logic path executed correctly.

### POST /turn (new session) — `test_turn_happy_new_session`
```
[BRAINSTORM][IMP:9][handle_turn][Auth][Verify-OK][BELIEF] token_fp=sha256:... session_fp=sha256:... service_id=brainstorm exp_delta_s=... [OK]
[BRAINSTORM][IMP:7][handle_turn][Turn][End][OK] session_fp=sha256:... turn_n=0 duration_ms=... reply_chars=... [OK]
```

### POST /turn (resume session) — `test_turn_happy_resume_session`
```
[BRAINSTORM][IMP:9][handle_turn][Auth][Verify-OK][BELIEF] ... [OK]
[BRAINSTORM][IMP:7][handle_turn][Turn][End][OK] ... state=done ... [OK]
```

### Auth failure (missing header) — `test_turn_missing_authz`
```
[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] reason=malformed detail=missing_authorization_header [FAIL]
```

### Auth failure (wrong service) — `test_turn_wrong_service`
```
[BRAINSTORM][IMP:9][verify_session_token][Auth][Verify-Failed] reason=wrong_service got='other_service' expected='brainstorm' ... [FAIL]
```

### Sweeper tick — `test_sweeper_tick_direct`
```
[BRAINSTORM][IMP:5][Sweeper][Sweep][Cleanup] session_fp=sha256:... [OK]
[BRAINSTORM][IMP:5][Sweeper][Sweep][Summary] deleted=1 now_unix=... [OK]
```

### Lifespan startup — `test_lifespan_ldd_startup_ok_logged`
```
[BRAINSTORM][IMP:7][Lifespan][Startup][OK] checkpointer=sqlite llm_model=gpt-4o-mini sweep_interval_sec=... [OK]
```

### /readyz healthy — `test_readyz_ok_when_all_healthy`
```
[BRAINSTORM][IMP:5][handle_readyz][Checkpointer][Ping] ok [OK]
[BRAINSTORM][IMP:5][handle_readyz][LLM][GatewayProbe] status=200 [OK]
```

---

## SQL Queries for Manual Verification

After `test_integration_full_stack.py::test_full_stack_scenario`, the real SQLite at
`tmp_path/integration.sqlite` should contain:

```sql
-- Verify a checkpoint was written (thread_id is uuid4.hex -- 32 chars)
SELECT thread_id, checkpoint_ns FROM checkpoints LIMIT 5;

-- Verify touch tracking sidecar
SELECT thread_id, last_touched FROM _brainstorm_meta LIMIT 5;

-- After /done is called, the session should be deleted:
SELECT thread_id FROM checkpoints WHERE thread_id = '<session_id>';
-- Expected: 0 rows

-- Verify writes table (LangGraph pending checkpoint writes):
SELECT thread_id FROM writes WHERE thread_id = '<session_id>';
-- Expected: 0 rows after /done
```

---

## Key Invariants to Verify (ZK / R4)

1. **I5**: `TokenClaims` has exactly 3 fields: `service_id`, `session_id`, `exp`. NO `user_id`, NO `iat`.
2. **I6**: `verify_session_token` pops `user_id` and `iat` from the raw payload dict after JSON parse.
3. **I7**: HMAC comparison uses `hmac.compare_digest` (not `==`).
4. **AC1**: `src/server/` contains NO strings: `energy`, `chakra`, `astrology`, `crypto`,
   `bitcoin`, `healing`, `manifesting`, `channeling`.
5. **AC7**: `src/server/` contains NO raw `user_id` field access or `ruble` literal.

Verified by: `bash scripts/verify_zero_knowledge.sh`

---

## Idempotency (AC8) Verification

Test: `test_integration_full_stack.py::test_duplicate_turn_same_session_is_idempotent`

Assertion: `len(llm_call_count) == 1` — LLM invoked exactly once for 2 identical requests
with the same `Idempotency-Key` header.

Manual check: Send two identical POST /turn requests with `Idempotency-Key: <key>`.
Both should return 200 with identical `session_id` and `reply`. Second call must include
the log line `[BRAINSTORM][IMP:5][handle_turn][Idempotency][CacheHit]`.

---

## Authentication Token Format

Tokens are v1 HMAC-SHA256 Bearer tokens:
```
Bearer v1.<base64url(json_payload)>.<base64url(hmac_sha256)>
```

Payload JSON (Go wire format):
```json
{"user_id": 999, "service_id": "brainstorm", "session_id": "<uuid4>", "exp": <unix_ts>}
```

Test factory: `signed_token_factory` fixture in `tests/server/conftest.py`.
Secret: `brainstorm-test-secret-32bytes!!` (32-byte ASCII).

---

## Prometheus Metrics Reference

Key metrics exposed via GET /metrics:

| Metric | Type | Description |
|---|---|---|
| `brainstorm_turns_total{state}` | Counter | Total /turn calls by final state |
| `brainstorm_turn_duration_seconds` | Histogram | End-to-end /turn latency |
| `brainstorm_llm_roundtrip_seconds` | Histogram | LLM stream duration |
| `brainstorm_active_sessions` | Gauge | Current live sessions |
| `brainstorm_done_total` | Counter | Total /done calls |
| `brainstorm_token_verify_failures_total{reason}` | Counter | Auth failures by reason |
| `brainstorm_idempotent_hits_total{source}` | Counter | Cache hits by source (header/internal) |
| `brainstorm_sweeper_runs_total` | Counter | Sweeper ticks executed |
| `brainstorm_sweeper_deleted_total` | Counter | Sessions deleted by sweeper |
| `brainstorm_readyz_checks_total` | Counter | /readyz probe invocations |

---

## Anti-Loop Protocol Status

Counter file: `tests/server/.test_counter.json`
Reset to 0 on 100% PASS. Check counter before running to detect prior failures.

```bash
cat tests/server/.test_counter.json
```

Expected on clean run: `{"failures": 0}`

---

## Production Files Modified/Created (Slice C)

| File | Change |
|---|---|
| `src/server/errors.py` | NEW: LLMTimeoutError, to_http_exception() |
| `src/server/metrics.py` | NEW: Metrics dataclass, build_registry(), make_metrics() |
| `src/server/middleware.py` | NEW: CorrelationIdMiddleware |
| `src/server/idempotency.py` | NEW: IdempotencyCache with TTLCache + asyncio.Lock |
| `src/server/sweeper.py` | NEW: Sweeper with asyncio.sleep + _tick() |
| `src/server/turn_api.py` | NEW: All 5 HTTP handlers + Pydantic models |
| `src/server/app_factory.py` | NEW: lifespan() async CM + create_app() factory |
| `src/server/checkpoint_factory.py` | UPDATED v1.1.0: adelete_thread(), ping() |
| `src/server/auth.py` | UPDATED: Header() injection in require_service._dep |
