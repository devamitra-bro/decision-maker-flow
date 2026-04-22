# FILE: plans/DevelopmentPlan.md
# VERSION: 2.0.0
# TARGET_PROJECT: /Users/a1111/Dev/CrabLink/flows/brainstorm
# TASK_SPEC_SOURCE: /Users/a1111/Dev/CrabLink/flows/TASK_INFRA_UPGRADE.md
# PROTOCOLS: devplan-protocol + document-protocol + core-rules (semantic exoskeleton)

$START_DOC_NAME DevelopmentPlan_DecisionMaker_InfraUpgrade_PathA

**PURPOSE:** End-to-end engineering contract for migrating the existing synchronous `decision_maker` backend to an asynchronous core (Async LangGraph + AsyncSqliteSaver) and integrating a real pluggable search engine (Tavily) with parallel query execution. Binds architectural decisions taken during Architect HITL Gates 1 and 2 of cycle v2 into a machine-interpretable plan ready for consumption by the `mode-code` subagent. Business logic, prompts, and graph topology (7 nodes + edges) are hard-frozen; ONLY infrastructure changes.
**SCOPE:** Async migration of `_invoke_llm`, all 6 LLM node functions, `tool_node`, `build_graph`, `start_session`, `resume_session`; introduction of Tavily-backed `search_async` pluggable adapter; `AsyncSqliteSaver` in production + `MemorySaver` in tests (hybrid checkpointer); `pytest-asyncio` migration of all 16 existing tests + 2 new parallelism-proving tests; LDD log integrity across concurrent branches.
**KEYWORDS:** `DOMAIN(Decision_Support): WeightedChoice; CONCEPT(Orchestration): AsyncLangGraph; CONCEPT(AntiHallucination): CoVe; PATTERN(Architecture): VFS; PATTERN(Reliability): AntiLoop; TECH(LLM): OpenRouter; TECH(Persistence): AsyncSqliteSaver; TECH(Search): TavilyAsyncClient; PATTERN(Concurrency): AsyncioGather; PATTERN(Observability): LDD`.

$START_DOCUMENT_PLAN
### Document Plan
<!-- AI-Agent: All sections and artifacts declared below MUST be expanded in order. -->

**SECTION_GOALS:**
- GOAL [Migrate orchestration core (session API, graph, checkpointer, LLM wrapper, tool_node) to async/await without altering the 7-node topology or the public contract of start_session / resume_session] => GOAL_ASYNC_MIGRATION
- GOAL [Replace stub_search with a pluggable Tavily-backed async adapter executed in parallel via asyncio.gather for multi-query payloads] => GOAL_REAL_SEARCH
- GOAL [Preserve safety invariants (DoubleTrueError, Anti-Loop rewrite_count cap) verbatim in the async execution model and prove them under pytest-asyncio] => GOAL_SAFETY_PRESERVED
- GOAL [Keep LDD telemetry IMP:1-10 format unchanged while correctly interleaving IMP:7 / IMP:8 lines from N concurrent search queries] => GOAL_LDD_PARALLEL_INTEGRITY
- GOAL [Freeze business logic — prompts.py, state.py, and all node semantics (what they write to state) are untouched] => GOAL_BUSINESS_LOGIC_FREEZE

**SECTION_USE_CASES:**
- USE_CASE [Architect delegates the async-migration feature slice to mode-code with one plan link] => UC_DELEGATE_SLICE
- USE_CASE [mode-qa audits the async implementation strictly against Acceptance Criteria (AC1-AC20)] => UC_QA_AUDIT
- USE_CASE [External async caller awaits start_session(...) and resume_session(...) as regular coroutines] => UC_EXTERNAL_ASYNC_CALLER
- USE_CASE [Root Architect performs final log-to-code alignment and regenerates AppGraph.xml reflecting async topology] => UC_FINAL_REVIEW

$END_DOCUMENT_PLAN

---

$START_SECTION_SCOPE_AND_TOPOLOGY
### 1. Scope, Classification, and Topology

$START_ARTIFACT_Classification
#### Artifact: Task Classification

**TYPE:** DECISION
**KEYWORDS:** `PATTERN(Architecture): VFS; CONCEPT(Migration): SyncToAsync`.

$START_CONTRACT
**PURPOSE:** Fix classifier hints so the `mode-code` subagent's Attention routes to the correct workflow sections (Plugin-System tests branch; no Launcher design step).
**DESCRIPTION:** System(Architect) -> ClassifyTask -> ClassifierHintsLocked.
**RATIONALE:** The feature remains a pure backend Python slice with no UI, no lesson wrapper, and no sibling dependencies. The task is a refactor of an existing codebase rather than greenfield; the subagent must be aware so it reads the existing files first.
**ACCEPTANCE_CRITERIA:** The subagent's first console line contains both `PROJECT_TYPE_DEFINED: Plugin System` and `TASK_TYPE_DEFINED: Code and Tests`.
$END_CONTRACT

$START_BODY
- `PROJECT_TYPE = Plugin System`
- `TASK_TYPE = Code and Tests`
- Targeted refactor inside an existing target directory populated by cycle v1.
$END_BODY

$END_ARTIFACT_Classification

$START_ARTIFACT_Topology
#### Artifact: File Topology (Delta vs v1.0.0)

**TYPE:** DATA_FORMAT
**KEYWORDS:** `PATTERN(Architecture): VerticalFeatureSliced; CONCEPT(Migration): IsomorphicLayout`.

$START_CONTRACT
**PURPOSE:** Freeze the file layout — layout is isomorphic to v1 with no added directories and two added test files. No existing file is deleted; all are edited in place.
**DESCRIPTION:** System(mode-code) -> EditFilesAtAbsolutePaths -> DirectoryLayoutMatchesContract.
**RATIONALE:** Minimising layout churn preserves import paths and test discovery. New tests for parallelism live alongside existing ones.
**ACCEPTANCE_CRITERIA:** Every file listed below exists under the stated absolute path; no extra files are created outside this list.
$END_CONTRACT

$START_BODY
Absolute root: `/Users/a1111/Dev/CrabLink/flows/brainstorm`.

