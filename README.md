# Decision Maker — Agentic Dilemma Analysis Flow

A LangGraph-based multi-agent system that turns free-form personal dilemmas ("buy a car or use carsharing?", "relocate to Uruguay or Vietnam?") into structured, evidence-backed analyses. The pipeline performs context extraction, parallel web search, calibrated weight elicitation from the user (Human-in-the-Loop), draft generation with Chain-of-Verification (CoVe) self-critique, and a final synthesized recommendation.

A streaming Gradio 5 front-end exposes the agent's internal state in real time (State X-Ray panel), so every intermediate hypothesis, tool call, rewrite, and critique is visible to the user.

## Graph topology

```
┌──────────────────────┐
│ 1. Context Analyzer  │ ──┐
└──────────┬───────────┘   │ (needs more data)
           │               ▼
           │        ┌──────────────┐
           │        │ 2. Tool Node │  ← parallel Tavily search (asyncio.gather)
           │        └──────┬───────┘
           │ ready         │ loop until ready_for_weights
           ▼               │
┌──────────────────────┐   │
│ 3. Weight Questioner │ ◄─┘
└──────────┬───────────┘
           │ ★ interrupt_after — UI collects user answer
           ▼
┌──────────────────────┐
│ 3.5 Weight Parser    │   (structured extraction from user free-text)
└──────────┬───────────┘
           ▼
┌──────────────────────┐       ┌──────────────────────┐
│ 4. Draft Generator   │ ◄──── │ 5. CoVe Critique     │
└──────────┬───────────┘       └──────────┬───────────┘
           │                              │ rewrite (≤2)
           ▼                              │
           └──────► (loop back if critic flags issues) ──┘
           │ finalize
           ▼
┌──────────────────────┐
│ 6. Final Synthesizer │
└──────────────────────┘
```

- **Checkpointer:** `AsyncSqliteSaver` (per-call context manager) — full session state is durable across the HITL interrupt.
- **Rewrite cap:** 2 iterations (hard-coded in `route_from_critique`) to prevent runaway self-critique.
- **Search backend:** Tavily basic (1 credit per query), auto-falls-back to a deterministic stub when `TAVILY_API_KEY` is absent.

## Features

- **Streaming Agentic UX** — Gradio 5 Blocks with `astream(stream_mode="updates")`; each node completion yields a UI event (status line + State X-Ray JSON refresh).
- **Transparent state** — the right-hand JSON pane shows the live `DecisionMakerState` (dilemma, queries, tool_facts, weights, draft, critique, rewrite_count).
- **Human-in-the-Loop calibration** — the graph interrupts after Node 3, the UI unlocks the input box, and the user's natural-language answer is parsed into structured weights by Node 3.5.
- **CoVe self-critique visible** — when Node 5 requests a rewrite, the UI emits a dedicated `cove_rewrite` event so the user can see why the draft was rejected.
- **Vertical Feature Slicing** — `src/features/decision_maker/` is framework-agnostic and gradio-free; `src/ui/` is the sole gradio consumer. Controllers (`src/ui/controllers.py`) contain no gradio imports and are tested in isolation.
- **LDD 2.0 logging** — every node emits `[CLASSIFIER][IMP:N][FN][BLOCK][OP] ... [STATUS]` lines to `decision_maker.log` for post-hoc flow audit.

## Project structure

```
brainstorm/
├── src/
│   ├── core/                    # Logger, LLM client, JSON/LLM utils
│   └── features/
│       └── decision_maker/      # Framework-agnostic agentic core
│           ├── graph.py         # build_graph, start/resume/stream_* APIs
│           ├── nodes.py         # 6 node implementations + routing
│           ├── prompts.py       # System prompts per node
│           ├── state.py         # DecisionMakerState TypedDict
│           ├── tools.py         # search_async (Tavily + stub)
│           └── tests/           # Unit + integration tests
│   └── ui/                      # Gradio layer (sole gradio importer)
│       ├── app.py               # build_ui(), on_submit, on_new_session
│       ├── controllers.py       # orchestrate_start/_resume (framework-agnostic)
│       └── presenter.py         # Pure render(event, chat, state) → tuple
├── scripts/
│   ├── run_brainstorm_ui.py     # Launch Gradio UI on 127.0.0.1:7860
│   └── smoke_run.py             # Headless end-to-end CLI smoke test
├── tests/ui/                    # UI-layer tests (presenter, controllers, handlers)
├── plans/                       # Architectural development plans
├── AppGraph.xml                 # Semantic knowledge graph of the codebase
└── requirements.txt
```

