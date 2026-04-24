# FILE: plans/DevelopmentPlan_MCP.md
# VERSION: 1.0.0
# TARGET_PROJECT: /Users/a1111/Dev/CrabLink/flows/brainstorm
# TASK_SPEC_SOURCE: /Users/a1111/Dev/CrabLink/TASK_brainstorm_mcp_integration.md
# PROTOCOLS: devplan-protocol + document-protocol + graph-protocol + core-rules
# FRAMEWORK_HOST: /Users/a1111/Dev/lessons/LESSON_2 (READ-ONLY during this flow)

$START_DOC_NAME DevelopmentPlan_Brainstorm_MCP_Integration_v1

**PURPOSE:** End-to-end engineering contract for converting the standalone `flows/brainstorm/` LangGraph service into the first **stateful MCP sub-agent** of the CrabLink ecosystem. Binds all architectural decisions from Architect HITL Gates 1, 2, and 3 (v1 cycle) into a machine-interpretable plan consumable by fresh-context `mode-code` subagents across five sequential feature slices (A..E). Wire-format of incoming session-tokens is cryptographically identical to `crablink-gateway/kernel/sessiontoken/token.go` v1.0.0; payload interpretation deliberately diverges via the R4⊕R3 collapse (zero-knowledge of user identity, path-based operation authorization).

**SCOPE:** New HTTP server layer `src/server/` (config, HMAC verifier, checkpointer factory, FastAPI app, idle sweeper); DI seams in `src/features/decision_maker/` keeping graph topology untouched; deployment artifacts (`Dockerfile`, `k8s/*.yaml`, `k8s/brainstorm.manifest.json`); L2 smoke script; documentation (`README.md` MCP section, `SECURITY.md`, local `AppGraph.xml`). Business logic of the decision-maker graph, prompts, CoVe mechanics, and the Gradio UI contract are **frozen** — only dependency-injection seams are added.

**KEYWORDS:** [DOMAIN(10): MCP_Integration; DOMAIN(9): ZeroKnowledgeDomain; DOMAIN(9): SessionAuth; TECH(10): FastAPI; TECH(10): HMAC_SHA256; TECH(9): LangGraphCheckpointer; TECH(9): Prometheus; TECH(8): Kubernetes; PATTERN(10): DependencyInjection; PATTERN(9): PathBasedAuthZ; PATTERN(9): FactoryPattern; PATTERN(8): CryptoWireCompat; CONCEPT(10): AgentAsToolUniformity; CONCEPT(9): ZeroKnowledgeBiller; CONCEPT(9): SemanticExoskeleton; CONCEPT(8): LDD2_0]

$START_DOCUMENT_PLAN
### Document Plan
<!--
AI-Agent: Generate this skeleton before expanding sections.
Format: TYPE [Description] => [Artifact_ID]
-->

**SECTION_GOALS:**
- GOAL [Expose brainstorm as a network-callable MCP sub-agent via FastAPI with HMAC-authenticated POST /turn and /done, plus /healthz, /readyz, /metrics] => GOAL_HTTP_CONTRACT
- GOAL [Port the crablink-gateway v1 session-token wire-format to Python 1:1 for cryptographic HMAC-SHA256 verification, with R4 zero-knowledge payload interpretation (drop user_id, drop iat)] => GOAL_TOKEN_COMPAT_R4
- GOAL [Provide production-safe checkpointer persistence with an env-driven factory (sqlite default + experimental postgres branch)] => GOAL_PERSISTENCE_FACTORY
- GOAL [Make the decision_maker graph injectable (checkpointer + llm_client) without modifying its topology, node semantics, or Gradio UI contract] => GOAL_DI_SEAMS_PRESERVING_UI
- GOAL [Deliver production-ready Docker + k8s artifacts implementing the standalone-deployable invariant (configurable via env vars only, no hardcoded CrabLink URLs)] => GOAL_DEPLOY_PORTABILITY
- GOAL [Enforce the Zero-Knowledge-Biller invariant through structural checks (grep gates, frozen dataclass shape, forbidden imports)] => GOAL_ZERO_KNOWLEDGE_ENFORCEMENT
- GOAL [Split implementation across five fresh-context subagent invocations (Slice A..E) each bounded below anti-loop threshold] => GOAL_SLICE_DECOMPOSITION

**SECTION_USE_CASES:**
- USE_CASE [Krab (ZeroClaw) calls `brainstorm__turn` via MCP-gateway; gateway dispatches HTTP POST /turn with a minted session-token; brainstorm authenticates, loads/creates checkpoint, runs LangGraph, returns reply] => UC_TURN_HAPPY_PATH
- USE_CASE [Krab invokes /done to explicitly close a session; brainstorm deletes checkpoint idempotently and decrements active_sessions gauge] => UC_DONE_IDEMPOTENT
- USE_CASE [Idle session past TTL; sweeper coroutine deletes it and emits [IMP:5][Sweep][Cleanup] log; active_sessions gauge decremented] => UC_TTL_SWEEP
- USE_CASE [Attacker presents a forged token; verify_session_token raises, metric `brainstorm_token_verify_failures_total{reason=bad_signature}` increments, 401 response returned; full token never logged] => UC_AUTH_FAILURE
- USE_CASE [Gateway-ops deploys brainstorm pod via `kubectl apply -k k8s/`; readiness probe passes once checkpointer initializes and LLM-gateway responds] => UC_K8S_DEPLOY
- USE_CASE [Operator registers manifest: `curl -X POST <gateway>/manifests/brainstorm -d @k8s/brainstorm.manifest.json`; brainstorm itself never reaches out to the gateway control-plane] => UC_MANIFEST_REGISTRATION
- USE_CASE [Developer runs `GRADIO_UI=true python scripts/run_brainstorm_ui.py`; legacy Gradio UI boots unchanged using the new DI-injected checkpointer] => UC_LEGACY_UI_SMOKE
- USE_CASE [Fresh-context mode-code subagent receives a single slice scope with absolute paths; implements, tests, returns SUCCESS or Bug Report; Architect gates the transition to next slice] => UC_SLICE_DELEGATION
- USE_CASE [mode-qa audits a completed slice strictly against Acceptance Criteria AC1..AC8 and slice-specific AC items; runs `verify_zero_knowledge.sh`] => UC_QA_AUDIT
$END_DOCUMENT_PLAN

---

$START_SECTION_ArchitecturalDecisions
### 1. Architectural Decisions (Collapsed at HITL Gates)

$START_ARTIFACT_TokenContract_R4_R3
#### 1.1. Token Wire-Format and Claims Interpretation — R4 ⊕ R3

**TYPE:** DECISION
**KEYWORDS:** [DOMAIN(10): SessionAuth; PATTERN(10): CryptoWireCompat; CONCEPT(10): ZeroKnowledgeDomain]

