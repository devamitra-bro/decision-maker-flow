# TEST GUIDE — Decision Maker (Scenario 1) — v2.1.0 LLM Sanitization
## For: mode-qa subagent independent verification
## Created: 2026-04-21 | Mode: mode-code v2.0.0 (async migration)
## Updated: 2026-04-21 | Mode: mode-code v2.1.0 (LLM sanitization via llm_utils)

---

## 1. How to Run the Tests

### Prerequisites

The project requires Python 3.12 (arm64) with packages installed to user site-packages.
The packages were installed via:
```bash
/opt/homebrew/bin/pip3.12 install --user --break-system-packages \
  langgraph==0.2.60 langchain-openai==0.2.10 langgraph-checkpoint-sqlite==2.0.1 \
  pydantic==2.9.2 python-dotenv==1.0.1 pytest==8.3.3 langsmith==0.1.147 \
  aiosqlite==0.20.0 tavily-python==0.5.0 pytest-asyncio==0.24.0
```

### Execute Tests

```bash
cd /Users/a1111/Dev/CrabLink/flows/brainstorm
PYTHONPATH="/Users/a1111/Library/Python/3.12/lib/python/site-packages:/Users/a1111/Dev/CrabLink/flows/brainstorm" \
  /opt/homebrew/bin/python3.12 -m pytest src/features/decision_maker/tests/ -s -v
```

**Expected output:** `29 passed in X.XXs` (26 original + 3 new from test_llm_utils.py parametrized + integration) — zero failures, zero skips.

### Environment Variables for Live Sessions (NOT Required for Tests)