```text
brainstorm/
├── requirements.txt                             [EDIT — add 3 async libs]
├── .env.example                                 [EDIT — add TAVILY_API_KEY]
├── decision_maker.log                           [produced at runtime]
├── checkpoints.sqlite                           [produced at runtime — async DB file]
├── plans/
│   └── DevelopmentPlan.md                       [THIS DOCUMENT, v2.0.0]
├── AppGraph.xml                                 [regenerated at finalization]
├── src/
│   ├── __init__.py                              [unchanged]
│   ├── core/
│   │   ├── __init__.py                          [unchanged]
│   │   ├── llm_client.py                        [unchanged — ChatOpenAI supports .ainvoke() natively]
│   │   ├── logger.py                            [unchanged — LDD handler is sync-safe for async callers]
│   │   └── json_utils.py                        [unchanged — pure function]
│   └── features/
│       └── decision_maker/
│           ├── __init__.py                      [unchanged — re-exports start_session, resume_session]
│           ├── state.py                         [unchanged — state schema frozen]
│           ├── prompts.py                       [unchanged — prompt constants frozen]
│           ├── tools.py                         [EDIT — add search_async (Tavily) + keep stub_search_async fallback]
│           ├── nodes.py                         [EDIT — async def all 6 LLM nodes + tool_node + _invoke_llm_async; routers stay sync]
│           ├── graph.py                         [EDIT — build_graph accepts checkpointer; async start_session/resume_session; AsyncSqliteSaver lifecycle]
│           └── tests/
│               ├── __init__.py                  [unchanged]
│               ├── conftest.py                  [EDIT — async fake_llm, fake_search_async, event_loop, MemorySaver fixtures]
│               ├── test_guide.md                [EDIT — async smoke-path description]
│               ├── test_routing.py              [EDIT — async context_analyzer invocation where applicable; route fn remains sync]
│               ├── test_anti_loop.py            [EDIT — async cove_critique invocation; asserts IMP:10 Anti-Loop still emitted]
│               ├── test_graph_compilation.py    [EDIT — async build with MemorySaver; asserts checkpointer isinstance BaseCheckpointSaver]
│               ├── test_json_utils.py           [unchanged — pure parser tests]
│               ├── test_async_core.py           [NEW — compiles with AsyncSqliteSaver; resume after interrupt_after preserves state]
│               └── test_parallel_search.py      [NEW — asserts N queries executed concurrently via timed fake_search_async]
```
$END_BODY

$START_LINKS
**IMPLEMENTS:** GOAL_ASYNC_MIGRATION, GOAL_BUSINESS_LOGIC_FREEZE
**IMPACTS:** UC_DELEGATE_SLICE
$END_LINKS

$END_ARTIFACT_Topology

$END_SECTION_SCOPE_AND_TOPOLOGY

---

$START_SECTION_TECH_STACK
### 2. Technology Stack (Delta)

$START_ARTIFACT_Requirements
#### Artifact: `requirements.txt` (additions with per-line WHY)

**TYPE:** TOOL
**KEYWORDS:** `TECH(Async): AsyncIO; TECH(Search): Tavily; TECH(Testing): PytestAsyncio`.

$START_CONTRACT
**PURPOSE:** Lock async library versions with per-line rationale comments (core-rules §3 requires WHY-comments). Preserve all v1 pins unchanged.
**DESCRIPTION:** System(mode-code) -> InstallDependencies -> EnvironmentReadyForAsyncImport.
**RATIONALE:** `langgraph-checkpoint-sqlite==2.0.1` already ships `AsyncSqliteSaver` via `langgraph.checkpoint.sqlite.aio`, backed by `aiosqlite`. Pinning `aiosqlite` explicitly guarantees reproducibility. `tavily-python` ≥0.5 exposes native `AsyncTavilyClient`. `pytest-asyncio` is required to run `@pytest.mark.asyncio` coroutines.
**ACCEPTANCE_CRITERIA:** `python -c "import aiosqlite, tavily, pytest_asyncio; from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver; from tavily import AsyncTavilyClient; print('OK')"` exits 0.
$END_CONTRACT

$START_BODY
```txt
# === v1.0.0 pins (unchanged) ===
langgraph==0.2.60
langgraph-checkpoint-sqlite==2.0.1   # ships AsyncSqliteSaver via langgraph.checkpoint.sqlite.aio
langchain-core==0.3.21
langchain-openai==0.2.10             # ChatOpenAI.ainvoke() supported since 0.2.x
pydantic==2.9.2
python-dotenv==1.0.1
pytest==8.3.3
langsmith==0.1.147

# === v2.0.0 additions ===
aiosqlite==0.20.0                    # AsyncSqliteSaver backing driver; pin for reproducibility (Criterion C1)
tavily-python==0.5.0                 # AsyncTavilyClient — native async search; requires TAVILY_API_KEY (Criterion C3)
pytest-asyncio==0.24.0               # @pytest.mark.asyncio + event_loop fixture (Criterion C5 + Stage-III tests)
```
$END_BODY

$END_ARTIFACT_Requirements

$START_ARTIFACT_DotEnvExample
#### Artifact: `.env.example` (augmented)

**TYPE:** DATA_FORMAT
**KEYWORDS:** `CONCEPT(Security): EnvIsolation; TECH(Search): Tavily`.

$START_CONTRACT
**PURPOSE:** Document the Tavily API key the feature now consumes, alongside existing OpenRouter and optional LangSmith keys.
**DESCRIPTION:** System(Developer) -> CopyEnvExampleToDotEnv -> CredentialsLoaded.
**RATIONALE:** The new `search_async` adapter reads `TAVILY_API_KEY` at runtime. Tests MUST NOT read this key — they inject a `fake_search_async` via DI.
**ACCEPTANCE_CRITERIA:** File contains `TAVILY_API_KEY` key with placeholder value; no real secret committed.
$END_CONTRACT

$START_BODY
```ini
# --- Required — LLM ---
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=x-ai/grok-4-fast               # default LLM for all nodes (2M ctx, agentic tool-calling)

# --- Required — Search (v2.0.0) ---
TAVILY_API_KEY=tvly-...                          # secret; consumed by AsyncTavilyClient in tools.search_async

# --- Optional (LangSmith tracing) ---
# LANGCHAIN_TRACING_V2=true
# LANGCHAIN_API_KEY=ls__...
# LANGCHAIN_PROJECT=decision_maker_scenario_1
```
$END_BODY

