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

Not yet specified. All rights reserved by the author pending license choice.