## Quickstart

### 1. Install

```bash
git clone git@github.com:devamitra-bro/decision-maker-flow.git
cd decision-maker-flow
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# then edit .env and set:
#   OPENROUTER_API_KEY=sk-or-v1-...
#   TAVILY_API_KEY=tvly-...
```

Default model is `x-ai/grok-4-fast` via the OpenRouter OpenAI-compatible endpoint. Any OpenAI-compatible chat model with sufficient context (≥128k) should work; override via `OPENROUTER_MODEL` in `.env`.

### 3. Run the UI

```bash
python scripts/run_brainstorm_ui.py
# -> Gradio UI on http://127.0.0.1:7860
```

Or the headless CLI smoke test:

```bash
python scripts/smoke_run.py
```

## Usage walk-through

1. **Enter a dilemma** in free Russian or English ("Should I buy a 2024 used car or switch to carsharing for 20k km/year?").
2. **Watch the State X-Ray** — the right pane shows `dilemma`, extracted `search_queries`, growing `tool_facts`.
3. **Answer the calibration question** — Node 3 asks you to weight your priorities on a 1–10 scale. Node 3.5 parses your free-text answer into structured weights.
4. **Observe CoVe loop** — if Node 5 finds factual errors or logical gaps, it sends the draft back with a `critique_feedback` annotation visible in the State X-Ray.
5. **Read the final synthesis** — Node 6 packages the audited draft into a markdown recommendation in the chat panel.

## Testing

```bash
# All tests (both backend and UI)
pytest src/features/decision_maker/tests/ tests/ui/ -v

# UI layer only (headless — no gradio server spawned)
pytest tests/ui/ -v
```

UI tests use the Headless UI pattern: async handler generators are iterated directly (no browser, no `.launch()`). Presenter tests cover all 9 `UIEvent` kinds. An **Anti-Loop protocol** counter at `tests/ui/.test_counter.json` detects stuck pytest loops.

## Cost per session (representative)

For a moderately complex dilemma (e.g., multi-country relocation with 3 search rounds and 1 CoVe rewrite):

| Item | Volume | Cost |
|---|---:|---:|
| Tavily basic search | ~10 calls | ~$0.08 |
| LLM input tokens (Grok-fast) | ~65k | ~$0.013 |
| LLM output tokens | ~9k | ~$0.005 |
| **Total** | | **~$0.10** |

Web search dominates (~80% of cost). Caching identical normalized queries between tool_node iterations would cut that significantly.

## Architectural conventions

This codebase follows the **KiloCode Prompt Framework v3.1** Semantic Exoskeleton:

- Every module opens with a `MODULE_CONTRACT` block (PURPOSE, SCOPE, INPUTS, OUTPUTS, KEYWORDS, LINKS).
- Complex functions have `FUNCTION_CONTRACT` headers + multi-paragraph docstrings (SFT-priming for LLM maintainability).
- Non-linear internal logic is wrapped in `# START_BLOCK_X / # END_BLOCK_X` pairs (XML-DOM for code navigation).
- `AppGraph.xml` is the authoritative dependency map — regenerate after any structural change.

These conventions exist to make the code **maintainable by autonomous LLM agents** (Zero-Context Survival), not just humans.

## Known limitations