$END_ARTIFACT_DotEnvExample

$END_SECTION_TECH_STACK

---

$START_SECTION_DEV_PLAN_CORE
### 3. Development Plan Core (devplan-protocol)

$START_DEV_PLAN

**PURPOSE:** Machine-consumable plan covering the Draft Code Graph, Step-by-step Data Flow, and Acceptance Criteria required for `mode-code` to implement the async infrastructure upgrade without re-architecting, and for `mode-qa` to verify it impartially.

---

#### 3.1. Draft Code Graph (v2.0.0 — async)

```xml
<DraftCodeGraph>

  <llm_client_py FILE="src/core/llm_client.py" TYPE="UTILITY_MODULE">
    <annotation>UNCHANGED. OpenRouter-backed ChatOpenAI factory. ChatOpenAI instances natively support `await llm.ainvoke(messages)` in langchain-openai 0.2.x — no code change required.</annotation>
  </llm_client_py>

  <logger_py FILE="src/core/logger.py" TYPE="UTILITY_MODULE">
    <annotation>UNCHANGED. Standard library logging is thread- and event-loop-safe; async callers emit via the same named logger without contention.</annotation>
  </logger_py>

  <json_utils_py FILE="src/core/json_utils.py" TYPE="UTILITY_MODULE">
    <annotation>UNCHANGED. safe_json_parse is a pure CPU function with zero I/O.</annotation>
  </json_utils_py>

  <state_py FILE="src/features/decision_maker/state.py" TYPE="DATA_SCHEMA_MODULE">
    <annotation>UNCHANGED. State schema frozen — prevents graph-topology drift.</annotation>
  </state_py>

  <prompts_py FILE="src/features/decision_maker/prompts.py" TYPE="CONSTANTS_MODULE">
    <annotation>UNCHANGED. Business logic freeze — Node prompts and GLOBAL_PRIMING are verbatim constants.</annotation>
  </prompts_py>

  <tools_py FILE="src/features/decision_maker/tools.py" TYPE="UTILITY_MODULE" CHANGE="EDITED">
    <annotation>Becomes an async pluggable-adapter module. Replaces the stub with a Tavily-backed async client while keeping the deterministic `stub_search_async` for dev/test fallbacks. Public callable `search_async(query)` is the sole surface consumed by `tool_node`.</annotation>

    <tools_search_async_FUNCTION NAME="search_async" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Public async entry — delegates to the default adapter resolved at runtime (Tavily if TAVILY_API_KEY is set, else stub_search_async). Returns List[Dict[query,result,source]] so the shape matches tool_facts list semantics. Emits [IMP:7] BEFORE the outbound call and [IMP:8] on response per-query.</annotation>
      <CrossLinks>
        <Link TARGET="nodes_tool_node_FUNCTION" TYPE="IS_USED_BY" />
      </CrossLinks>
    </tools_search_async_FUNCTION>

    <tools_stub_search_async_FUNCTION NAME="stub_search_async" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Deterministic async stub: `await asyncio.sleep(0)` + returns [{"query": q, "result": "&lt;stubbed-fact&gt;", "source": "stub"}]. Present for offline/test use; selected automatically when TAVILY_API_KEY is absent.</annotation>
    </tools_stub_search_async_FUNCTION>

    <tools__build_tavily_adapter_FUNCTION NAME="_build_tavily_adapter" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Private factory — constructs AsyncTavilyClient(api_key=...) and returns a bound async callable `async def adapter(query: str) -> List[Dict]` that normalises Tavily's `.search(query)` response to the {query,result,source} shape.</annotation>
    </tools__build_tavily_adapter_FUNCTION>
  </tools_py>

  <nodes_py FILE="src/features/decision_maker/nodes.py" TYPE="BUSINESS_LOGIC_MODULE" CHANGE="EDITED">
    <annotation>All 6 LLM nodes become `async def`. `_invoke_llm` is replaced by `_invoke_llm_async` using `await llm.ainvoke(messages)`. `tool_node` becomes async and executes N search queries concurrently via `asyncio.gather`. Routers (`route_from_context`, `route_from_critique`) remain SYNC — they are pure state inspectors with no I/O. DoubleTrueError and Anti-Loop cap are preserved verbatim. DI parameter renamed to `llm_factory` (unchanged) + new `search_fn` parameter on `tool_node` for search adapter injection in tests.</annotation>

    <nodes_DoubleTrueError_CLASS NAME="DoubleTrueError" TYPE="IS_CLASS_OF_MODULE">
      <annotation>UNCHANGED class. Still raised only inside sync router route_from_context.</annotation>
    </nodes_DoubleTrueError_CLASS>

    <nodes__invoke_llm_async_FUNCTION NAME="_invoke_llm_async" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Private async helper. Signature: `async def _invoke_llm_async(llm: Any, system_text: str, human_text: str) -> str`. Builds `[SystemMessage, HumanMessage]`, calls `await llm.ainvoke(messages)`, returns `response.content`. Emits `[API][IMP:7]` PENDING before await and `[API][IMP:8]` SUCCESS after response. Removes the synchronous predecessor `_invoke_llm`.</annotation>
    </nodes__invoke_llm_async_FUNCTION>

    <nodes_context_analyzer_FUNCTION NAME="context_analyzer" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Node 1 — async. `async def context_analyzer(state, llm_factory=None) -> dict`. Internally `await _invoke_llm_async(...)`. Post-parse state write is identical to v1. LDD markers IMP:5/IMP:7/IMP:8/IMP:9/IMP:10 unchanged in format.</annotation>
      <CrossLinks>
        <Link TARGET="nodes__invoke_llm_async_FUNCTION" TYPE="CALLS_FUNCTION" />
        <Link TARGET="json_utils_safe_json_parse_FUNCTION" TYPE="CALLS_FUNCTION" />
      </CrossLinks>
    </nodes_context_analyzer_FUNCTION>

    <nodes_tool_node_FUNCTION NAME="tool_node" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Node 2 — async + parallel. `async def tool_node(state, search_fn=None) -> dict`. Resolves `search_fn` (default: `tools.search_async`). Builds `coros = [search_fn(q) for q in state.search_queries]` and awaits `results = await asyncio.gather(*coros)`. Flattens and appends to `tool_facts`. Parallel-execution invariant: for N queries the wall-time must be close to max(single query) rather than sum.</annotation>
      <CrossLinks>
        <Link TARGET="tools_search_async_FUNCTION" TYPE="CALLS_FUNCTION" />
      </CrossLinks>
    </nodes_tool_node_FUNCTION>

    <nodes_weight_questioner_FUNCTION NAME="weight_questioner" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Node 3 — async. Identical body to v1 except `async def` + `await _invoke_llm_async`. Graph still interrupts AFTER this node via interrupt_after.</annotation>
    </nodes_weight_questioner_FUNCTION>

    <nodes_weight_parser_FUNCTION NAME="weight_parser" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Node 3.5 — async. Identical body to v1 except `async def` + `await _invoke_llm_async`.</annotation>
    </nodes_weight_parser_FUNCTION>

    <nodes_draft_generator_FUNCTION NAME="draft_generator" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Node 4 — async. Identical body to v1 except `async def` + `await _invoke_llm_async`.</annotation>
    </nodes_draft_generator_FUNCTION>

    <nodes_cove_critique_FUNCTION NAME="cove_critique" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Node 5 — async. Identical body to v1 — Anti-Loop cap (rewrite_count &gt;= 2 → force needs_rewrite=False) preserved VERBATIM, IMP:10 BLOCK_ANTI_LOOP log line unchanged.</annotation>
    </nodes_cove_critique_FUNCTION>

    <nodes_final_synthesizer_FUNCTION NAME="final_synthesizer" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>Node 6 — async. Identical body to v1 except `async def` + `await _invoke_llm_async`.</annotation>
    </nodes_final_synthesizer_FUNCTION>

    <nodes_route_from_context_FUNCTION NAME="route_from_context" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>SYNC. Pure state inspector — no I/O. Signature unchanged. Still raises DoubleTrueError on double-True. LangGraph accepts mixed sync/async across nodes and routers.</annotation>
    </nodes_route_from_context_FUNCTION>

    <nodes_route_from_critique_FUNCTION NAME="route_from_critique" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>SYNC. Pure state inspector — no I/O. Signature unchanged.</annotation>
    </nodes_route_from_critique_FUNCTION>
  </nodes_py>

  <graph_py FILE="src/features/decision_maker/graph.py" TYPE="ORCHESTRATION_MODULE" CHANGE="EDITED">
    <annotation>Graph assembly factored into a sync `build_graph(checkpointer)` that wires the topology AND accepts an already-constructed checkpointer via Dependency Injection (no more internal SqliteSaver construction). Public API `start_session` / `resume_session` become `async def` and internally open `AsyncSqliteSaver.from_conn_string(path)` as an async context manager per call, then `await graph.ainvoke(...)` / `await graph.aupdate_state(...)`. This is the "per-call checkpointer context" pattern from Concept A — clean lifecycle, trivial test isolation (tests pass `MemorySaver()` directly into build_graph).</annotation>

    <graph_build_graph_FUNCTION NAME="build_graph" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>SYNC factory. Signature changes: `def build_graph(checkpointer)`. Caller is responsible for checkpointer lifecycle. Wires the same 7 nodes + conditional edges as v1 (topology frozen). `interrupt_after=["3_Weight_Questioner"]` preserved.</annotation>
      <CrossLinks>
        <Link TARGET="nodes_context_analyzer_FUNCTION" TYPE="REGISTERS_NODE" />
        <Link TARGET="nodes_tool_node_FUNCTION" TYPE="REGISTERS_NODE" />
        <Link TARGET="nodes_weight_questioner_FUNCTION" TYPE="REGISTERS_NODE" />
        <Link TARGET="nodes_weight_parser_FUNCTION" TYPE="REGISTERS_NODE" />
        <Link TARGET="nodes_draft_generator_FUNCTION" TYPE="REGISTERS_NODE" />
        <Link TARGET="nodes_cove_critique_FUNCTION" TYPE="REGISTERS_NODE" />
        <Link TARGET="nodes_final_synthesizer_FUNCTION" TYPE="REGISTERS_NODE" />
        <Link TARGET="nodes_route_from_context_FUNCTION" TYPE="REGISTERS_CONDITIONAL_EDGE" />
        <Link TARGET="nodes_route_from_critique_FUNCTION" TYPE="REGISTERS_CONDITIONAL_EDGE" />
      </CrossLinks>
    </graph_build_graph_FUNCTION>

    <graph_start_session_FUNCTION NAME="start_session" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>`async def start_session(user_input: str, thread_id: str, checkpoint_path: Optional[str] = None) -> dict`. Opens `async with AsyncSqliteSaver.from_conn_string(path) as cp:`, calls `graph = build_graph(cp)`, `await graph.ainvoke(initial_state, config)`, then `snapshot = await graph.aget_state(config)`. Return shape identical to v1: `{"status":"awaiting_user","question":last_question,"thread_id":...}`.</annotation>
    </graph_start_session_FUNCTION>

    <graph_resume_session_FUNCTION NAME="resume_session" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>`async def resume_session(user_answer: str, thread_id: str, checkpoint_path: Optional[str] = None) -> dict`. Opens the same async checkpointer ctx, rebuilds graph (stateless factory), `await graph.aupdate_state(config, {"user_answer": user_answer})`, `await graph.ainvoke(None, config)`, reads `snapshot = await graph.aget_state(config)`, returns `{"status":"done","final_answer":final_answer,"thread_id":...}`.</annotation>
    </graph_resume_session_FUNCTION>
  </graph_py>

  <tests_conftest_py FILE="src/features/decision_maker/tests/conftest.py" TYPE="TEST_INFRA_MODULE" CHANGE="EDITED">
    <annotation>Anti-Loop session hooks (.test_counter.json) preserved unchanged. NEW fixtures: (a) `event_loop` — pytest-asyncio session-scoped loop; (b) `memory_checkpointer` — yields fresh `MemorySaver()` per test; (c) `fake_llm` rewired to return an async callable (scripted AIMessage generator); (d) `fake_search_async` — async callable with configurable sleep delay used by test_parallel_search to prove concurrency. `ldd_capture(caplog)` helper unchanged.</annotation>
  </tests_conftest_py>

  <tests_test_routing_py FILE="src/features/decision_maker/tests/test_routing.py" TYPE="TEST_MODULE" CHANGE="EDITED">
    <annotation>Router tests remain SYNC (routers are sync). Double-True still raises DoubleTrueError; "needs_data only" → "tool"; "ready only" → "questioner". `@pytest.mark.asyncio` not required.</annotation>
  </tests_test_routing_py>

  <tests_test_anti_loop_py FILE="src/features/decision_maker/tests/test_anti_loop.py" TYPE="TEST_MODULE" CHANGE="EDITED">
    <annotation>Becomes `@pytest.mark.asyncio`. Seeds state with `rewrite_count=2`, awaits `cove_critique(state, llm_factory=fake_llm_factory)`, asserts returned dict has `rewrite_count` unchanged AND `[IMP:10][BLOCK_ANTI_LOOP][SafetyTripped]` log line present in caplog. Then calls SYNC `route_from_critique` → asserts "finalize".</annotation>
  </tests_test_anti_loop_py>

  <tests_test_graph_compilation_py FILE="src/features/decision_maker/tests/test_graph_compilation.py" TYPE="TEST_MODULE" CHANGE="EDITED">
    <annotation>Compiles graph using `memory_checkpointer` fixture (MemorySaver — no SQLite DB file). Asserts all 7 node IDs reachable from START and that `interrupt_after` includes "3_Weight_Questioner". Checkpointer isinstance check relaxed to `BaseCheckpointSaver` (abstract superclass of both MemorySaver and AsyncSqliteSaver) so the same test covers both paths.</annotation>
  </tests_test_graph_compilation_py>

  <tests_test_json_utils_py FILE="src/features/decision_maker/tests/test_json_utils.py" TYPE="TEST_MODULE">
    <annotation>UNCHANGED. Pure parser tests; no async.</annotation>
  </tests_test_json_utils_py>

  <tests_test_async_core_py FILE="src/features/decision_maker/tests/test_async_core.py" TYPE="TEST_MODULE" CHANGE="NEW">
    <annotation>NEW. Two `@pytest.mark.asyncio` tests: (1) `test_async_start_session_reaches_interrupt` — awaits start_session against AsyncSqliteSaver at tmp_path DB, asserts returned status="awaiting_user" and question non-empty, uses fake_llm for nodes 1..3; (2) `test_async_resume_session_preserves_state` — chains start+resume against the SAME tmp_path DB with the SAME thread_id, asserts final_answer non-empty AND that the graph state at end contains weights/draft_analysis/final_answer (proving AsyncSqliteSaver checkpoint round-trip survives event-loop transitions).</annotation>
    <CrossLinks>
      <Link TARGET="graph_start_session_FUNCTION" TYPE="TESTS" />
      <Link TARGET="graph_resume_session_FUNCTION" TYPE="TESTS" />
    </CrossLinks>
  </tests_test_async_core_py>

  <tests_test_parallel_search_py FILE="src/features/decision_maker/tests/test_parallel_search.py" TYPE="TEST_MODULE" CHANGE="NEW">
    <annotation>NEW. `@pytest.mark.asyncio` test proving concurrency. Constructs 3 queries. Injects `fake_search_async` that sleeps DELAY=0.2s and returns a deterministic dict. Awaits `tool_node(state, search_fn=fake_search_async)` and asserts wall-clock elapsed &lt; 0.5s (sequential would be ≥0.6s). Also asserts all 3 queries' IMP:7/IMP:8 lines present in caplog.</annotation>
    <CrossLinks>
      <Link TARGET="nodes_tool_node_FUNCTION" TYPE="TESTS" />
    </CrossLinks>
  </tests_test_parallel_search_py>

</DraftCodeGraph>
```

