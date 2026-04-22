$START_DEV_PLAN_UI

**PURPOSE:** Design the Gradio-based UI layer (`src/ui/`) that realises the Agentic UX contract for the Decision Maker feature — step-by-step node streaming, live State X-Ray, CoVe critique highlighting, and Human-in-the-Loop gate — while leaving the feature core (prompts, graph topology) untouched and keeping the UI layer fully headless-testable via pytest.

**SCOPE:** `src/ui/` package (new), additive extensions to `src/features/decision_maker/graph.py` and `__init__.py` (two new `async def` parallel-API functions only — no modification of existing `start_session`/`resume_session`/`build_graph`), new `tests/ui/` suite, new `scripts/run_brainstorm_ui.py` launcher, and a single new dependency `gradio==5.9.1`.

**KEYWORDS:** [DOMAIN(10): AgenticUX; DOMAIN(9): VerticalFeatureSlicing; TECH(10): Gradio5; TECH(10): LangGraphAstream; CONCEPT(10): StreamModeUpdates; CONCEPT(9): HumanInTheLoop; CONCEPT(9): ChainOfVerification; PATTERN(9): ControllerPresenter; PATTERN(8): ParallelPublicAPI; PATTERN(8): HeadlessUITesting]

---

$START_DOCUMENT_PLAN
### Document Plan

**SECTION_GOALS:**
- GOAL [Stream multi-agent LangGraph execution into a two-column Gradio UI in real time] => G1_AGENTIC_UX_STREAMING
- GOAL [Preserve decision_maker core untouched — only additive parallel API in graph.py] => G2_CORE_IMMUTABILITY
- GOAL [All UI logic verified by pytest without launching any web server / browser] => G3_HEADLESS_TESTABILITY
- GOAL [Surface CoVe rewrite decisions visibly — user MUST see critique_feedback] => G4_COVE_TRANSPARENCY
- GOAL [Human-in-the-Loop gate after Node 3 with correct thread_id continuity] => G5_HITL_CONTINUITY
- GOAL [Conformance to the semantic exoskeleton + LDD 2.0 logging protocol in every new module] => G6_PROMPT_PROTOCOL_COMPLIANCE

**SECTION_USE_CASES:**
- USE_CASE [User submits a decision dilemma → observes node-by-node progress → sees State X-Ray update → gets calibration question from Node 3] => UC1_START_AND_AWAIT
- USE_CASE [User provides weight answer → graph resumes through Draft/CoVe/Final → final Markdown answer rendered in Chatbot] => UC2_RESUME_TO_FINAL
- USE_CASE [During resume, CoVe critique returns decision="rewrite" → chat shows highlighted critique_feedback → rewrite_count increments → eventually finalize] => UC3_COVE_REWRITE_VISIBLE
- USE_CASE [Developer runs pytest → UI handlers verified with mocked stream_session yielding N events → Gradio never launched] => UC4_HEADLESS_VERIFICATION
- USE_CASE [Operator runs scripts/run_brainstorm_ui.py → local Gradio server on 127.0.0.1 opens in browser] => UC5_LAUNCH_UI

$END_DOCUMENT_PLAN

---

### 1. Draft Code Graph

XML conforms to the graph-protocol standard: hierarchical node IDs (`File_Entity_TYPE`), `annotation` tags, and explicit `CrossLinks` for inter-module calls. Existing modules are referenced by short IDs only; only **new** or **modified** nodes are expanded.