- **Linear weight aggregation** — the scoring formula is a weighted sum, which can mask Pareto-optimal trade-offs. Sensitivity analysis is not yet generated.
- **Weight Questioner is passive** — it only calibrates dimensions the user already named; does not propose missing ones.
- **CoVe catches facts, not framing** — the critic checks factual consistency against `tool_facts`, but does not challenge the decision framework itself.
- **Shallow search** — Tavily basic returns ~5 snippets/query. For high-stakes decisions, consider `search_depth="advanced"` (2 credits/call) or source whitelists.

## License

[MIT](LICENSE) © 2026 devamitra.

---

## MCP HTTP API

The brainstorm service exposes five HTTP endpoints. All endpoints except the health probes and `/metrics` require a valid `Authorization: Bearer v1.<payload>.<sig>` session token (see [Security model](#security-model)).

### `POST /turn`

Advance an active brainstorm session by one user message. Creates a new session if `session_id` is omitted.

**Request headers:**
- `Authorization: Bearer <token>` (required)
- `Idempotency-Key: <8-128 alphanumeric chars>` (optional; if provided, duplicate requests with the same key return a cached reply without re-invoking the LLM)
- `X-Correlation-ID: <8-64 alphanumeric chars>` (optional; echoed in response headers)

**Request body:**
```json
{ "message": "Should I buy a car or use carsharing?", "session_id": "optional-existing-uuid" }
```

**Response 200:**
```json
{ "session_id": "550e8400-e29b-41d4-a716-446655440000", "reply": "...", "state": "running", "metadata": {} }
```

**Error responses:**
- `401` — Missing or invalid token
- `403` — Token has wrong `service_id`
- `404` — `session_id` provided but no checkpoint exists
- `408` — LLM gateway timeout
- `400` — Message empty or > 4000 chars

### `POST /done`

Idempotently close a session and delete its checkpoint. Safe to call multiple times.

**Request body:** `{ "session_id": "<uuid>" }`

**Response 200:** `{ "acknowledged": true }`

### `GET /healthz`

Liveness probe. No auth. Returns `{ "status": "ok" }` unconditionally.

### `GET /readyz`

Readiness probe. No auth. Checks checkpointer ping + LLM gateway HTTP probe.

**Response 200:** `{ "status": "ready", "checkpointer": "ok", "llm_gateway": "ok" }`

**Response 503:** `{ "status": "not_ready", "checkpointer": "fail:<msg>", "llm_gateway": "fail:<msg>" }`

### `GET /metrics`

Prometheus metrics in text format. No auth.

Declared metric families:
`brainstorm_turns_total`, `brainstorm_turn_duration_seconds`, `brainstorm_llm_roundtrip_seconds`,
`brainstorm_active_sessions`, `brainstorm_done_total`, `brainstorm_token_verify_failures_total`,
`brainstorm_idempotent_hits_total`, `brainstorm_sweeper_runs_total`,
`brainstorm_sweeper_deleted_total`, `brainstorm_readyz_checks_total`.

---

## Configuration

All configuration is driven by environment variables. Secrets are injected via k8s Secrets; non-secret values come from a ConfigMap.

| Environment Variable | Python Field | Default | Required | Description |
|---|---|---|---|---|
| `BRAINSTORM_HMAC_SECRET` | `hmac_secret` | — | YES | Per-service HMAC-SHA256 key for session-token verification. Must differ from the gateway uber-key. |
| `GATEWAY_LLM_API_KEY` | `gateway_llm_api_key` | — | YES | API key for the LLM proxy endpoint. |
| `GATEWAY_LLM_PROXY_URL` | `gateway_llm_proxy_url` | — | YES | Base URL of the LLM proxy (e.g. `https://openrouter.ai/api/v1`). |
| `BRAINSTORM_CHECKPOINTER` | `checkpointer_kind` | `sqlite` | No | Checkpointer backend: `sqlite` (default, MVP) or `postgres` (EXPERIMENTAL). |
| `BRAINSTORM_SQLITE_PATH` | `sqlite_path` | `/data/checkpoints.sqlite` | No | Filesystem path for the SQLite checkpoint database. |
| `BRAINSTORM_CHECKPOINT_DSN` | `checkpoint_dsn` | `""` | No | Postgres DSN; used only when `BRAINSTORM_CHECKPOINTER=postgres`. |
| `BRAINSTORM_SESSION_TTL_SEC` | `session_ttl_sec` | `1800` | No | Idle session TTL in seconds before sweeper deletes checkpoint. |
| `BRAINSTORM_TURN_TIMEOUT_SEC` | `turn_timeout_sec` | `120` | No | Timeout in seconds for a single /turn LLM call. |
| `BRAINSTORM_SWEEP_INTERVAL_SEC` | `sweep_interval_sec` | `60` | No | Interval between sweeper scan cycles. |
| `BRAINSTORM_SWEEP_THRESHOLD_SECS` | `sweep_threshold_secs` | `600` | No | Session inactivity threshold for sweeper deletion. Must be >= 5 × turn_timeout_sec. |
| `BRAINSTORM_LLM_MODEL` | `llm_model` | `gpt-4o-mini` | No | LLM model identifier passed to the proxy. |
| `GRADIO_UI` | `gradio_ui` | `false` | No | Set `true` to enable legacy Gradio UI for local development. Never set in prod. |
| `LOG_LEVEL` | `log_level` | `INFO` | No | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `METRICS_PORT` | `metrics_port` | `9090` | No | Port for /metrics (0 = serve on main port). |

---

## Deployment

### Docker (standalone)

```bash
docker build -t brainstorm-mcp:latest .

docker run \
  -e BRAINSTORM_HMAC_SECRET=your-secret-min-32-chars \
  -e GATEWAY_LLM_PROXY_URL=https://openrouter.ai/api/v1 \
  -e GATEWAY_LLM_API_KEY=sk-or-v1-... \
  -p 8000:8000 \
  brainstorm-mcp:latest
```

Health check:
```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

### Kubernetes (kustomize)

```bash
# 1. Copy and fill secrets
cp k8s/secret.example.yaml k8s/secret.yaml
# Edit k8s/secret.yaml: set BRAINSTORM_HMAC_SECRET and GATEWAY_LLM_API_KEY

# 2. Edit k8s/configmap.yaml: set GATEWAY_LLM_PROXY_URL

# 3. Apply all manifests
kubectl apply -k k8s/

# 4. Watch pod readiness (expect Running within ~30s after image pull)
kubectl get pods -l app=brainstorm-mcp -w

# 5. Wait for readiness probe
kubectl wait --for=condition=Ready pod -l app=brainstorm-mcp --timeout=120s
```

**Invariant:** `replicas: 1` and `--workers 1` are hardlocked together for the sqlite MVP. Do NOT scale replicas without switching to the Postgres checkpointer (see BACKLOG.md).

---

## Manifest registration

Brainstorm does NOT self-register. The operator registers the manifest with the gateway after deployment:

```bash
# Port-forward or use the gateway's cluster-internal address
kubectl port-forward svc/crablink-gateway 7000:7000 -n crablink &

curl -X POST http://127.0.0.1:7000/manifests/brainstorm \
  -H "Content-Type: application/json" \
  -d @k8s/brainstorm.manifest.json
```

The manifest file (`k8s/brainstorm.manifest.json`) declares the two MCP tools (`brainstorm__turn`, `brainstorm__done`), their input schemas, and the endpoint URL pointing to the brainstorm k8s service.

---

## Security model

Brainstorm implements a **zero-knowledge domain** security model: it verifies cryptographic identity (session token) without knowing anything about user identity, billing, or CrabLink-internal topology.

Token verification uses HMAC-SHA256 in the Go-compatible `v1.<b64url_payload>.<b64url_sig>` wire format (per-service shared secret `BRAINSTORM_HMAC_SECRET`). Comparison is constant-time (`hmac.compare_digest`). No raw secrets, tokens, or user messages are ever logged — only `sha256:<first-8-hex>` fingerprints at IMP ≥ 5.

See [SECURITY.md](SECURITY.md) for the full threat model, race analysis, and operational secret rotation procedure.