---

#### 3.2. Step-by-step Data Flow (async-augmented)

**Simulation mode:** dry-run the async graph mentally before generation.

1. **Session boot.** External async caller awaits `start_session(user_input, thread_id, checkpoint_path=None)`.
2. **Checkpointer open.** `path = checkpoint_path or _DEFAULT_CHECKPOINT_PATH`. Enter `async with AsyncSqliteSaver.from_conn_string(path) as cp:` — `aiosqlite` opens a non-blocking SQLite connection.
3. **Graph factory.** `graph = build_graph(cp)` — sync assembly of StateGraph + 7 nodes + conditional edges + `compile(checkpointer=cp, interrupt_after=["3_Weight_Questioner"])`.
4. **Initial state seed.** `initial_state = {"user_input": user_input, "tool_facts": [], "rewrite_count": 0}`; `config = {"configurable": {"thread_id": thread_id}}`.
5. **Async graph invoke (1st leg).** `await graph.ainvoke(initial_state, config)` — LangGraph drives Node 1 asynchronously.
6. **Node 1 `1_Context_Analyzer` (async).** Emits `[Flow][IMP:5]`, resolves `llm_factory` → `build_llm()` → `await _invoke_llm_async(llm, GLOBAL_PRIMING, human_text)`. `[API][IMP:7][context_analyzer][BLOCK_LLM_CALL][ExternalCall] [PENDING]` before await; `[API][IMP:8][...][ResponseReceived] [SUCCESS]` after. `safe_json_parse` is sync. `[BeliefState][IMP:9]` on state write.
7. **Router `route_from_context` (sync).** Unchanged behaviour — reads is_data_sufficient/search_queries/explicit test flags; raises `DoubleTrueError` on injected double-True; else returns `"tool"` or `"questioner"`.
8. **Node 2 `2_Tool_Node` (async + parallel).** Resolves `search_fn = search_fn or tools.search_async`. Constructs `coros = [search_fn(q) for q in state.search_queries]`. Before gather emits `[Flow][IMP:5]` entry line. Inside `search_async` (and inside `fake_search_async` in tests), per-query emit `[API][IMP:7][tool_node][BLOCK_EXECUTE_SEARCHES][ExternalCall] query={q!r} [PENDING]`. `results = await asyncio.gather(*coros)` resolves concurrently. Per-query completion emits `[API][IMP:8][tool_node][BLOCK_EXECUTE_SEARCHES][ResponseReceived] query={q!r} items={n} [SUCCESS]`. Flatten `new_facts = list(itertools.chain.from_iterable(results))`. `tool_facts = existing_facts + new_facts`. `[BeliefState][IMP:9]` on state write. Unconditional edge back to Node 1.
9. **Node 1 re-entry.** Same as step 6 with non-empty tool_facts in state. LLM instructed by NODE_1 prompt to transition to `ready_for_weights=true` on sufficient evidence.
10. **Node 3 `3_Weight_Questioner` (async).** Same as v1 but via `await _invoke_llm_async`. Writes `last_question`. **Graph interrupts** (interrupt_after triggered).
11. **First leg return.** `snapshot = await graph.aget_state(config)`; read `last_question`; EXIT async-with block (AsyncSqliteSaver closes aiosqlite conn cleanly). Return `{"status":"awaiting_user", "question": last_question, "thread_id": thread_id}`.
12. **Human reply.** Caller eventually awaits `resume_session(user_answer, thread_id, checkpoint_path=None)`.
13. **Checkpointer re-open.** New `async with AsyncSqliteSaver.from_conn_string(path)` — reopens the SAME SQLite file; LangGraph reloads checkpointed state keyed by `thread_id`.
14. **Inject user_answer.** `await graph.aupdate_state(config, {"user_answer": user_answer})`.
15. **Async graph invoke (2nd leg).** `await graph.ainvoke(None, config)` — LangGraph resumes from after-Node-3.
16. **Node 3.5 `3.5_Weight_Parser` (async).** `await _invoke_llm_async`; writes `weights` and optional `assumptions`.
17. **Node 4 `4_Draft_Generator` (async).** Writes `draft_analysis`.
18. **Node 5 `5_CoVe_Critique` (async).** Reads current `rewrite_count`. `await _invoke_llm_async`. **Anti-Loop cap (sync inside async):** if `rewrite_count >= 2`, override `needs_rewrite = False` and emit `[LOGIC][IMP:10][cove_critique][BLOCK_ANTI_LOOP][SafetyTripped]`. If `needs_rewrite` stays True, increment `rewrite_count`.
19. **Router `route_from_critique` (sync).** Returns "rewrite" iff `critique_feedback` non-empty AND `rewrite_count < 2`; else "finalize".
20. **Rewrite loop (≤1).** On "rewrite" → back to Node 4 with `critique_feedback` populated. Second CoVe pass is capped by Anti-Loop.
21. **Node 6 `6_Final_Synthesizer` (async).** Writes `final_answer`. Edge → END.
22. **Second leg return.** `snapshot = await graph.aget_state(config)`; read `final_answer`; exit async-with. Return `{"status": "done", "final_answer": final_answer, "thread_id": thread_id}`.