```xml
<DraftCodeGraph>

  <!-- =========================================================
       EXISTING CORE (referenced for CrossLinks only — NOT MODIFIED)
       ========================================================= -->
  <decision_maker_graph_py FILE="src/features/decision_maker/graph.py" TYPE="EXISTING_PLUS_ADDITIVE">
    <annotation>Existing LangGraph assembly. Only additive: two new async-generator functions appended. No change to build_graph, start_session, resume_session, imports topology, or interrupt_after list.</annotation>

    <decision_maker_graph_build_graph_FUNC NAME="build_graph" TYPE="EXISTING_UNCHANGED">
      <annotation>Existing sync factory. Accepts checkpointer via DI. REUSED AS-IS by new stream_* functions.</annotation>
    </decision_maker_graph_build_graph_FUNC>

    <decision_maker_graph_start_session_FUNC NAME="start_session" TYPE="EXISTING_UNCHANGED">
      <annotation>Existing async non-streaming API. Kept untouched for backwards compatibility with smoke_run.py and existing tests.</annotation>
    </decision_maker_graph_start_session_FUNC>

    <decision_maker_graph_resume_session_FUNC NAME="resume_session" TYPE="EXISTING_UNCHANGED">
      <annotation>Existing async non-streaming API. Kept untouched.</annotation>
    </decision_maker_graph_resume_session_FUNC>

    <decision_maker_graph_stream_session_FUNC NAME="stream_session" TYPE="IS_ASYNC_GEN_OF_MODULE">
      <annotation>NEW. Async generator. Opens AsyncSqliteSaver per-call, builds graph via DI, seeds initial state, iterates graph.astream(initial_state, config, stream_mode="updates"), yields a dict per node completion plus a post-stream "awaiting_user" terminator (question + thread_id). NEVER raises on interrupt — interrupt is the expected terminal condition of leg 1.</annotation>
      <CrossLinks>
        <Link TARGET="decision_maker_graph_build_graph_FUNC" TYPE="CALLS_FUNCTION" />
        <Link TARGET="ui_controllers_orchestrate_start_FUNC" TYPE="CALLED_BY" />
      </CrossLinks>
    </decision_maker_graph_stream_session_FUNC>

    <decision_maker_graph_stream_resume_session_FUNC NAME="stream_resume_session" TYPE="IS_ASYNC_GEN_OF_MODULE">
      <annotation>NEW. Async generator. Opens AsyncSqliteSaver per-call, rebuilds graph, injects user_answer via aupdate_state, iterates graph.astream(None, config, stream_mode="updates"), yields a dict per node completion plus a post-stream "done" terminator (final_answer + thread_id).</annotation>
      <CrossLinks>
        <Link TARGET="decision_maker_graph_build_graph_FUNC" TYPE="CALLS_FUNCTION" />
        <Link TARGET="ui_controllers_orchestrate_resume_FUNC" TYPE="CALLED_BY" />
      </CrossLinks>
    </decision_maker_graph_stream_resume_session_FUNC>
  </decision_maker_graph_py>

  <decision_maker_init_py FILE="src/features/decision_maker/__init__.py" TYPE="EXISTING_MODIFIED">
    <annotation>__all__ extended to export stream_session and stream_resume_session alongside existing API.</annotation>
  </decision_maker_init_py>

  <!-- =========================================================
       NEW UI PACKAGE — src/ui/
       ========================================================= -->

  <ui_init_py FILE="src/ui/__init__.py" TYPE="PACKAGE_MARKER">
    <annotation>Package initialiser. Re-exports build_ui() factory from app.py for scripts/run_brainstorm_ui.py. Nothing else is exported (internal controllers and presenter are implementation detail).</annotation>
  </ui_init_py>

  <ui_controllers_py FILE="src/ui/controllers.py" TYPE="PURE_ASYNC_DOMAIN_LAYER">
    <annotation>FRAMEWORK-AGNOSTIC. Does NOT import gradio. Provides two async-generator orchestration functions that consume the decision_maker stream API and emit a normalised stream of UIEvent dicts (domain events). This is the headless-testable seam.</annotation>

    <ui_controllers_UIEvent_TYPEDDICT NAME="UIEvent" TYPE="IS_TYPEDDICT_OF_MODULE">
      <annotation>TypedDict (total=False). Fields: kind (Literal of 9 event kinds), node, state_delta, state_snapshot, question, critique_feedback, final_answer, error_message, thread_id.</annotation>
    </ui_controllers_UIEvent_TYPEDDICT>

    <ui_controllers_orchestrate_start_FUNC NAME="orchestrate_start" TYPE="IS_ASYNC_GEN_OF_MODULE">
      <annotation>async def orchestrate_start(user_input: str, thread_id: str, checkpoint_path: Optional[str] = None) -> AsyncIterator[UIEvent]. Yields session_started (once), then for each chunk from stream_session: node_completed + state_snapshot; on cove_critique with rewrite decision emits cove_rewrite between them. Final event: awaiting_user. On exception: error.</annotation>
      <CrossLinks>
        <Link TARGET="decision_maker_graph_stream_session_FUNC" TYPE="CALLS_FUNCTION" />
      </CrossLinks>
    </ui_controllers_orchestrate_start_FUNC>

    <ui_controllers_orchestrate_resume_FUNC NAME="orchestrate_resume" TYPE="IS_ASYNC_GEN_OF_MODULE">
      <annotation>async def orchestrate_resume(user_answer: str, thread_id: str, checkpoint_path: Optional[str] = None) -> AsyncIterator[UIEvent]. Yields resume_started, then iterates stream_resume_session; terminates with final_answer. CoVe rewrites produce cove_rewrite events between node_completed events.</annotation>
      <CrossLinks>
        <Link TARGET="decision_maker_graph_stream_resume_session_FUNC" TYPE="CALLS_FUNCTION" />
      </CrossLinks>
    </ui_controllers_orchestrate_resume_FUNC>

    <ui_controllers_extract_cove_rewrite_FUNC NAME="_extract_cove_rewrite_event" TYPE="IS_PRIVATE_HELPER_OF_MODULE">
      <annotation>Pure function. Input: state_delta from 5_CoVe_Critique node. If delta["decision"] == "rewrite" AND delta.get("critique_feedback"), returns UIEvent of kind="cove_rewrite"; else None.</annotation>
    </ui_controllers_extract_cove_rewrite_FUNC>

  </ui_controllers_py>

  <ui_presenter_py FILE="src/ui/presenter.py" TYPE="PURE_RENDERER_LAYER">
    <annotation>Converts UIEvent into a tuple of Gradio-renderable deltas: (new_chat_history, new_state_snapshot_dict, status_text). Pure function — easily unit-tested without async context.</annotation>

    <ui_presenter_render_FUNC NAME="render" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>def render(event: UIEvent, chat_history: list[dict], state_snapshot: dict) -> tuple[list[dict], dict, str]. Dispatches on event["kind"]; returns updated tuple. Chat history uses Gradio 5 "messages" format: list of {"role": "user"|"assistant", "content": str}. Status text uses emoji prefixes per task spec ("⏳ Узел 1: Анализ контекста...", "🌐 Выполнение N параллельных запросов...", "🚨 Критик нашел ошибку...").</annotation>
    </ui_presenter_render_FUNC>

    <ui_presenter_filter_state_FUNC NAME="_filter_state_for_display" TYPE="IS_PRIVATE_HELPER_OF_MODULE">
      <annotation>Drops internal/verbose fields (user_input, last_question verbatim) and keeps the human-readable subset mentioned in the task: dilemma, search_queries, tool_facts, weights, rewrite_count, critique_feedback, decision, final_answer. Truncates long string values to 500 chars for readability.</annotation>
    </ui_presenter_filter_state_FUNC>

    <ui_presenter_format_status_FUNC NAME="_format_status" TYPE="IS_PRIVATE_HELPER_OF_MODULE">
      <annotation>Maps (event_kind, node_name) → emoji-prefixed Russian status string per task spec §2.1. Node IDs → human names: "1_Context_Analyzer" → "Анализ контекста" etc.</annotation>
    </ui_presenter_format_status_FUNC>
  </ui_presenter_py>

  <ui_app_py FILE="src/ui/app.py" TYPE="GRADIO_COMPOSITION_ROOT">
    <annotation>Only layer that imports gradio. Exposes build_ui() -> gr.Blocks. Wires Blocks two-column layout, defines two async-generator handlers (on_submit, on_resume), and connects them to buttons. Also generates a fresh thread_id on session start via uuid4 stored in gr.State.</annotation>

    <ui_app_build_ui_FUNC NAME="build_ui" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>def build_ui() -> gr.Blocks. Creates the two-column Blocks. Left: gr.Chatbot(type="messages"), gr.Textbox(placeholder="Опиши дилемму..."), gr.Button("Отправить"), gr.Markdown for status line. Right: gr.JSON(label="State X-Ray", value={}). gr.State components for thread_id, mode ("awaiting_submit"|"awaiting_user_answer"), chat_history, state_snapshot.</annotation>
    </ui_app_build_ui_FUNC>

    <ui_app_on_submit_FUNC NAME="on_submit" TYPE="IS_ASYNC_GEN_HANDLER_OF_MODULE">
      <annotation>async def on_submit(user_input, thread_id, mode, chat_history, state_snapshot) -> AsyncIterator[tuple]. Dispatches to orchestrate_start when mode == "awaiting_submit", else to orchestrate_resume when mode == "awaiting_user_answer". For each event: runs render(), yields Gradio update tuple (chat, state_json, status_md, new_mode, new_thread_id, textbox_clear).</annotation>
      <CrossLinks>
        <Link TARGET="ui_controllers_orchestrate_start_FUNC" TYPE="CALLS_FUNCTION" />
        <Link TARGET="ui_controllers_orchestrate_resume_FUNC" TYPE="CALLS_FUNCTION" />
        <Link TARGET="ui_presenter_render_FUNC" TYPE="CALLS_FUNCTION" />
      </CrossLinks>
    </ui_app_on_submit_FUNC>

    <ui_app_on_new_session_FUNC NAME="on_new_session" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>def on_new_session() -> tuple. Resets: generates new uuid4 thread_id, clears chat_history, resets state_snapshot to {}, status to "Готов принять дилемму.", mode="awaiting_submit". Bound to a "Новая сессия" button.</annotation>
    </ui_app_on_new_session_FUNC>

  </ui_app_py>

  <!-- =========================================================
       LAUNCHER
       ========================================================= -->

  <scripts_run_brainstorm_ui_py FILE="scripts/run_brainstorm_ui.py" TYPE="CLI_LAUNCHER">
    <annotation>Operator entry-point. Mirrors smoke_run.py topology: prepends project root to sys.path, loads .env, lazy-imports gradio & build_ui, launches ui on 127.0.0.1 inbrowser=True, wraps in try/except KeyboardInterrupt.</annotation>
    <scripts_run_brainstorm_ui_main_FUNC NAME="main" TYPE="IS_FUNCTION_OF_MODULE">
      <annotation>def main() -> None. Reads required env vars (OPENROUTER_API_KEY, OPENROUTER_MODEL, TAVILY_API_KEY) same way as smoke_run.py. Lazy imports ui.app.build_ui. Calls demo.launch(inbrowser=True, server_name="127.0.0.1").</annotation>
    </scripts_run_brainstorm_ui_main_FUNC>
  </scripts_run_brainstorm_ui_py>

  <!-- =========================================================
       TESTS — tests/ui/
       ========================================================= -->

  <tests_init_py FILE="tests/__init__.py" TYPE="PACKAGE_MARKER">
    <annotation>Root-level test package marker (new — did not exist before).</annotation>
  </tests_init_py>

  <tests_ui_init_py FILE="tests/ui/__init__.py" TYPE="PACKAGE_MARKER">
    <annotation>tests/ui package marker.</annotation>
  </tests_ui_init_py>

  <tests_ui_conftest_py FILE="tests/ui/conftest.py" TYPE="PYTEST_CONFTEST">
    <annotation>Anti-Loop Protocol conftest per mode-code spec: session-scoped pytest_sessionstart/pytest_sessionfinish managing tests/ui/.test_counter.json. Checklist & reflection output driven by attempt counter. pytest_asyncio mode="auto".</annotation>
  </tests_ui_conftest_py>

  <tests_ui_test_ui_handlers_py FILE="tests/ui/test_ui_handlers.py" TYPE="PYTEST_MODULE">
    <annotation>UI Headless Test per task spec §3.1. Verifies on_submit handler behaviour WITHOUT launching Gradio. Uses monkeypatch to replace orchestrate_start with a fake async generator yielding a scripted sequence of UIEvents. Asserts: (a) handler is an async generator; (b) yields exactly one tuple per event; (c) each tuple has 6 slots matching on_submit output contract; (d) final yield contains status "⏳ Жду ответа..." or similar HITL marker; (e) captured logs at [IMP:7-10] are non-empty.</annotation>
  </tests_ui_test_ui_handlers_py>

  <tests_ui_test_state_streaming_py FILE="tests/ui/test_state_streaming.py" TYPE="PYTEST_MODULE">
    <annotation>State Streaming Test per task spec §3.2. Constructs a mocked graph stream yielding 3 state updates. Verifies orchestrate_start + render pipeline produces 3 sequential Chatbot+JSON updates. Includes a CoVe-rewrite scenario variant that injects a critique_feedback update and asserts the cove_rewrite UIEvent is emitted with the correct feedback text.</annotation>
  </tests_ui_test_state_streaming_py>

  <tests_ui_test_presenter_py FILE="tests/ui/test_presenter.py" TYPE="PYTEST_MODULE">
    <annotation>Table-driven pure-function tests for presenter.render. Covers all 9 UIEvent kinds. Pure input→output assertions; no async, no gradio import needed for these tests other than what presenter.py itself brings in transitively (no gr.components instantiated).</annotation>
  </tests_ui_test_presenter_py>

  <!-- =========================================================
       DEPENDENCY MANIFEST
       ========================================================= -->

  <requirements_txt FILE="requirements.txt" TYPE="EXISTING_MODIFIED">
    <annotation>Append v3.0.0 section with exactly one new line: gradio==5.9.1 with WHY-comment.</annotation>
  </requirements_txt>

  <!-- =========================================================
       FINALISATION (root Architect only)
       ========================================================= -->

  <AppGraph_xml FILE="AppGraph.xml" TYPE="EXISTING_MODIFIED_BY_ROOT_ARCHITECT">
    <annotation>Local AppGraph.xml updated by root Architect (NOT by mode-code) after QA SUCCESS, adding the 7 new UI nodes above. mode-code does NOT modify this file.</annotation>
  </AppGraph_xml>

</DraftCodeGraph>
```