$START_CONTRACT
**PURPOSE:** Fix the exact interpretation of incoming session-tokens under the invariant that the gateway wire-format `v1.<b64url(payload)>.<b64url(hmac_sha256(payload,secret))>` is immutable and brainstorm must not know user identity.
**DESCRIPTION:** Brainstorm ports the Go verifier cryptographically (HMAC-SHA256 + `base64.urlsafe_b64decode` with pad-repair + `hmac.compare_digest`) but reduces the payload to a three-field dataclass `TokenClaims(service_id, session_id, exp)`. Fields `user_id` and `iat` present in the raw JSON are explicitly `.pop()`ed immediately after `json.loads` and asserted absent. Operation granularity (`/turn` vs `/done`) is enforced by URL path only — both endpoints require `claims.service_id == "brainstorm"`. This combines R4 (zero-knowledge identity; forward-compatible with gateway child-token re-mint) and R3 (path-based operation scoping).
**RATIONALE:** (1) Go source-of-truth lacks `scope` / `iat` fields declared in task §2. (2) Wire-format is invariant (`tokenVersion="v1"` forever, per Go module comment). (3) Task §1 demands zero-knowledge biller; receiving `user_id` at the domain boundary is a structural violation. (4) Overloading `service_id` with operation suffix (R2) confuses identity and capability semantics. (5) Re-minting in gateway (R4) is the correct layer for identity isolation; brainstorm's implementation is forward-compatible regardless of whether gateway enables re-mint now or later.
**ACCEPTANCE_CRITERIA:**
- `TokenClaims` frozen dataclass contains exactly `{service_id, session_id, exp}`.
- `verify_session_token` passes an architectural assertion: `assert "user_id" not in raw and "iat" not in raw` after pop.
- All unit tests cover: malformed, bad_version, bad_signature, expired, wrong_service (via `service_id != "brainstorm"`), missing_session_id; plus one Go-generated fixture test (see §7.1).
$END_CONTRACT

$START_BODY
**`TokenClaims` dataclass (fixed shape):**
```python
@dataclass(frozen=True)
class TokenClaims:
    service_id: str   # MUST equal "brainstorm"
    session_id: str   # non-empty; UUID-v4 check enforced by verifier
    exp: int          # unix seconds; must be > now() at verify time
```

**`verify_session_token` signature (fixed):**
```python
def verify_session_token(
    raw_authorization_header: str | None,
    required_service_id: str,
    now: int,
    secret: bytes,
) -> TokenClaims: ...
# raises AuthError(reason) where reason ∈
#   {"malformed","bad_version","bad_signature","expired","wrong_service","missing_session"}
```

**Bearer-parsing is internal** to `verify_session_function`, not inside the FastAPI dependency wrapper. The dependency's only job is: read `Authorization` header via `Header(None, alias="Authorization")`, reject missing header with 401, pass raw string to verifier, translate `AuthError` → `HTTPException(401|403)`.

**Forbidden labels on `brainstorm_token_verify_failures_total`:** `insufficient_scope` (removed with R3 collapse), `bad_user_id` (never extracted), `iat_skew` (field dropped).
$END_BODY

$START_LINKS
**IMPLEMENTS:** GOAL_TOKEN_COMPAT_R4, GOAL_ZERO_KNOWLEDGE_ENFORCEMENT
**IMPACTS:** SLICE_A, AC1_ZeroKnowledge, AC6_NoSecretLeakage
**REQUIRES:** Go token.go v1.0.0 (read-only reference, do not re-read during implementation)
$END_LINKS

$END_ARTIFACT_TokenContract_R4_R3

$START_ARTIFACT_AuthLayer_A1
#### 1.2. Auth Placement — A1 (FastAPI Depends per-route)

**TYPE:** DECISION
**KEYWORDS:** [PATTERN(9): DependencyInjection; TECH(9): FastAPI_Depends]

$START_CONTRACT
**PURPOSE:** Authorize protected routes via FastAPI `Depends(require_service("brainstorm"))` rather than global middleware or per-route decorators.
**DESCRIPTION:** `require_service(service_id)` is a factory returning a closure `_dep(authz, cfg, metrics) -> TokenClaims`. Public routes (`/healthz`, `/readyz`, `/metrics`, `/openapi.json`, `/docs`) explicitly omit the dependency. Coverage is enforced by a unit test `test_all_non_public_routes_require_auth` iterating `app.routes` and asserting each non-public `APIRoute` has a dependency whose callable name contains `"require_service"` or `"verify"`.
**RATIONALE:** A1 over A2 (middleware): middleware's PUBLIC_PATHS allowlist is equally fragile as A1's missing-Depends, but A2 breaks OpenAPI schema (gateway ops reading `/docs` sees no auth requirement) and hides `claims` in `request.state`. A1 is FastAPI-idiomatic, testable via `app.dependency_overrides`, and keeps claims visible in handler signatures.
**ACCEPTANCE_CRITERIA:**
- `test_all_non_public_routes_require_auth` passes on the final app.
- `/openapi.json` shows `security` schema on `/turn` and `/done`.
- No `request.state.claims` access pattern appears in any handler.
$END_CONTRACT

$START_BODY
**Caching policy:** `require_service` factory uses a module-level dict `_DEPS_CACHE: dict[str, Callable]` with `setdefault` to avoid per-call closure creation (FastAPI dependency identity must remain stable for `dependency_overrides` to work in tests).
$END_BODY

$END_ARTIFACT_AuthLayer_A1

$START_ARTIFACT_CheckpointerInjection_B1
#### 1.3. Checkpointer Injection — B1 with DI-hook

**TYPE:** DECISION
**KEYWORDS:** [PATTERN(9): FactoryPattern; PATTERN(8): DependencyInjection]

$START_CONTRACT
**PURPOSE:** Build the checkpointer through a single pure factory `build_checkpointer(cfg) -> BaseCheckpointSaver` and share the instance between HTTP handlers (via `Depends(get_checkpointer)`) and the idle sweeper (via constructor injection).
**DESCRIPTION:** `build_checkpointer` switches on `cfg.checkpointer_kind`:
- `"sqlite"` → `SqliteSaver.from_conn_string(cfg.sqlite_path)` (default, production for MVP).
- `"postgres"` → `PostgresSaver.from_conn_string(cfg.checkpoint_dsn)` (EXPERIMENTAL; marked in docstring; tests present, production readiness out-of-scope of this TASK).
- Unknown kind → `ConfigError("unsupported checkpointer_kind")`.
Every factory result has `.setup()` called once in the FastAPI lifespan to auto-migrate schema. The lifespan stores the instance on `app.state.checkpointer` and constructs `Sweeper(checkpointer=..., interval_secs=cfg.sweep_interval)` reusing the same instance (single-writer constraint; sqlite WAL lock safety).
**RATIONALE:** B2 (Protocol-based) is premature abstraction for a 2-backend surface (one experimental). B3 (dual union) introduces mypy noise without flexibility gain. B1 is exactly what task §3 prescribes; testability is preserved via `dependency_overrides[get_checkpointer] = lambda: InMemoryStub()` for unit tests of handlers, and integration tests use the real `SqliteSaver(tmp_path)`.
**ACCEPTANCE_CRITERIA:**
- `build_checkpointer(sqlite_cfg)` → roundtrip save/load via `tmp_path`.
- `build_checkpointer(postgres_cfg)` → works in integration test via testcontainers (marker `@pytest.mark.integration_postgres`).
- `build_checkpointer(unknown_cfg)` → `ConfigError`.
- FastAPI lifespan shutdown calls `app.state.checkpointer.close()` (verified by `test_lifespan_closes_checkpointer_on_shutdown`).
$END_CONTRACT

$END_ARTIFACT_CheckpointerInjection_B1

$START_ARTIFACT_BusIntegration_C1
#### 1.4. Bus Integration — C1 (point-to-point) + C3-via-LDD

**TYPE:** DECISION
**KEYWORDS:** [CONCEPT(9): ZeroKnowledgeDomain; PATTERN(8): Observability]