**LDD touchpoints (must be emitted and preserve format):**
- Every node entry: `[Flow][IMP:5][<node_fn>][BLOCK_ENTRY][StateRead]`.
- Every LLM call: `[API][IMP:7][<node_fn>][BLOCK_LLM_CALL][ExternalCall] [PENDING]` on request; `[API][IMP:8][...][ResponseReceived] [SUCCESS]` on reply.
- **Per-query search**: `[API][IMP:7][tool_node][BLOCK_EXECUTE_SEARCHES][ExternalCall] query={q!r} [PENDING]` + `[API][IMP:8][tool_node][BLOCK_EXECUTE_SEARCHES][ResponseReceived] query={q!r} items={n} [SUCCESS]` — ONE PAIR PER QUERY, interleaving is permitted and expected.
- Every state mutation: `[BeliefState][IMP:9][<node_fn>][BLOCK_STATE_WRITE][BusinessLogic]`.
- Anti-Loop trigger (verbatim preserved): `[LOGIC][IMP:10][cove_critique][BLOCK_ANTI_LOOP][SafetyTripped] rewrite_count>={n}; forcing approval (was needs_rewrite={b}) [VALUE]`.
- Parser failure: `[ParserError][IMP:10][<node_fn>][BLOCK_PARSE][ExceptionEnrichment]` with raw snippet.
- Double-True trigger (verbatim preserved): `[LOGIC][IMP:10][route_from_context][BLOCK_DOUBLE_TRUE_GUARD][SafetyTripped] FATAL: ... [FATAL]`.