---

### 2. Step-by-step Data Flow

Three orthogonal flows. Performed mental simulation to validate logical consistency; annotations after each step cite the node/function they bind to.

#### Flow A — `UC1_START_AND_AWAIT` (user submits dilemma)

1. **User action.** Types dilemma into `Textbox`, clicks `Отправить`. Gradio invokes `on_submit(user_input, thread_id, mode="awaiting_submit", chat_history=[], state_snapshot={})`.
2. **Append user turn.** Handler appends `{"role": "user", "content": user_input}` to `chat_history` and yields first UI update (status="⏳ Узел 1: Анализ контекста...").
3. **Delegate to controller.** Handler `async for event in orchestrate_start(user_input, thread_id)`.
4. **`orchestrate_start` — session_started.** Emits `UIEvent{kind:"session_started", thread_id}` **before** the first chunk (so presenter can reset state_snapshot to `{}`).
5. **`orchestrate_start` — stream loop.**
   5.1. `async with AsyncSqliteSaver.from_conn_string(path)` → `build_graph(cp)` → `config={"configurable":{"thread_id": thread_id}}`.
   5.2. `initial_state = {"user_input": user_input, "tool_facts": [], "rewrite_count": 0}`.
   5.3. `async for chunk in graph.astream(initial_state, config, stream_mode="updates"):`
        – Each `chunk` is `{node_id: state_delta}` (single-key dict per LangGraph contract). Let `(node_id, state_delta) = next(iter(chunk.items()))`.
        – Emit `UIEvent{kind:"node_completed", node:node_id, state_delta, thread_id}`.
        – `snapshot = await graph.aget_state(config)`; emit `UIEvent{kind:"state_snapshot", node:node_id, state_snapshot:snapshot.values, thread_id}`.
   5.4. **After loop** (interrupt reached after `3_Weight_Questioner`): `snapshot = await graph.aget_state(config)`, extract `last_question = snapshot.values.get("last_question","")`. Emit `UIEvent{kind:"awaiting_user", question:last_question, thread_id}`.