$START_CONTRACT
**PURPOSE:** Brainstorm does not publish to any gateway bus; audit trail flows through `stdout` JSON-line logs (LDD `[IMP:7][Turn][End]`) collected by the standard k8s log pipeline.
**DESCRIPTION:** No bus client, no gateway-address knowledge, no shared broker credentials. The turn-completed event is a structured LDD log line: `[BRAINSTORM][IMP:7][Turn][End][OK] session_fp=<8hex> turn_n=<int> duration_ms=<int> reply_chars=<int> token_fp=sha256:<8hex>`. Prohibited fields in this log: `user_id`, `energy`, `billing`, `balance`, raw `reply` body, raw `session_id` (only `session_fp = sha256(session_id)[:8]`), raw `Authorization` header.
**RATIONALE:** C2 (bus publish) explicitly violates task §1 zero-knowledge and couples deploy topology. C3 is a free bonus of axis [5] LDD coverage — no additional code.
$END_CONTRACT

$END_ARTIFACT_BusIntegration_C1

$START_ARTIFACT_SuccessCriteria_8_Axes
#### 1.5. Success Criteria — 8 Axes (Gate 1 + Gate 2 additions)

**TYPE:** NFR
**KEYWORDS:** [CONCEPT(10): Verifiability; PATTERN(9): StructuralEnforcement]

$START_CONTRACT
**PURPOSE:** Every slice A..E is measured against these axes before the QA gate is passed. Any axis red ⇒ slice not green ⇒ no merge.
**ACCEPTANCE_CRITERIA (8 axes):**
- **AC1 — Zero-knowledge isolation.** `grep -rn -E "\.user_id|UserID|user_id\s*[=:]" flows/brainstorm/src/` → 0 matches.
- **AC2 — Framework-host integrity.** `git -C /Users/a1111/Dev/CrabLink diff --stat -- ':(exclude)flows/brainstorm'` after slice commit → empty.
- **AC3 — Test coverage ≥ 95% on `src/server/*`.** `pytest --cov=src/server --cov-fail-under=95` passes.
- **AC4 — Semantic Exoskeleton.** Every new `.py` file contains `# FILE`, `# VERSION`, `# START_MODULE_CONTRACT` / `# END_MODULE_CONTRACT`, `# START_RATIONALE` / `# END_RATIONALE`, `# START_MODULE_MAP` / `# END_MODULE_MAP`, and every non-trivial function is wrapped in `# START_FUNCTION_<Name>` / `# END_FUNCTION_<Name>` with `# START_CONTRACT` and a docstring paragraph ≥ 1 sentence.
- **AC5 — LDD coverage.** Grep by `[IMP:9]` shows entries for `[Auth][Verify-Failed]`, `[Checkpointer][Fatal]`, `[LLM][Timeout]`; `[IMP:7]` for `[Turn][Start]`/`[Turn][End]`, `[LLM][Roundtrip-Start]`/`[End]`; `[IMP:5]` for `[Sweep][Cleanup]`, `[Checkpointer][Load]`/`[Save]`; `[IMP:4]` for `[HTTP][Request-In]`/`[Response-Out]`. AI Belief State lines precede each boundary.
- **AC6 — No secret leakage.** `scripts/verify_zero_knowledge.sh` greps test-run output, Docker build output, log files for regex `(HMAC_SECRET|API_KEY|[a-f0-9]{32,}|clk_[A-Za-z0-9]{20,})` minus allowed fingerprint form `sha256:[0-9a-f]{8}` → 0 matches.
- **AC7 — Zero billing-grep.** `grep -rn -E "⚡|energy|billing|credit|balance|deduct" flows/brainstorm/src/` → 0 matches. Allowed in `SECURITY.md` prose only.
- **AC8 — Idempotency of session replay.** Integration test `test_duplicate_turn_same_session_is_idempotent` verifies that a retried `/turn` with identical `(session_id, message)` does not cause a duplicate LLM call (delegated to checkpointer state; if checkpoint at `turn_n` already has this message, return cached reply).
$END_CONTRACT

$END_ARTIFACT_SuccessCriteria_8_Axes

$END_SECTION_ArchitecturalDecisions

---

$START_SECTION_SliceDecomposition
### 2. Slice Decomposition (A → B → C → D → E)

Each slice is ONE `Agent(subagent_type=mode-code)` invocation with a fresh context. An `Agent(subagent_type=mode-qa)` gate follows before the next slice begins. Anti-loop: max 3 iterations per slice before escalation back to Architect.

#### 2.1. Slice A — Config + Auth (HMAC verifier, Go-fixture compat)

- **Scope:** `src/server/__init__.py`, `src/server/config.py`, `src/server/auth.py`, `tests/server/__init__.py`, `tests/server/conftest.py`, `tests/server/test_config.py`, `tests/server/test_auth.py`, `tests/fixtures/go_generated_tokens.json`, `scripts/verify_zero_knowledge.sh`.
- **Entry criteria:** Architect approves DevelopmentPlan_MCP.md (this file).
- **Exit criteria (QA gate):**
  - AC3 ≥ 95% on `src/server/config.py` + `src/server/auth.py`.
  - AC4, AC5, AC6 verified on the added files.
  - Go-compat fixture test passes (see §7.1); the fixture JSON is committed with the reference Go code snippet used to generate it in a code-block inside the JSON as `_generator` field.
  - `verify_zero_knowledge.sh` produces 0 matches on the slice diff.
- **Dependencies:** none (foundational slice).

#### 2.2. Slice B — Checkpoint factory + DI seams in decision_maker

- **Scope:** `src/server/checkpoint_factory.py`, DI seams added to `src/features/decision_maker/graph.py` (additive parameters `checkpointer` and `llm_client` with backward-compatible defaults so Gradio UI still works), `tests/server/test_checkpoint_factory.py`, `tests/server/test_decision_maker_di.py`, smoke verification of `scripts/run_brainstorm_ui.py` (must still import and boot to port-bind success).
- **Entry criteria:** Slice A green.
- **Exit criteria:**
  - `build_checkpointer` covers sqlite roundtrip + unknown_kind + postgres testcontainer (latter marked `@pytest.mark.integration_postgres`, skippable without docker).
  - `start_session` / `resume_session` / `stream_session` / `stream_resume_session` accept optional `checkpointer=` and `llm_client=` kwargs; when omitted, fall back to current behavior (env-driven singletons) — zero regression for Gradio UI.
  - `GRADIO_UI=true python scripts/run_brainstorm_ui.py` launches without error (smoke launch with 2s kill; non-zero exit before 2s ⇒ fail).
- **Dependencies:** Slice A (no import cross-reference yet, but consistent namespace).

#### 2.3. Slice C — FastAPI app + Sweeper + Metrics

- **Scope:** `src/server/turn_api.py` (router + handlers + lifespan + public route registration), `src/server/sweeper.py`, `src/server/metrics.py` (Prometheus registry), `src/server/app_factory.py` (`create_app(cfg) -> FastAPI`), `src/server/errors.py` (AuthError, ConfigError, LLMTimeoutError, translation to HTTPException), `tests/server/test_turn_handler.py`, `tests/server/test_done_handler.py`, `tests/server/test_health_ready_metrics.py`, `tests/server/test_sweeper.py`, `tests/server/test_route_auth_coverage.py`, `tests/server/test_lifespan.py`, `tests/server/test_integration_full_stack.py`.
- **Entry criteria:** Slice B green.
- **Exit criteria:**
  - All HTTP happy/sad paths covered per task §7.3, §7.4 (TestClient).
  - `test_route_auth_coverage` passes — every non-public route has auth dep.
  - `test_duplicate_turn_same_session_is_idempotent` (AC8) passes.
  - Sweeper unit + race-check tests pass.
  - Full-stack integration test: 2× /turn + /done, metrics scraped and assertions on labels.
  - AC3 ≥ 95% on all of `src/server/*`.