---

#### 3.3. Acceptance Criteria

- [ ] **AC1 — File layout.** All files from §1 Topology exist at exact absolute paths; two new test files present (`test_async_core.py`, `test_parallel_search.py`); no unlisted file created.
- [ ] **AC2 — Semantic exoskeleton preserved.** Every edited `.py` module retains its `MODULE_CONTRACT`, updates `CHANGE_SUMMARY` (LAST_CHANGE=v2.0.0 — async migration), refreshes `MODULE_MAP` signatures. Every async function has `FUNCTION_CONTRACT` with `KEYWORDS` and `COMPLEXITY_SCORE`; Complexity>7 retains `START_BLOCK_*`/`END_BLOCK_*` segmentation.
- [ ] **AC3 — Zero abbreviations.** No `...`, `pass`, `etc.` placeholders in production code (existing empty marker classes allowed).
- [ ] **AC4 — State schema unchanged.** `DecisionMakerState` is NOT modified — exact same 13 TypedDict fields as v1.
- [ ] **AC5 — Graph topology unchanged.** Same 7 node IDs, same edges, `interrupt_after=["3_Weight_Questioner"]`.
- [ ] **AC6 — Anti-Loop preserved.** `cove_critique` still forces `needs_rewrite=False` when `rewrite_count>=2` and emits `[IMP:10][BLOCK_ANTI_LOOP]`. `test_anti_loop.py` passes under async.
- [ ] **AC7 — DoubleTrueError preserved.** `route_from_context` still raises on double-True. `test_routing.py` passes.
- [ ] **AC8 — Async public API.** `start_session` and `resume_session` are `async def`; existing return shapes unchanged (`{"status": ..., "question"/"final_answer": ..., "thread_id": ...}`).
- [ ] **AC9 — AsyncSqliteSaver in production.** `graph.py` uses `AsyncSqliteSaver.from_conn_string(path)` as async context manager inside `start_session`/`resume_session`; no `sqlite3.connect` or `SqliteSaver` remains in production paths.
- [ ] **AC10 — Hybrid checkpointer in tests.** `test_async_core.py` uses `AsyncSqliteSaver` at `tmp_path`; `test_graph_compilation.py` uses `MemorySaver` via `memory_checkpointer` fixture.
- [ ] **AC11 — Parallel search.** `tool_node` dispatches N queries via `asyncio.gather`; `test_parallel_search.py` asserts wall-clock elapsed is less than DELAY × N × 0.75 (so genuine parallelism is proved, not just "passes eventually").
- [ ] **AC12 — Pluggable search adapter.** `tools.search_async` selects Tavily when `TAVILY_API_KEY` is set, else `stub_search_async`. `tool_node` accepts `search_fn` DI parameter; production default resolved at call-time.
- [ ] **AC13 — LDD parallel integrity.** For each query `q`, exactly ONE `[IMP:7]...query={q!r}...[PENDING]` and ONE `[IMP:8]...query={q!r}...[SUCCESS]` appear in the log. Interleaving across queries is allowed. `test_parallel_search.py` asserts this.
- [ ] **AC14 — pytest-asyncio installed and active.** `pytest-asyncio==0.24.0` in requirements; `pyproject.toml` OR `conftest.py` configures `asyncio_mode = "auto"` OR all async tests carry `@pytest.mark.asyncio` explicitly (preferred: explicit marker — cognitive clarity).
- [ ] **AC15 — No real network in tests.** `test_async_core.py` and `test_parallel_search.py` MUST inject `fake_llm` and `fake_search_async` via DI; a test run with `TAVILY_API_KEY` and `OPENROUTER_API_KEY` UNSET MUST still pass.
- [ ] **AC16 — No hardcoded paths.** All tests use `tmp_path`. Production default checkpoint path remains computed relative to module file.
- [ ] **AC17 — No venv creation.** Subagent MUST NOT run `python -m venv`.
- [ ] **AC18 — No sibling-folder reads.** Subagent MUST NOT `Read` files outside the target root OR the explicit authorised-reference list below.
- [ ] **AC19 — pytest green.** `python -m pytest src/features/decision_maker/tests/ -s -v` reports 16 original + 2 new = **18 tests passing**; no `skipped`.
- [ ] **AC20 — LDD on disk.** After test run, `brainstorm/decision_maker.log` contains at least one `[IMP:9]` line from `cove_critique` AND multiple `[IMP:7]/[IMP:8]` lines from `tool_node` with distinct `query=` values.