6. **`orchestrate_start` exits generator.** `async with` closes `AsyncSqliteSaver`.
7. **Handler per-event render.** For every event: `chat_history, state_snapshot, status_md = render(event, chat_history, state_snapshot)`; yield `(chat_history, state_snapshot, status_md, new_mode, thread_id, "")`. At `awaiting_user` event, `new_mode="awaiting_user_answer"`, status="🤔 Нужен ваш ответ:" and chat appends `{"role":"assistant","content":question}`.
8. **User sees:** left column fills progressively (user turn → status line → assistant turn with question), right column JSON updates after every node. Textbox unlocks (Gradio generator exits).

#### Flow B — `UC2_RESUME_TO_FINAL` + `UC3_COVE_REWRITE_VISIBLE` (user answers)

1. **User action.** Types answer, clicks `Отправить` again. Gradio invokes `on_submit(user_answer, thread_id, mode="awaiting_user_answer", ...)`.
2. **Handler dispatches.** Since `mode=="awaiting_user_answer"` → `async for event in orchestrate_resume(user_answer, thread_id)`.
3. **`orchestrate_resume` stream loop.**
   3.1. `async with AsyncSqliteSaver.from_conn_string(path)` → `graph=build_graph(cp)` → `config={thread_id}`.
   3.2. `await graph.aupdate_state(config, {"user_answer": user_answer})`.
   3.3. Emit `UIEvent{kind:"resume_started", thread_id}`.
   3.4. `async for chunk in graph.astream(None, config, stream_mode="updates"):`
        – Extract `(node_id, state_delta)`.
        – **Before** emitting `node_completed`, if `node_id=="5_CoVe_Critique"` and `_extract_cove_rewrite_event(state_delta)` returns non-None: emit `UIEvent{kind:"cove_rewrite", critique_feedback:state_delta["critique_feedback"], thread_id}`. *(This yields BEFORE node_completed so presenter paints the red critique line before the JSON updates.)*
        – Emit `node_completed` and `state_snapshot` as in Flow A.
   3.5. **After loop** (graph reached END): `snapshot = aget_state`; extract `final_answer`. Emit `UIEvent{kind:"final_answer", final_answer:final_answer, thread_id}`.