Tests do NOT make network calls. For running live sessions via `start_session` / `resume_session`,
the following environment variables must be set in `.env` (copy from `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | YES | OpenRouter API secret key (sk-or-v1-...) |
| `OPENROUTER_MODEL` | YES | Model identifier (e.g. `x-ai/grok-4-fast`) |
| `TAVILY_API_KEY` | YES | Tavily search API key (tvly-...) — for production search |
| `LANGCHAIN_TRACING_V2` | No | Set to `true` to enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | No | LangSmith API key (required if tracing enabled) |
| `LANGCHAIN_PROJECT` | No | LangSmith project name |
| `LDD_LOG_LEVEL` | No | Override log level (default: `INFO`) |

---

## 2. Test Matrix and Key Assertions

### Test File 1: `test_routing.py` (3 tests — SYNC)

**Target:** `route_from_context` in `src/features/decision_maker/nodes.py`
**Note:** Routers remain SYNC in v2.0.0 — no `@pytest.mark.asyncio` needed.

| Test | State Input | Expected Result |
|---|---|---|
| `test_route_double_true_raises` | `_needs_data=True, _ready_for_weights=True` | Raises `DoubleTrueError` |
| `test_route_needs_data_returns_tool` | `_needs_data=True, _ready_for_weights=False` | Returns `"tool"` |
| `test_route_ready_returns_questioner` | `_needs_data=False, _ready_for_weights=True` | Returns `"questioner"` |

**LDD Markers to verify:**
- `[IMP:10]` log with `BLOCK_DOUBLE_TRUE_GUARD` and `SafetyTripped` (double-True test)
- `[IMP:9]` log with `route_from_context` and `BLOCK_ROUTING` (all tests)

---

### Test File 2: `test_anti_loop.py` (3 tests — ASYNC for cove_critique tests)

**Target:** `cove_critique` (async) + `route_from_critique` (sync) in nodes.py

| Test | Mode | State Input | Expected Result |
|---|---|---|---|
| `test_anti_loop_forces_approval` | `@pytest.mark.asyncio` | `rewrite_count=2`, `fake_llm` returns `needs_rewrite=True` | `rewrite_count` stays 2; `route_from_critique` returns `"finalize"` |
| `test_route_critique_finalize_at_cap` | sync | `critique_feedback` set, `rewrite_count=2` | Returns `"finalize"` |
| `test_anti_loop_imp10_log_emitted` | `@pytest.mark.asyncio` | `rewrite_count=2`, `fake_llm` injected | IMP:10 log with `BLOCK_ANTI_LOOP` and `SafetyTripped` |

**LDD Markers to verify:**
- `[LOGIC][IMP:10][cove_critique][BLOCK_ANTI_LOOP][SafetyTripped] rewrite_count>=2; forcing approval`
- `[BeliefState][IMP:9][cove_critique][BLOCK_STATE_WRITE][BusinessLogic]`

---

### Test File 3: `test_graph_compilation.py` (4 tests — SYNC)

**Target:** `build_graph(checkpointer)` in `src/features/decision_maker/graph.py`
**Note:** v2.0.0 build_graph accepts checkpointer via DI; uses `memory_checkpointer` fixture.

| Test | Assertion |
|---|---|
| `test_graph_compiles` | Graph is not None; has `invoke` and `ainvoke` methods |
| `test_all_node_ids_present` | All 7 IDs from scenario_1_flow.xml are in `graph.graph.nodes` |
| `test_checkpointer_is_base_checkpoint_saver` | `isinstance(graph.checkpointer, BaseCheckpointSaver)` |
| `test_interrupt_after_configured` | IMP:9 log confirms `interrupt_after=['3_Weight_Questioner']` |

**Required 7 node IDs:**
```
1_Context_Analyzer, 2_Tool_Node, 3_Weight_Questioner, 3.5_Weight_Parser,
4_Draft_Generator, 5_CoVe_Critique, 6_Final_Synthesizer
```

**LDD Markers to verify:**
- `[BeliefState][IMP:9][build_graph][BLOCK_COMPILE][BusinessLogic] Graph compiled successfully`
- Log must include `interrupt_after=['3_Weight_Questioner']`

---

### Test File 4: `test_json_utils.py` (6 tests — SYNC, UNCHANGED)

**Target:** `safe_json_parse` + `JsonParseError` in `src/core/json_utils.py`

| Test | Input | Expected Result |
|---|---|---|
| `plain_json` | `'{"a": 1}'` | Dict with `a=1` |
| `fenced_json_with_language` | `` ```json\n{"a": 2}\n``` `` | Dict with `a=2` |
| `fenced_json_bare` | `` ```\n{"a": 3}\n``` `` | Dict with `a=3` |
| `prose_embedded` | `"Here is: {"a": 4} — end."` | Dict with `a=4` |
| `test_safe_json_parse_malformed_raises` | `"No JSON here"` | Raises `JsonParseError` |
| `test_json_parse_error_snippet` | 300-char string | `raw_snippet` is first 200 chars |

---

### Test File 5: `test_async_core.py` (2 tests — ASYNC — NEW)

**Target:** `start_session` + `resume_session` in `src/features/decision_maker/graph.py`
**Note:** Uses `AsyncSqliteSaver` at `tmp_path` DB to prove checkpoint round-trip (AC10).

| Test | Assertion |
|---|---|
| `test_async_start_session_reaches_interrupt` | Graph runs to interrupt; `last_question` non-empty from weight_questioner |
| `test_async_resume_session_preserves_state` | Chains start+resume on SAME DB + SAME thread_id; final_answer, weights, draft_analysis all non-empty in final state |

**Key assertions:**
- `last_question` non-empty after first leg
- `final_answer` non-empty after second leg
- `weights` in final state (weight_parser ran)
- `draft_analysis` in final state (draft_generator ran)
- IMP:9 from `weight_questioner` and `final_synthesizer` both present in caplog

---

### Test File 6: `test_parallel_search.py` (2 tests — ASYNC — NEW)

**Target:** `tool_node` in `src/features/decision_maker/nodes.py`
**Note:** Proves asyncio.gather concurrency via wall-clock timing (AC11) and LDD pairs (AC13).

| Test | Assertion |
|---|---|
| `test_parallel_search_wall_clock` | 3 queries @ 0.2s = elapsed < 0.45s (parallel) vs 0.6s (sequential) |
| `test_parallel_search_ldd_pairs` | For each query, exactly 1 `[IMP:7]...[PENDING]` + 1 `[IMP:8]...[SUCCESS]` in caplog |

**LDD Markers to verify:**
- `[API][IMP:7][tool_node][BLOCK_EXECUTE_SEARCHES][ExternalCall] query={q!r} [PENDING]`
- `[API][IMP:8][tool_node][BLOCK_EXECUTE_SEARCHES][ResponseReceived] query={q!r} items=1 [SUCCESS]`

---

## 3. Log File Verification (AC20)

After running tests, verify `decision_maker.log` contains required LDD markers:

```bash
# File must exist
ls -la /Users/a1111/Dev/CrabLink/flows/brainstorm/decision_maker.log

# Must have at least 1 IMP:9 line from cove_critique
grep "\[IMP:9\]" /Users/a1111/Dev/CrabLink/flows/brainstorm/decision_maker.log | grep "cove_critique" | wc -l

# Must have multiple IMP:7 lines with distinct query= values
grep "\[IMP:7\]" /Users/a1111/Dev/CrabLink/flows/brainstorm/decision_maker.log | grep "query=" | head -10

# Must have matching IMP:8 SUCCESS lines
grep "\[IMP:8\]" /Users/a1111/Dev/CrabLink/flows/brainstorm/decision_maker.log | grep "query=" | head -10
```

---

## 4. Acceptance Criteria Verification Checklist

| AC | Criterion | How to Verify |
|---|---|---|
| AC1 | All files exist at exact paths | `ls` each file in §1 Topology |
| AC2 | Semantic exoskeleton on every .py | Read each file; check MODULE_CONTRACT, CHANGE_SUMMARY, MODULE_MAP, FUNCTION_CONTRACT |
| AC3 | No `...`, `pass`, `etc.` in production code | `grep -rn "\.\.\." src/features/decision_maker/ --include="*.py"` |
| AC4 | DecisionMakerState unchanged (13 fields) | Read `state.py`; count fields |
| AC5 | 7 node IDs match XML | `test_all_node_ids_present` PASSED |
| AC6 | Anti-Loop cap works | `test_anti_loop_forces_approval` PASSED (async) |
| AC7 | DoubleTrueError raised | `test_route_double_true_raises` PASSED |
| AC8 | start_session and resume_session are async def | Read `graph.py`; check `async def start_session` and `async def resume_session` |
| AC9 | AsyncSqliteSaver in production | Read `graph.py`; confirm no `sqlite3.connect` or `SqliteSaver` in production paths |
| AC10 | Hybrid checkpointer in tests | `test_async_core.py` uses AsyncSqliteSaver; `test_graph_compilation.py` uses MemorySaver |
| AC11 | Parallel search | `test_parallel_search_wall_clock` PASSED; elapsed < 0.45s |
| AC12 | Pluggable search adapter | Read `tools.py`; check `search_async` selects Tavily or stub based on env var |
| AC13 | LDD parallel integrity | `test_parallel_search_ldd_pairs` PASSED; per-query IMP:7/IMP:8 pairs verified |
| AC14 | pytest-asyncio installed | `pytest-asyncio==0.24.0` in requirements.txt; all async tests use `@pytest.mark.asyncio` |
| AC15 | No real network in tests | All async tests inject fake_llm + fake_search_async via DI; TAVILY_API_KEY not needed |
| AC16 | No hardcoded paths | All tests use `tmp_path`; production uses `Path(__file__)` relative path |
| AC17 | No venv created | Packages installed via --user flag |
| AC18 | No sibling-folder reads | Only authorized reference paths read outside target |
| AC19 | pytest green | `20 passed` in test output (>= 18 required) |
| AC20 | LDD on disk | `decision_maker.log` has IMP:9 from cove_critique AND IMP:7/IMP:8 with distinct query= values |

---

## 5. Anti-Loop Counter File

The Anti-Loop counter persists at:
```
/Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/.test_counter.json
```

Contents after a successful run: `{"failures": 0}`

If the counter is non-zero, the session start hook will print the Anti-Loop CHECKLIST
with v2.0.0 items including async-specific checks.

---

## 6. Key Architectural Decisions (for QA context)

1. **AsyncSqliteSaver over SqliteSaver (v2.0.0)** — Concept A architecture: start_session and
   resume_session each open `async with AsyncSqliteSaver.from_conn_string(path)` and pass
   the live checkpointer into `build_graph(checkpointer)` via DI. Tests use MemorySaver.
2. **build_graph(checkpointer) DI pattern** — The graph factory now accepts a pre-built
   checkpointer. This eliminates lifecycle coupling between graph compilation and SQLite
   connection management.
3. **Async node functions** — All 6 LLM nodes are now `async def` using
   `await _invoke_llm_async()` which calls `await llm.ainvoke(messages)`. Routers remain SYNC.
4. **asyncio.gather in tool_node** — Node 2 builds coroutines for all N queries and awaits
   `asyncio.gather(*coros)` — wall-clock time is max(single query latency) not sum.
5. **fake_llm async-aware interface** — In v2.0.0, fake_llm provides `.ainvoke()` (async) as
   the primary interface. `.invoke()` is retained as a compatibility fallback.
6. **interrupt_after=["3_Weight_Questioner"]** — Hard invariant. Unchanged from v1.
7. **Per-query LDD telemetry in search_async** — IMP:7 [PENDING] before call, IMP:8 [SUCCESS]
   after response, emitted inside `search_async` (or `fake_search_async` in tests). This
   satisfies AC13 LDD parallel integrity even when queries execute concurrently.
8. **TAVILY_API_KEY gating** — `search_async` reads env var at call-time (not import time)
   to ensure dotenv loading order is not a concern.