$END_DEV_PLAN

$END_SECTION_DEV_PLAN_CORE

---

$START_SECTION_TESTING_STRATEGY
### 4. Testing Strategy (Plugin-System Mode, Async)

$START_ARTIFACT_TestMatrix
#### Artifact: Test Matrix (v2.0.0)

**TYPE:** NFR
**KEYWORDS:** `PATTERN(Testing): Atomic+Integration+Concurrency; PATTERN(Reliability): AntiLoop; PATTERN(Observability): LDD`.

$START_CONTRACT
**PURPOSE:** Map the 16 adapted tests + 2 new tests to files and LDD assertions so `mode-code` produces the exact matrix `mode-qa` will verify.
**DESCRIPTION:** System(mode-code) -> ProduceTestFiles -> TestMatrixMatchesContract.
**RATIONALE:** Task §3 requires existing 16 tests to be adapted under async (not rewritten from scratch). Two new tests prove the two new capabilities (async core + parallel search) with explicit assertions, per HITL Gate 1 Q3=(b).
**ACCEPTANCE_CRITERIA:** Every row below maps to a file listed in §1 Topology; every assertion appears in the produced file.
$END_CONTRACT

$START_BODY

| # | File | Mode | Count | Key assertions |
|---|------|------|-------|----------------|
| 1 | `test_routing.py` | sync | ~3 | double-True raises `DoubleTrueError`; `{needs_data:true}` → `"tool"`; `{ready_for_weights:true}` → `"questioner"` |
| 2 | `test_anti_loop.py` | async | ~3 | `rewrite_count=2` + await `cove_critique` → `needs_rewrite` forced False; `[IMP:10][BLOCK_ANTI_LOOP]` emitted; sync `route_from_critique` → `"finalize"` |
| 3 | `test_graph_compilation.py` | sync | ~4 | `build_graph(memory_checkpointer)` compiles; all 7 IDs reachable; checkpointer isinstance `BaseCheckpointSaver`; `interrupt_after` set |
| 4 | `test_json_utils.py` | sync | ~6 | 4+ positive parse modes; `JsonParseError.raw_snippet` populated on malformed |
| 5 | `test_async_core.py` | async | 2 | `async start_session` returns `awaiting_user`; `async resume_session` preserves state across event-loop boundary using AsyncSqliteSaver @ tmp_path |
| 6 | `test_parallel_search.py` | async | 2 | 3 queries @ 0.2s each complete in <0.5s via `asyncio.gather`; per-query IMP:7/IMP:8 both present in caplog |

**Total: 16 adapted + 2 new = 18 tests.**

**conftest.py responsibilities (v2.0.0):**
- `.test_counter.json` session lifecycle (UNCHANGED).
- `event_loop` session-scoped pytest-asyncio loop.
- `memory_checkpointer` fixture — `yield MemorySaver()` per-test.
- `fake_llm` fixture — returns an async-aware scripted callable (provides `.ainvoke(messages) -> AIMessage`).
- `fake_search_async` fixture — async callable with a configurable sleep delay.
- `ldd_capture(caplog)` helper — filters `[IMP:7-10]` lines and prints them to stdout before asserts.

$END_BODY

$END_ARTIFACT_TestMatrix

$END_SECTION_TESTING_STRATEGY

---

$START_SECTION_NEGATIVE_CONSTRAINTS
### 5. Verbatim Constraints and Invariants (for `mode-code` Prompt)

$START_ARTIFACT_ConstraintBlock
#### Artifact: Constraint Block (propagate verbatim to mode-code)

**TYPE:** PRINCIPLE
**KEYWORDS:** `CONCEPT(Safety): NegativeConstraints; CONCEPT(Governance): Invariants`.

$START_CONTRACT
**PURPOSE:** Hold the exact text that must be copied into the `mode-code` prompt under a section titled "Task-specific constraints (verbatim)".
**DESCRIPTION:** System(Architect) -> PropagateVerbatim -> SubagentReceivesConstraintsWithoutReinterpretation.
**RATIONALE:** Subagents never re-read the task file; any constraint not propagated here is effectively lost.
**ACCEPTANCE_CRITERIA:** Architect's Agent-tool call to `mode-code` contains a section reproducing the bullet list below character-for-character.
$END_CONTRACT