4. **Handler render.** Each event produces Gradio tuple yield. `cove_rewrite` event triggers: status="🚨 Критик нашел ошибку, переписываю (попытка {rewrite_count}/2)" + chat appends assistant msg with clear prefix `⚠️ CoVe-критика:\n\n{critique_feedback}`. `final_answer` event appends `{"role":"assistant","content":final_answer}` and sets status="✅ Готово".
5. **Mode transition.** At end of resume generator, presenter/handler sets `new_mode="awaiting_submit"` so next click treats input as a new dilemma for the **same** thread_id (user can continue dialogue iff task allows — here it terminates the session).

#### Flow C — `UC4_HEADLESS_VERIFICATION` (pytest)

1. **Fixture setup.** `conftest.py` initialises `.test_counter.json`; `pytest_asyncio` enabled.
2. **State Streaming Test.**
   2.1. Build `fake_stream = [chunk1, chunk2, chunk3]` where `chunkN = {"1_Context_Analyzer": {...}}` / `{"2_Tool_Node": {...}}` / `{"3_Weight_Questioner": {"last_question": "..."}}`.
   2.2. Use `monkeypatch.setattr("src.ui.controllers.stream_session", <fake_async_gen>)` — `fake_async_gen` yields 3 chunks then emits an `awaiting_user` terminator dict directly.
   2.3. Collect all events: `events = [ev async for ev in orchestrate_start("x", "tid")]`.
   2.4. Assert:
        – `len([e for e in events if e["kind"]=="node_completed"]) == 3`.
        – `events[-1]["kind"] == "awaiting_user"` with expected question.
        – After applying `render()` sequentially: final `chat_history` has at least 1 assistant turn with the question text; final `state_snapshot` contains `dilemma` key from chunk 1 delta.