- **Dependencies:** Slices A, B.

#### 2.4. Slice D — Deployment artifacts

- **Scope:** `Dockerfile` (multi-stage, python:3.12-slim, non-root uid 10001, healthcheck), `.dockerignore`, `k8s/deployment.yaml` (StatefulSet for sqlite mode with `replicas: 1` hardlocked + comment), `k8s/service.yaml`, `k8s/pvc.yaml`, `k8s/secret.example.yaml`, `k8s/configmap.yaml`, `k8s/kustomization.yaml`, `k8s/brainstorm.manifest.json`, `tests/deployment/test_dockerfile_lints.py` (optional hadolint shell-out, skip if unavailable), `tests/deployment/test_manifest_json.py` (validate JSON shape).
- **Entry criteria:** Slice C green.
- **Exit criteria:**
  - `docker build .` succeeds (shell verification only, not in pytest).
  - `docker run -e BRAINSTORM_HMAC_SECRET=... -e GATEWAY_LLM_PROXY_URL=... <image>` returns 200 on `/healthz` within 10s.
  - `kubectl apply --dry-run=client -k k8s/` validates.
  - `brainstorm.manifest.json` validates against an inline JSON-schema draft-07 in `test_manifest_json.py`.
- **Dependencies:** Slice C.

#### 2.5. Slice E — Smoke script + Docs + AppGraph

- **Scope:** `scripts/smoke_brainstorm.py` (7-check L2 smoke against real container + real gateway via kind; CI-skippable via `@pytest.mark.requires_gateway`), `scripts/mint_token.py` (dev-only helper), `README.md` additions (HTTP API, Configuration, Deployment, Manifest registration, Security model sections), `SECURITY.md` (new, 1-page), `AppGraph.xml` (local, regenerated via graph-protocol skill), `BACKLOG.md` (stub with deferred items: full Postgres production readiness, R4 gateway re-mint task reference, HMAC rotation automation).
- **Entry criteria:** Slice D green.
- **Exit criteria:**
  - Smoke script 7/7 PASS against a locally deployed `kind` cluster (manual verification by Architect; logs archived).
  - README + SECURITY.md reviewed.
  - `AppGraph.xml` reflects the new `src/server/` nodes and deployment section.
  - Root `/Users/a1111/Dev/CrabLink/AppGraph.xml` verified untouched (`git diff --stat`).
- **Dependencies:** Slice D.

$END_SECTION_SliceDecomposition

---

$START_SECTION_DraftCodeGraph
### 3. Draft Code Graph (MANDATORY per devplan-protocol)