$START_BODY

**Target project root (absolute, read/write):** `/Users/a1111/Dev/CrabLink/flows/brainstorm`. Write ONLY inside this directory.

**Authorised read-only reference paths outside the target:**
- `/Users/a1111/Dev/CrabLink/flows/TASK_INFRA_UPGRADE.md` (task spec)

No other reads outside the target are permitted (AC18).

**Hard invariants (business-logic freeze):**
- Log file name: `decision_maker.log`, at `brainstorm/decision_maker.log`.
- Checkpointer: `AsyncSqliteSaver` in production (replaces v1 sync `SqliteSaver`); `MemorySaver` in tests that don't cover persistence.
- Interrupt location: `interrupt_after=["3_Weight_Questioner"]` — unchanged.
- State schema (`DecisionMakerState`): unchanged — 13 TypedDict fields.
- Prompts (`prompts.py`): unchanged — verbatim constants.
- Graph topology: 7 nodes + same edges + same conditional routing — unchanged.
- Entry point: NONE — Plugin System, no launcher.
- LDD log format `[CLASSIFIER][IMP:N][FN][BLOCK][OP] msg [STATUS]` — unchanged in shape.

**Negative constraints (forbidden actions):**
- DO NOT create a new virtualenv (`python -m venv`) or run `pip install` unless explicitly verifying an import.
- DO NOT alter `prompts.py`, `state.py`, or the set of node IDs registered in the graph.
- DO NOT import anything from sibling CrabLink projects (`crablink-gateway`, `crablink-web`, `flow_service`, `hermes-agent`, `zeroclaw`, etc.).
- DO NOT add a frontend layer (no Gradio, no Streamlit, no FastAPI).
- DO NOT make real network calls in tests — DI fake_llm + fake_search_async are the ONLY permitted I/O.
- DO NOT use `subprocess.run` for business-logic testing.
- DO NOT use `unittest.mock.patch` against internal feature state — use DI fixtures.
- DO NOT write to the framework host `/Users/a1111/Dev/lessons/LESSON_2`.
- DO NOT call `update_test_counter(False)` from inside test bodies (Anti-Loop session-hook rule).
- DO NOT keep any synchronous `_invoke_llm` helper or `SqliteSaver` reference in production code after migration.

$END_BODY

$END_ARTIFACT_ConstraintBlock

$END_SECTION_NEGATIVE_CONSTRAINTS

---

$START_SECTION_DELIVERY_CONTRACT
### 6. Delivery Contract (`mode-code` Return Protocol)

$START_ARTIFACT_ReturnFormat
#### Artifact: Success/Failure Return Format

**TYPE:** DATA_FORMAT
**KEYWORDS:** `PATTERN(Protocol): StructuredReturn; CONCEPT(Reliability): BugReport`.

$START_CONTRACT
**PURPOSE:** Define exactly what the subagent's final message must contain so the Architect can parse it deterministically.
**DESCRIPTION:** System(mode-code) -> EmitFinalMessage -> ArchitectConsumesStructuredReturn.
**RATIONALE:** Free-form returns break automated verification.
**ACCEPTANCE_CRITERIA:** Return matches one of the two formats below verbatim.
$END_CONTRACT

$START_BODY

On SUCCESS:
```
SUCCESS
Artifacts:
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/graph.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/nodes.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tools.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/conftest.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/test_routing.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/test_anti_loop.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/test_graph_compilation.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/test_json_utils.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/test_async_core.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/test_parallel_search.py
- /Users/a1111/Dev/CrabLink/flows/brainstorm/src/features/decision_maker/tests/test_guide.md
- /Users/a1111/Dev/CrabLink/flows/brainstorm/requirements.txt
- /Users/a1111/Dev/CrabLink/flows/brainstorm/.env.example
- /Users/a1111/Dev/CrabLink/flows/brainstorm/decision_maker.log
Summary: <1-3 sentences>
Test run: <pytest summary line, e.g., "18 passed in 1.23s">
```

On BLOCKED / FAILED:
```
BUG_REPORT
User Goal: <what was intended>
Actual Result: <what broke>
Log Analysis: <key [IMP:7-10] lines from decision_maker.log>
Data Analysis: <quantitative discrepancies, e.g., "test_parallel_search wall-clock=0.62s expected<0.45s">
Hypothesis: <root cause>
Recommendation: <specific fix required>
```

$END_BODY

$END_ARTIFACT_ReturnFormat

$END_SECTION_DELIVERY_CONTRACT

---

$START_SECTION_CHANGE_LOG
### 7. Plan Change Log

$START_ARTIFACT_ChangeLog
#### Artifact: Change Log

**TYPE:** DECISION
**KEYWORDS:** `CONCEPT(Governance): Traceability`.

$START_CONTRACT
**PURPOSE:** Track amendments to the plan between Architect/QA/Debug cycles.
**DESCRIPTION:** System(Architect) -> AppendChangeLogEntry -> HistoryPreserved.
**RATIONALE:** Zero-Context Survival — a future agent reading this plan must see how it evolved.
**ACCEPTANCE_CRITERIA:** Any plan update adds a new line below with ISO-8601 date, author (Architect/QA/Debug/Code), version, and a one-line reason.
$END_CONTRACT

$START_BODY
- `2026-04-21 | Architect | v1.0.0 | Initial plan; HITL Gates 1 and 2 collapsed to Hypothesis B + 4 additional decisions.`
- `2026-04-21 | Architect | v2.0.0 | TASK_INFRA_UPGRADE (PATH A). HITL Gate 1 collapsed to {Tavily-Q1=b, HybridCheckpointer-Q2=b, 16+Parallel-Q3=b}; HITL Gate 2 collapsed to Concept A (full async invasion + per-call checkpointer context). Business logic / prompts / state / topology FROZEN; ONLY infrastructure migrated. Added 2 new tests (test_async_core, test_parallel_search). Added 3 libs (aiosqlite, tavily-python, pytest-asyncio). Added TAVILY_API_KEY to .env.example. Updated OPENROUTER_MODEL default to x-ai/grok-4-fast.`
$END_BODY

$END_ARTIFACT_ChangeLog

$END_SECTION_CHANGE_LOG

$END_DOC_NAME