3. **UI Handlers Test.**
   3.1. Monkeypatch `src.ui.app.orchestrate_start` with a fake that yields 2 UIEvents + terminator.
   3.2. Collect `tuples = [t async for t in on_submit("dilemma", "tid", "awaiting_submit", [], {})]`.
   3.3. Assert each tuple has length 6; last tuple's `new_mode == "awaiting_user_answer"`; no gradio server started (implicit — we never called `build_ui()` or `.launch()`).
4. **LDD Telemetry.** `caplog.set_level("INFO")`. Print all `[IMP:7-10]` lines via regex before asserts (Anti-Illusion: ensures agent sees algorithm trajectory on failure).

---

### 3. Acceptance Criteria

Strict, measurable. Verified by pytest + final audit. Numbering `AC_UI_*` to disambiguate from backend `AC*`.

- [ ] **AC_UI_01 — Directory layout.** Files exist at: `src/ui/__init__.py`, `src/ui/controllers.py`, `src/ui/presenter.py`, `src/ui/app.py`, `tests/__init__.py`, `tests/ui/__init__.py`, `tests/ui/conftest.py`, `tests/ui/test_ui_handlers.py`, `tests/ui/test_state_streaming.py`, `tests/ui/test_presenter.py`, `scripts/run_brainstorm_ui.py`.
- [ ] **AC_UI_02 — Core immutability.** `git diff src/features/decision_maker/prompts.py src/features/decision_maker/state.py src/features/decision_maker/nodes.py src/features/decision_maker/tools.py` produces ZERO output. `src/features/decision_maker/graph.py` diff is ADDITIVE ONLY (no removed lines except optional trailing whitespace). `src/features/decision_maker/__init__.py` diff is additive-only in `__all__`.
- [ ] **AC_UI_03 — Parallel API.** `from src.features.decision_maker import stream_session, stream_resume_session` succeeds. Both are `async def` functions returning async iterators (verified via `inspect.isasyncgenfunction`).
- [ ] **AC_UI_04 — No gradio leak.** `grep -r "import gradio\|from gradio" src/features/` returns nothing. `grep -r "import gradio\|from gradio" src/ui/controllers.py src/ui/presenter.py` returns nothing — gradio only in `src/ui/app.py` and `scripts/run_brainstorm_ui.py`.
- [ ] **AC_UI_05 — Semantic exoskeleton.** Every new `.py` file under `src/ui/` and `tests/ui/` contains: `# START_MODULE_CONTRACT`, `# END_MODULE_CONTRACT`, at least one `# START_MODULE_MAP`, `# START_CHANGE_SUMMARY`. Every non-trivial public function has `# START_CONTRACT`/`# END_CONTRACT` and a ≥1-paragraph docstring.
- [ ] **AC_UI_06 — LDD logging.** `src/ui/controllers.py` and `src/ui/app.py` emit at least one `[IMP:9]` Belief-State log per public function execution path (`session_started`, `awaiting_user`, `final_answer`). Logs include the classifier tag `[UIEvent]` or `[BeliefState]`.
- [ ] **AC_UI_07 — Stream fidelity (State Streaming Test).** `tests/ui/test_state_streaming.py::test_three_updates_produce_three_uievents` — mocked graph emitting 3 state updates produces exactly 3 `node_completed` events + 3 `state_snapshot` events + 1 terminator event. PASS.
- [ ] **AC_UI_08 — CoVe visibility.** `tests/ui/test_state_streaming.py::test_cove_rewrite_emits_dedicated_event` — when mocked `5_CoVe_Critique` returns `{"decision":"rewrite","critique_feedback":"..."}`, a `cove_rewrite` UIEvent is emitted with exact `critique_feedback` text, **before** the corresponding `node_completed`. PASS.
- [ ] **AC_UI_09 — HITL gate.** `tests/ui/test_ui_handlers.py::test_on_submit_transitions_to_awaiting_user_answer` — after `awaiting_user` event, `on_submit` yields final tuple with `new_mode=="awaiting_user_answer"` and chat history last message `{"role":"assistant"}` containing the mocked question string. PASS.
- [ ] **AC_UI_10 — Presenter purity.** `tests/ui/test_presenter.py` — `render` is a pure function (no I/O, no globals mutated); table-driven tests cover all 9 UIEvent kinds; each row asserts returned tuple shape `(list, dict, str)`. PASS.
- [ ] **AC_UI_11 — Headless constraint.** Full test suite runs with `gradio` installed but NEVER calls `.launch()`. Add a grep-based assertion in conftest or a dedicated test: `assert "launch(" not in any test source`. Alternative: rely on test timings (any launched server would hang the suite).
- [ ] **AC_UI_12 — Dependency hygiene.** `requirements.txt` contains a single new line `gradio==5.9.1` with a WHY-comment. No other additions.
- [ ] **AC_UI_13 — Anti-Loop Protocol.** `tests/ui/.test_counter.json` created and managed by `tests/ui/conftest.py` session hooks. Counter resets to 0 only at 100% PASS. Checklist printed to console on attempt >0.
- [ ] **AC_UI_14 — Existing suite unaffected.** `python -m pytest src/features/decision_maker/tests/ -s -v` continues to pass with the same count as before the UI task (no regressions from additive graph.py changes).
- [ ] **AC_UI_15 — Launcher smoke.** `python scripts/run_brainstorm_ui.py --help` (or dry-run flag) or at minimum import-time succeeds without error on a machine with env vars set. The launcher is NOT auto-tested with real Gradio launch (operator-only).
- [ ] **AC_UI_16 — Centralised log.** UI-level logs land in the same `decision_maker.log` file as core logs (shared logger via `src/core/logger.py::setup_ldd_logger`). No new `.log` files are created.
- [ ] **AC_UI_17 — thread_id continuity.** `on_new_session` button generates a fresh `uuid4`, but absent this click, the `thread_id` stored in `gr.State` persists across submit → resume — verified in `test_ui_handlers.py` via assertion that the `thread_id` in the post-submit yield tuple equals the one passed in.