<DraftCodeGraph>
  <Brainstorm_MCP_v0_1_0_Info TYPE="PROJECT_INFO">
    <keywords>MCP_Integration, FastAPI, HMAC_SHA256, LangGraph, Kubernetes, ZeroKnowledgeDomain</keywords>
    <annotation>Standalone brainstorm sub-agent exposing POST /turn + /done + health probes + /metrics, authenticated by HMAC session-tokens wire-compatible with crablink-gateway kernel/sessiontoken v1.</annotation>
    <BusinessScenarios>
      <Scenario NAME="TurnFlow">Krab -> MCP_Gateway -> POST_/turn(bearer) -> VerifyToken -> LoadCheckpoint -> RunLangGraph -> SaveCheckpoint -> 200_reply</Scenario>
      <Scenario NAME="DoneFlow">Krab -> MCP_Gateway -> POST_/done(bearer) -> VerifyToken -> DeleteCheckpoint -> 200_ack</Scenario>
      <Scenario NAME="IdleSweep">Timer -> Sweeper.scan -> CheckpointStale -> Delete -> MetricDecrement</Scenario>
    </BusinessScenarios>
  </Brainstorm_MCP_v0_1_0_Info>

  <server_config_py FILE="src/server/config.py" TYPE="CONFIG_MODULE">
    <keywords>pydantic-settings, env-driven, SecretStr</keywords>
    <annotation>Pydantic-settings loader: reads env vars, validates required fields, redacts secrets in repr.</annotation>
    <server_config_Config_CLASS NAME="Config" TYPE="IS_CLASS_OF_MODULE">
      <annotation>Frozen configuration snapshot used by every downstream component.</annotation>
    </server_config_Config_CLASS>
    <server_config_get_cfg_FUNC NAME="get_cfg" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>FastAPI Depends-compatible provider; cached via functools.lru_cache.</annotation>
    </server_config_get_cfg_FUNC>
  </server_config_py>

  <server_auth_py FILE="src/server/auth.py" TYPE="AUTH_MODULE">
    <keywords>HMAC_SHA256, base64url, ConstantTimeCompare, ZeroKnowledgeClaims</keywords>
    <annotation>Python port of crablink-gateway v1 session-token verifier with R4 zero-knowledge payload interpretation.</annotation>
    <server_auth_TokenClaims_CLASS NAME="TokenClaims" TYPE="IS_DATACLASS_OF_MODULE">
      <annotation>Frozen dataclass {service_id, session_id, exp} — explicitly no user_id, no iat.</annotation>
    </server_auth_TokenClaims_CLASS>
    <server_auth_AuthError_CLASS NAME="AuthError" TYPE="IS_EXCEPTION_OF_MODULE">
      <annotation>Typed verifier exception with `reason` enum field for metric labeling.</annotation>
    </server_auth_AuthError_CLASS>
    <server_auth_verify_session_token_FUNC NAME="verify_session_token" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Parse Bearer, split v1.&lt;payload&gt;.&lt;sig&gt;, HMAC-verify, pop user_id/iat, validate service_id + exp, return TokenClaims.</annotation>
      <CrossLinks>
        <Link TARGET="server_auth_TokenClaims_CLASS" TYPE="CONSTRUCTS_RESULT" />
        <Link TARGET="server_metrics_py" TYPE="INCREMENTS_METRIC" />
      </CrossLinks>
    </server_auth_verify_session_token_FUNC>
    <server_auth_require_service_FUNC NAME="require_service" TYPE="IS_DEP_FACTORY_OF_MODULE">
      <annotation>FastAPI Depends factory returning a closure that reads Authorization header and delegates to verify_session_token.</annotation>
      <CrossLinks>
        <Link TARGET="server_auth_verify_session_token_FUNC" TYPE="CALLS_FUNCTION" />
      </CrossLinks>
    </server_auth_require_service_FUNC>
  </server_auth_py>

  <server_checkpoint_factory_py FILE="src/server/checkpoint_factory.py" TYPE="FACTORY_MODULE">
    <keywords>LangGraph, SqliteSaver, PostgresSaver, FactoryPattern</keywords>
    <annotation>Pure factory returning a BaseCheckpointSaver. Sqlite is default/production for MVP; Postgres branch is EXPERIMENTAL.</annotation>
    <server_checkpoint_factory_build_checkpointer_FUNC NAME="build_checkpointer" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Switches on cfg.checkpointer_kind; calls saver.setup() for schema migration; raises ConfigError on unknown kind.</annotation>
      <CrossLinks>
        <Link TARGET="server_config_Config_CLASS" TYPE="READS_DATA_FROM" />
      </CrossLinks>
    </server_checkpoint_factory_build_checkpointer_FUNC>
    <server_checkpoint_factory_get_checkpointer_FUNC NAME="get_checkpointer" TYPE="IS_DEP_OF_MODULE">
      <annotation>FastAPI Depends returning app.state.checkpointer; overridable in tests.</annotation>
    </server_checkpoint_factory_get_checkpointer_FUNC>
  </server_checkpoint_factory_py>

  <server_sweeper_py FILE="src/server/sweeper.py" TYPE="BACKGROUND_MODULE">
    <keywords>asyncio, TTL, ConcurrencyLock</keywords>
    <annotation>Asyncio coroutine that scans checkpointer every interval, deletes stale sessions, updates active_sessions gauge.</annotation>
    <server_sweeper_Sweeper_CLASS NAME="Sweeper" TYPE="IS_CLASS_OF_MODULE">
      <annotation>Constructor-injected checkpointer + TTL + interval; .run() is the long-running coroutine.</annotation>
      <server_sweeper_Sweeper_run_METHOD NAME="run" TYPE="IS_METHOD_OF_CLASS">
        <annotation>Infinite loop: sleep(interval) → scan → delete expired → log [IMP:5][Sweep][Cleanup].</annotation>
        <CrossLinks>
          <Link TARGET="server_checkpoint_factory_py" TYPE="USES_CHECKPOINTER" />
          <Link TARGET="server_metrics_py" TYPE="UPDATES_GAUGE" />
        </CrossLinks>
      </server_sweeper_Sweeper_run_METHOD>
    </server_sweeper_Sweeper_CLASS>
  </server_sweeper_py>

  <server_metrics_py FILE="src/server/metrics.py" TYPE="OBSERVABILITY_MODULE">
    <keywords>prometheus_client, Counter, Histogram, Gauge</keywords>
    <annotation>Single-registry Prometheus metrics namespace; exposed via /metrics on main port.</annotation>
    <server_metrics_build_registry_FUNC NAME="build_registry" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Builds a CollectorRegistry with all brainstorm_* metrics; used both in app and in tests.</annotation>
    </server_metrics_build_registry_FUNC>
  </server_metrics_py>

  <server_errors_py FILE="src/server/errors.py" TYPE="ERRORS_MODULE">
    <keywords>ConfigError, AuthError, LLMTimeoutError, HTTPException</keywords>
    <annotation>Domain exception types and their translation to HTTPException with correlation_id.</annotation>
  </server_errors_py>

  <server_turn_api_py FILE="src/server/turn_api.py" TYPE="HTTP_MODULE">
    <keywords>FastAPI, APIRouter, Lifespan</keywords>
    <annotation>Router with POST /turn, POST /done, GET /healthz, GET /readyz, GET /metrics.</annotation>
    <server_turn_api_handle_turn_FUNC NAME="handle_turn" TYPE="IS_HANDLER_OF_MODULE">
      <annotation>Authenticated POST /turn: loads or creates checkpoint, invokes decision_maker.stream_session or resume_session, returns TurnResponse.</annotation>
      <CrossLinks>
        <Link TARGET="server_auth_require_service_FUNC" TYPE="DEPENDS_ON" />
        <Link TARGET="server_checkpoint_factory_get_checkpointer_FUNC" TYPE="DEPENDS_ON" />
        <Link TARGET="decision_maker_start_session_FUNC" TYPE="CALLS_FUNCTION" />
        <Link TARGET="decision_maker_resume_session_FUNC" TYPE="CALLS_FUNCTION" />
      </CrossLinks>
    </server_turn_api_handle_turn_FUNC>
    <server_turn_api_handle_done_FUNC NAME="handle_done" TYPE="IS_HANDLER_OF_MODULE">
      <annotation>Idempotent POST /done: deletes checkpoint; 200 on both exists/not-exists.</annotation>
      <CrossLinks>
        <Link TARGET="server_auth_require_service_FUNC" TYPE="DEPENDS_ON" />
        <Link TARGET="server_checkpoint_factory_get_checkpointer_FUNC" TYPE="DEPENDS_ON" />
      </CrossLinks>
    </server_turn_api_handle_done_FUNC>
    <server_turn_api_handle_healthz_FUNC NAME="handle_healthz" TYPE="IS_HANDLER_OF_MODULE">
      <annotation>Public liveness probe: returns 200 {status: ok} unconditionally.</annotation>
    </server_turn_api_handle_healthz_FUNC>
    <server_turn_api_handle_readyz_FUNC NAME="handle_readyz" TYPE="IS_HANDLER_OF_MODULE">
      <annotation>Readiness: checkpointer ping + LLM-gateway ping (2s timeout); returns 200 or 503 with diag.</annotation>
    </server_turn_api_handle_readyz_FUNC>
  </server_turn_api_py>

  <server_app_factory_py FILE="src/server/app_factory.py" TYPE="BOOTSTRAP_MODULE">
    <keywords>FastAPI, Lifespan, DependencyOverrides</keywords>
    <annotation>create_app(cfg) builds FastAPI instance, installs lifespan (checkpointer + sweeper), registers router, mounts /metrics.</annotation>
    <server_app_factory_create_app_FUNC NAME="create_app" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Factory pattern: injectable for tests; used by uvicorn entry point in Dockerfile CMD.</annotation>
      <CrossLinks>
        <Link TARGET="server_checkpoint_factory_build_checkpointer_FUNC" TYPE="CALLS_FUNCTION" />
        <Link TARGET="server_sweeper_Sweeper_CLASS" TYPE="CONSTRUCTS" />
        <Link TARGET="server_turn_api_py" TYPE="REGISTERS_ROUTER" />
      </CrossLinks>
    </server_app_factory_create_app_FUNC>
  </server_app_factory_py>

  <decision_maker_graph_py FILE="src/features/decision_maker/graph.py" TYPE="LANGGRAPH_MODULE">
    <keywords>LangGraph, CheckpointInjection, LLMClientInjection</keywords>
    <annotation>EXISTING module; additive DI seams only — start_session/resume_session/stream_session/stream_resume_session accept optional checkpointer= and llm_client= kwargs.</annotation>
    <decision_maker_start_session_FUNC NAME="start_session" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>ADDITIVE: gains checkpointer=None, llm_client=None kwargs; None preserves current env-driven behavior (Gradio UI regression-safe).</annotation>
    </decision_maker_start_session_FUNC>
    <decision_maker_resume_session_FUNC NAME="resume_session" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>ADDITIVE: gains checkpointer=None, llm_client=None kwargs.</annotation>
    </decision_maker_resume_session_FUNC>
  </decision_maker_graph_py>

  <tests_server_dir FILE="tests/server/" TYPE="TEST_SUITE">
    <annotation>Unit + integration tests for all src/server modules; uses pytest-asyncio, pytest-httpx, freezegun.</annotation>
  </tests_server_dir>

  <k8s_brainstorm_manifest_json FILE="k8s/brainstorm.manifest.json" TYPE="DEPLOY_ARTIFACT">
    <keywords>MCP_manifest, JSON_schema_draft_07</keywords>
    <annotation>Reference manifest submitted to gateway /manifests/brainstorm by operator; never read by the brainstorm service itself.</annotation>
  </k8s_brainstorm_manifest_json>

  <scripts_smoke_brainstorm_py FILE="scripts/smoke_brainstorm.py" TYPE="L2_SMOKE">
    <keywords>kind, real_gateway, MintToken</keywords>
    <annotation>7-check L2 smoke against locally deployed kind cluster; CI-skippable via @pytest.mark.requires_gateway.</annotation>
  </scripts_smoke_brainstorm_py>

  <ProjectCrossLinks TYPE="MODULE_INTERACTIONS_OVERVIEW">
    <Link TARGET="server_turn_api_py" TYPE="ORCHESTRATES_FLOW" />
    <Link TARGET="server_app_factory_py" TYPE="WIRES_DEPENDENCIES" />
    <Link TARGET="decision_maker_graph_py" TYPE="INJECTED_VIA_DI" />
  </ProjectCrossLinks>