---

### 4. Task-specific Constraints (verbatim for subagent prompt)

These MUST be propagated to `mode-code` verbatim in the delegation prompt:

**NEGATIVE:**
- N1: Do NOT modify `src/features/decision_maker/prompts.py`, `state.py`, `nodes.py`, `tools.py` under any circumstance.
- N2: Do NOT modify the body of existing functions `build_graph`, `start_session`, `resume_session` in `graph.py`. You may ONLY *append* the two new async-generator functions and optionally extend the MODULE_MAP / CHANGE_SUMMARY at the top of the file.
- N3: Do NOT import gradio in `src/ui/controllers.py`, `src/ui/presenter.py`, `src/features/**/*`, or any `tests/ui/*.py` test module. Gradio is permitted only in `src/ui/app.py`, `src/ui/__init__.py` (if re-exporting a `gr`-typed factory), and `scripts/run_brainstorm_ui.py`.
- N4: Do NOT launch Gradio in tests (`demo.launch()`, `ui.launch()` forbidden inside `tests/**`).
- N5: Do NOT create a new venv. Install `gradio==5.9.1` via the existing environment if needed for test import resolution (but tests should not require a running UI).
- N6: Do NOT update `AppGraph.xml`. That step is handled by the root Architect after QA SUCCESS.
- N7: Do NOT create a new `.log` file. Reuse `src/core/logger.py::setup_ldd_logger` so all logs write to `decision_maker.log`.