</DraftCodeGraph>

$END_SECTION_DraftCodeGraph

---

$START_SECTION_DataFlows
### 4. Step-by-step Data Flows (MANDATORY per devplan-protocol)

#### 4.1. POST /turn — happy path

1. **Request ingress** (uvicorn → FastAPI). Log: `[BRAINSTORM][IMP:4][handle_turn][HTTP][Request-In] path=/turn correlation_id=<uuid4>` — `correlation_id` generated per-request via middleware, attached to `request.state.correlation_id` and returned in `X-Correlation-ID` response header.
2. **Auth dependency resolves.** `require_service("brainstorm")` reads `Authorization` header. Missing → `HTTPException(401, "missing authorization")` + metric `token_verify_failures_total{reason="malformed"}`.
3. **verify_session_token** strips `Bearer `, splits on `.` (exactly 3 parts; else `AuthError("malformed")`), verifies `parts[0] == "v1"` (else `AuthError("bad_version")`).
4. Base64url-decode payload + sig (pad-repair: append `=` until `len % 4 == 0`). On failure → `AuthError("malformed")`.
5. HMAC-SHA256 over payload bytes with `cfg.hmac_secret`. `hmac.compare_digest(computed, sig_bytes)` → if False: `AuthError("bad_signature")`.
6. `json.loads(payload)` → dict `raw`. Pop `user_id`, `iat`. Assert both absent. Construct `TokenClaims(service_id=raw["service_id"], session_id=raw["session_id"], exp=int(raw["exp"]))`. Validate `claims.service_id == "brainstorm"` (else `AuthError("wrong_service")`); `claims.session_id` non-empty and UUID-v4 (else `AuthError("missing_session")`); `claims.exp > now()` (else `AuthError("expired")`).
7. **AI Belief State** log: `[BRAINSTORM][IMP:9][handle_turn][Auth][Verify-OK][BELIEF] token_fp=sha256:<8hex> session_fp=<8hex> service_id=brainstorm exp_delta_s=<int>`.
8. **Body parse.** Pydantic `TurnRequest(session_id: str | None, message: str)`. Validate `len(message) <= 4000`, message non-empty. Else `HTTPException(400)`.
9. **Session routing.** If `body.session_id is None` → new thread: `new_session_id = uuid.uuid4().hex`. Else `session_id = body.session_id`. If checkpoint missing for a provided session_id → 404 (explicit check via `checkpointer.get({"configurable": {"thread_id": session_id}})`).
10. **LDD boundary.** `[BRAINSTORM][IMP:7][handle_turn][Turn][Start] session_fp=<8hex> new=<bool>`.
11. **Graph invocation.** If new session → `stream_session(message, checkpointer=chkpt, llm_client=llm)`; else → `stream_resume_session(session_id, message, checkpointer=chkpt, llm_client=llm)`. Wrap in `asyncio.wait_for(..., timeout=cfg.turn_timeout_sec)`. Timeout → `LLMTimeoutError` → metric + `HTTPException(408)`.
12. **Result extraction.** Iterate stream; collect final state; assemble `reply`, detect `state ∈ {"running","done"}` (by presence of graph terminal node marker), `turn_number`, `tokens_estimate`.
13. **Metrics update.** `brainstorm_turns_total{state=...}.inc()`, `brainstorm_turn_duration_seconds.observe(...)`, `brainstorm_llm_roundtrip_seconds.observe(...)`, `brainstorm_active_sessions.inc()` if new.
14. **LDD.** `[BRAINSTORM][IMP:7][handle_turn][Turn][End][OK] session_fp=<8hex> turn_n=<int> duration_ms=<int> reply_chars=<int> token_fp=sha256:<8hex>`.
15. **Response.** `TurnResponse(session_id, reply, state, metadata)` → 200.

#### 4.2. POST /done — idempotent

1. Auth same as /turn.
2. Body parse `DoneRequest(session_id: str)`.
3. `checkpointer.delete({"configurable": {"thread_id": session_id}})` — swallow `NotFound`; return `{"acknowledged": true}` unconditionally (200).
4. Metrics: `brainstorm_done_total.inc()`, `brainstorm_active_sessions.dec()` **only** if delete returned "existed".

#### 4.3. GET /healthz

1. Return 200 `{"status": "ok"}`. No IO. No metric emission (liveness probes are noisy).

#### 4.4. GET /readyz

1. `checkpointer.ping()` (sqlite: `SELECT 1`; postgres: `SELECT 1`). Failure → 503 `{"status":"not_ready","checkpointer":"fail:<exc>"}`.
2. `httpx.AsyncClient(timeout=2).get(f"{cfg.gateway_llm_proxy_url}/healthz")` → 503 if not 200.
3. Return 200 `{"status":"ready","checkpointer":"ok","llm_gateway":"ok"}`.

#### 4.5. Sweeper coroutine

1. `await asyncio.sleep(cfg.sweep_interval_sec)` (default 60s).
2. Scan: `checkpointer.list_stale(older_than=now - cfg.session_ttl_sec)` — return list of session_ids. (If native API absent, iterate all threads and filter by `last_update` metadata.)
3. For each stale id → `delete(...)`; `active_sessions.dec()`; log `[BRAINSTORM][IMP:5][Sweeper][Sweep][Cleanup] session_fp=<8hex>`.
4. Aggregate log `[BRAINSTORM][IMP:5][Sweeper][Sweep][Summary] deleted=<n>`.
5. Guard with `asyncio.Lock` shared with `/turn` handler for the same session_id to prevent race on active turn (see AC8 idempotency test scope).

#### 4.6. Lifespan (create_app)

1. **Startup.** `cfg = get_cfg()`. `checkpointer = build_checkpointer(cfg)`. `checkpointer.setup()`. `app.state.checkpointer = checkpointer`. `app.state.llm_client = build_llm_client(cfg)` (new helper in `src/core/llm_client.py` wrapping the existing `build_llm` with gateway URL override). `app.state.sweeper_task = asyncio.create_task(Sweeper(chkpt, cfg.session_ttl_sec, cfg.sweep_interval_sec).run())`. LDD `[IMP:7][Lifespan][Startup][OK]`.
2. **Shutdown.** Cancel `sweeper_task`, await with exception-swallow. `checkpointer.close()` if closable. LDD `[IMP:7][Lifespan][Shutdown][OK]`.

$END_SECTION_DataFlows

---

$START_SECTION_TestStrategy
### 5. Test Strategy

#### 5.1. Directory layout

- `tests/server/` — all new server-layer tests (A, B, C slices).
- `tests/server/fixtures/go_generated_tokens.json` — Go-generated compat fixture.
- `tests/deployment/` — Slice D (manifest + Dockerfile shape).
- `tests/ui/` — EXISTING, untouched.
- `tests/conftest.py` — shared `caplog` log-selection helper printing `[IMP:7-10]` lines (pattern from existing ui conftest).
- `tests/server/conftest.py` — slice-specific fixtures: `tmp_path_checkpointer`, `fake_llm_client`, `signed_token_factory`.

#### 5.2. Anti-Loop infrastructure

Every test suite uses the Anti-Loop `.test_counter.json` protocol (already present in `tests/ui/conftest.py`). Copy the pattern into `tests/server/conftest.py` with a separate counter file `tests/server/.test_counter.json`. Counter resets to 0 on full 100% PASS of the slice's test selection.

#### 5.3. Go-compat fixture (Slice A, task §7.1)

- `tests/server/fixtures/go_generated_tokens.json` committed with:
  - 3 positive tokens (varying session_id + exp), each with the secret used (hex-encoded).
  - 2 tampered tokens (flipped-byte sig, truncated payload).
  - 1 expired token.
  - `_generator` field: Go code snippet that produced them (for reproducibility).
- Python test loads each entry and asserts `verify_session_token` result matches `expected_outcome` field.

#### 5.4. Coverage

- `pytest --cov=src/server --cov-fail-under=95 -x`. Fail slice if < 95%.
- No coverage requirement on `src/features/decision_maker/` (existing code not under this TASK's coverage scope; only DI regression).

#### 5.5. Zero-knowledge verifier script

`scripts/verify_zero_knowledge.sh`:
```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
BAD1=$(grep -rn -E "\.user_id|UserID|user_id\s*[=:]" src/ || true)
BAD2=$(grep -rn -E "⚡|energy|billing|credit|balance|deduct" src/ || true)
if [ -n "$BAD1" ]; then echo "AC1 VIOLATION:"; echo "$BAD1"; exit 1; fi
if [ -n "$BAD2" ]; then echo "AC7 VIOLATION:"; echo "$BAD2"; exit 1; fi
echo "zero-knowledge checks OK"
```
Called from `tests/server/conftest.py::pytest_sessionstart` — fails the session if violations exist.

$END_SECTION_TestStrategy

---

$START_SECTION_NegativeConstraints
### 6. Negative Constraints and Positive Invariants (VERBATIM for subagent prompts)

These lines MUST be copied verbatim into each `Agent(mode-code)` prompt:

1. **DO NOT** create a new virtualenv or reinstall existing libraries. `venv_lesson_*/` are forbidden here.
2. **DO NOT** read files outside `/Users/a1111/Dev/CrabLink/flows/brainstorm/` except the explicitly listed read-only references (`/Users/a1111/Dev/CrabLink/crablink-gateway/kernel/sessiontoken/token.go` for Slice A; `/Users/a1111/Dev/CrabLink/TASK_brainstorm_mcp_integration.md` for all slices).
3. **DO NOT** touch `/Users/a1111/Dev/CrabLink/AppGraph.xml`. Only `flows/brainstorm/AppGraph.xml` may be written (Slice E only).
4. **DO NOT** modify `src/features/decision_maker/` beyond additive DI kwargs on the four public session functions (`start_session`, `resume_session`, `stream_session`, `stream_resume_session`). Graph topology, nodes.py, prompts.py, state.py, tools.py are frozen.
5. **DO NOT** modify `src/ui/` or `src/core/` (except additive helper `build_llm_client(cfg)` in `src/core/llm_client.py` during Slice B — additive function, existing `build_llm` untouched).
6. **DO NOT** import `BalanceReader`, `User`, `energy_`, or any gateway-side billing symbol. Any such import → immediate slice reject.
7. **DO NOT** hardcode `crablink.svc.cluster.local`, `gateway.internal`, or any CrabLink-specific URL in Python code. All URLs live in Config → env → k8s ConfigMap.
8. **DO NOT** log raw `Authorization` header, raw `HMAC_SECRET`, raw `API_KEY`, or raw user `message` at IMP ≥ 5. The only allowed fingerprint form is `sha256:<first-8-hex>`.
9. **DO NOT** add `TODO` / `FIXME` to production code. Open items go into `flows/brainstorm/BACKLOG.md` (Slice E).
10. **DO NOT** use `subprocess.run` for business-logic testing (core-rules mode-code §4).
11. **DO NOT** use `...`, `pass`, `etc.` abbreviations in generated code (core-rules Principle 2).
12. **DO NOT** self-register manifest (`POST /manifests/brainstorm` from within brainstorm). Manifest registration is operator-side via `k8s/brainstorm.manifest.json`.
13. **DO NOT** deploy multi-replica with sqlite checkpointer; manifest `replicas: 1` hardlocked with comment referencing this plan.

Positive invariants:

- **I1.** Log file naming: emit LDD to `stdout` only (structured lines; k8s collects). Do not create `.log` files in `/data`.
- **I2.** Entry point in Dockerfile: `CMD ["uvicorn", "src.server.app_factory:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]`.
- **I3.** Checkpointer WAL: sqlite with `PRAGMA journal_mode=WAL;` at `setup()` time for crash-safety on pod eviction.
- **I4.** Framework-host integrity pre-commit check: `git -C /Users/a1111/Dev/CrabLink status --porcelain -- ':(exclude)flows/brainstorm'` MUST print nothing (except the pre-existing untracked noise unrelated to this task — Architect will verify).
- **I5.** `TokenClaims` is `@dataclass(frozen=True)` with exactly three fields. `test_token_claims_strict_shape` asserts `set(f.name for f in fields(TokenClaims)) == {"service_id","session_id","exp"}`.

$END_SECTION_NegativeConstraints

---

$START_SECTION_LegacyPlanLinks
### 7. Legacy Plan Pointers

The following existing plans **remain authoritative for their respective scopes**; this plan extends the service boundary only and does not supersede them:

- `plans/DevelopmentPlan.md` v2.0.0 — authoritative for the async core of the decision_maker graph (nodes, prompts, state, tools, LangGraph topology). **Do not re-interpret or rewrite.** Slice B only adds DI kwargs to four public functions; everything else frozen.
- `plans/DevelopmentPlan_UI.md` v1.x — authoritative for Gradio UI (`src/ui/`, `scripts/run_brainstorm_ui.py`, `tests/ui/`). **Regression-only** during Slice B: UI must continue to boot.

This plan (`DevelopmentPlan_MCP.md` v1.0.0) adds an orthogonal HTTP+auth+deployment service boundary (`src/server/`, `k8s/`, `Dockerfile`, `scripts/smoke_brainstorm.py`, `scripts/mint_token.py`, `SECURITY.md`, `BACKLOG.md`).

$END_SECTION_LegacyPlanLinks

---

$START_SECTION_AcceptanceCriteria
### 8. Acceptance Criteria (Definition of Done)

Binding Acceptance Criteria AC1..AC8 from §1.5 apply **globally** — each slice verifies them within its own scope. Per-slice exit criteria (§2) layer on top.

Slice-level acceptance gates (Architect confirms before next slice):

- [ ] **SliceA_PASS:** Config + Auth modules committed; 95% coverage; Go-fixture tests green; verify_zero_knowledge.sh green; AC1/AC4/AC5/AC6/AC7 green.
- [ ] **SliceB_PASS:** Checkpoint factory + DI seams committed; roundtrip tests green; legacy `run_brainstorm_ui.py` smoke-launches; AC1..AC7 green on the diff.
- [ ] **SliceC_PASS:** FastAPI + sweeper + metrics committed; all task §7.3-§7.6 test scenarios green; route-auth coverage test green; idempotency test (AC8) green; 95% coverage on `src/server/*`.
- [ ] **SliceD_PASS:** Dockerfile + k8s manifests + manifest.json committed; `docker build` + `kubectl apply --dry-run=client` pass; manifest JSON-schema validates.
- [ ] **SliceE_PASS:** Smoke 7/7 PASS (manual); README + SECURITY.md + local AppGraph.xml + BACKLOG.md committed; root AppGraph.xml untouched; `verify_zero_knowledge.sh` green on full tree.

Task-level Definition of Done (copy from TASK §"Критерии приёмки"):

- [ ] `pytest -x --cov=src/server --cov-fail-under=95` PASS.
- [ ] `pytest -x` full run PASS.
- [ ] `scripts/smoke_brainstorm.py` 7/7 PASS.
- [ ] `docker build .` succeeds; `docker run` `/healthz` + `/readyz` return 200.
- [ ] `kubectl apply -k k8s/` on local kind: pod Running ≥ 5 min, readiness=true.
- [ ] Manual curl-check happy/sad paths.
- [ ] Prometheus scrape shows all declared metrics with correct labels.
- [ ] LDD coverage — `[IMP:4/5/7/9]` on correct events; **zero** raw HMAC tokens / API-keys.
- [ ] Semantic Exoskeleton checklist on all new files.
- [ ] Local `flows/brainstorm/AppGraph.xml` updated.
- [ ] Root `/Users/a1111/Dev/CrabLink/AppGraph.xml` **NOT** touched.
- [ ] `k8s/brainstorm.manifest.json` validates as JSON Schema draft-07.
- [ ] README + SECURITY.md merged.

$END_SECTION_AcceptanceCriteria

---

$START_SECTION_PostGate3Refinements
### 9. Post-Gate-3 Refinements (v1.1.0 addendum — binding)

These clarifications override earlier sections where in conflict.

#### 9.1. Idempotency (§1.5 AC8, §2.3, §4.1 step 10) — hybrid idempotency cache

- New module **Slice C**: `src/server/idempotency.py`. Contains `IdempotencyCache` wrapping `cachetools.TTLCache(maxsize=10000, ttl=600)` (thread-/async-safe; guard with `asyncio.Lock` on lookup+insert).
- `handle_turn` flow:
  1. Determine cache key. If request header `Idempotency-Key` present and matches `^[a-zA-Z0-9_\-]{8,128}$` → `key = header_value`. Else → `key = sha256(session_id + "\x00" + message + "\x00" + str(turn_n_read_from_checkpoint))[:32]`. `turn_n_read_from_checkpoint` is the current `len(messages)` of the checkpointed state (0 for new session).
  2. `if key in cache: return cached_reply (200), increment brainstorm_idempotent_hits_total, skip LLM call, skip checkpoint advance`.
  3. On first miss → run LLM → on success `cache.set(key, reply)`.
- Metric addition: `brainstorm_idempotent_hits_total{source="header"|"internal"}` (counter).
- Test `test_duplicate_turn_same_session_is_idempotent` (Slice C) splits into `test_idempotency_via_header` and `test_idempotency_via_internal_tuple`.
- Malformed `Idempotency-Key` (wrong regex) → treat as absent, fall back to internal tuple; log `[BRAINSTORM][IMP:5][Idempotency][MalformedKey] key_fp=sha256:<8hex>`.

#### 9.2. Correlation-ID middleware (§4.1 step 1) — accept + validate + fallback

- Middleware in Slice C (`src/server/middleware.py`):
  1. Read `X-Correlation-ID` header.
  2. Validate against `^[a-zA-Z0-9_-]{8,64}$`. Match → use as-is. Malformed → uuid4 + log `[BRAINSTORM][IMP:5][Trace][MalformedCorrelationID] got_fp=sha256:<8hex>`. Missing → uuid4 (no log).
  3. Attach to `request.state.correlation_id`.
  4. Copy into every response as `X-Correlation-ID` (even on errors).
- **Never** write the raw header value to logs without fingerprinting.

#### 9.3. `build_llm_client(cfg)` — env-isolation enforced

- Signature: `build_llm_client(cfg: Config) -> ChatOpenAI`. **No `os.environ` reads inside**; `cfg` is the sole input source.
- Test `test_build_llm_client_ignores_openrouter_env` (Slice B): `monkeypatch.setenv("OPENROUTER_API_KEY", "leak_detector")`, build client via `build_llm_client(cfg)` where `cfg.gateway_llm_api_key = SecretStr("real_key")`; assert client's `.openai_api_base` equals `cfg.gateway_llm_proxy_url` and client's `.openai_api_key` (revealed only for this assertion) does not contain `"leak_detector"`.

#### 9.4. Sweeper↔Turn race — touch-based exclusion (replaces §4.5 step 5)

- Every checkpoint read/write (invoked from `handle_turn`, `handle_done`) updates a `last_touched = now()` metadata entry for the thread_id. The adapter layer in `src/server/checkpoint_factory.py` wraps `saver.get` / `saver.put` with a thin `touch(thread_id)` side-effect writing to a dedicated `_brainstorm_meta` table (sqlite) / auxiliary column (postgres future).
- Sweeper predicate changes from `last_update < now - TTL` to `last_touched < now - SWEEP_THRESHOLD_SECS` where `SWEEP_THRESHOLD_SECS = 600` (default; config-overridable). `TURN_TIMEOUT_SEC = 120` remains; `SWEEP_THRESHOLD_SECS` must be ≥ `5 × TURN_TIMEOUT_SEC` (Config `model_validator` asserts).
- Accepted race window: turn running ≤ 600s safe; turn running > 600s sees session deleted, next write creates a new session (context loss, not data corruption). Documented in `SECURITY.md § Race Analysis`.
- `LockRegistry` approach deferred to `BACKLOG.md` (Postgres/multi-worker future).
- New test (Slice C) `test_sweeper_skips_recently_touched_session`: create checkpoint, touch at `t0`, invoke sweeper at `t0+SWEEP_THRESHOLD/2` → session preserved; invoke again at `t0+2*SWEEP_THRESHOLD` → session deleted.

#### 9.5. `--workers 1` — hardlock + k8s doc (§2.4)

- Dockerfile `CMD`: `["uvicorn","src.server.app_factory:create_app","--factory","--host","0.0.0.0","--port","8000","--workers","1"]`.
- `k8s/deployment.yaml` (or `statefulset.yaml`): add comment block:
  ```yaml
  # INVARIANT: replicas=1 and --workers=1 are hardlocked together for sqlite MVP.
  # Postgres-backed deployment MAY scale replicas but MUST NOT exceed --workers=1
  # per pod due to LangGraph in-memory state semantics.
  ```
- **HPA explicitly disabled** for MVP — add a commented-out `HorizontalPodAutoscaler` stub in `k8s/deployment.yaml` with note "# Enable only when switching to Postgres checkpointer; requires validation of graph-state sharing semantics."

#### 9.6. Requirements delta (v4 additions, updated)

```
# === v4.0.0 additions (MCP integration) ===
fastapi==0.115.4
uvicorn[standard]==0.32.0
httpx==0.27.2
prometheus-client==0.21.0
pydantic-settings==2.6.1
cachetools==5.5.0                    # TTLCache for Idempotency-Key header (Slice C, §9.1)
langgraph-checkpoint-postgres==2.0.2 # EXPERIMENTAL
# test-only
pytest-httpx==0.33.0
freezegun==1.5.1
```

$END_SECTION_PostGate3Refinements

$END_DOC_NAME