**POSITIVE:**
- P1: Gradio version pinned to `gradio==5.9.1`. Use `gr.Chatbot(type="messages")` and the ChatMessage-style list-of-dicts history format throughout.
- P2: Entry-point location: `scripts/run_brainstorm_ui.py` (mirrors `scripts/smoke_run.py` structure exactly — project-root `sys.path` injection, `load_dotenv`, env-var sanity check, lazy import of `gradio` and `build_ui`, `try/except KeyboardInterrupt`).
- P3: UIEvent schema: exactly 9 kinds — `session_started`, `node_completed`, `state_snapshot`, `cove_rewrite`, `awaiting_user`, `resume_started`, `final_answer`, `error`, plus one reserved `node_started` (emit optionally at beginning of controller for initial status). Do NOT introduce additional kinds without updating the plan.
- P4: `stream_mode="updates"` (NOT "values", NOT "messages").
- P5: Async iteration with per-call `AsyncSqliteSaver.from_conn_string(path)` context (mirrors existing `start_session`/`resume_session` pattern — do not deviate).
- P6: LDD log format identical to existing core: `f"[{CLASSIFIER}][IMP:{N}][{FUNCTION_NAME}][{BLOCK_NAME}][{OPERATION_TYPE}] Description [{STATUS}]"`. Use `[UIEvent]` classifier for controller/presenter/app logs.
- P7: pytest runner: `python -m pytest tests/ui/ -s -v` is the canonical invocation for mode-code's verification loop.
- P8: All new UI files carry `VERSION: 1.0.0`; `decision_maker/graph.py` bumps to `VERSION: 3.0.0` (was 2.0.0) with `PREV_CHANGE_SUMMARY` chain intact; `decision_maker/__init__.py` bumps to `1.1.0`.

---

### 5. Classifier Hints for mode-code

- `PROJECT_TYPE_DEFINED: Plugin System` (VFS architecture; new feature slice inside existing plugin system — NOT a tutorial lesson).
- `TASK_TYPE_DEFINED: Code and Tests`.

---

### 6. Test Guide (brief — expanded by mode-code as `tests/ui/test_guide.md`)

For QA subagent:
- Run: `python -m pytest tests/ui/ -s -v`.
- Also re-run existing suite to verify AC_UI_14: `python -m pytest src/features/decision_maker/tests/ -s -v`.
- Expected outcome: both runs 100% PASS; `.test_counter.json` for `tests/ui/` reads `0` after success.
- Critical log markers to grep in captured output:
  - `[BeliefState][IMP:9][orchestrate_start]` — session_started Belief log.
  - `[BeliefState][IMP:9][orchestrate_start][BLOCK_AWAITING_USER]` — HITL emission.
  - `[BeliefState][IMP:9][orchestrate_resume][BLOCK_FINAL_ANSWER]` — final answer emission.
  - `[UIEvent][IMP:7][render][BLOCK_COVE_REWRITE]` — CoVe rewrite rendered.

$END_DEV_PLAN_UI
