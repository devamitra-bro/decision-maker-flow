# FILE: src/features/decision_maker/tests/conftest.py
# VERSION: 2.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Shared pytest fixtures and session hooks for the Decision Maker test suite (v2.0.0).
# SCOPE: Anti-Loop counter (.test_counter.json), async fake_llm fixture, fake_search_async
#        fixture, event_loop session fixture, memory_checkpointer fixture, ldd_capture helper.
# INPUT: pytest session lifecycle events.
# OUTPUT: .test_counter.json maintained per session; async-aware fake_llm fixture;
#         fake_search_async with configurable delay; MemorySaver checkpointer; ldd_capture helper.
# KEYWORDS: [DOMAIN(8): TestInfra; CONCEPT(9): AntiLoop; PATTERN(8): DependencyInjection;
#            CONCEPT(7): LDDTelemetry; PATTERN(6): SessionHook; CONCEPT(10): AsyncIO;
#            PATTERN(9): AsyncFixture]
# LINKS: [USES_API(7): pytest; USES_API(8): pytest_asyncio; READS_DATA_FROM(6): .test_counter.json;
#         USES_API(7): langgraph.checkpoint.memory.MemorySaver]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §4 (conftest responsibilities v2.0.0); AC14, AC15
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Counter increment (False call) ONLY happens in pytest_sessionfinish hook, NEVER in test bodies.
# - Counter resets to 0 ONLY when all tests pass (100% PASS).
# - fake_llm returns a Callable that returns an object with async .ainvoke() method returning AIMessage.
# - fake_search_async is an async callable accepting (query: str) returning List[Dict].
# - ldd_capture prints and returns all log records with IMP:7-10.
# - event_loop is session-scoped to support AsyncSqliteSaver tests (AC10).
# - memory_checkpointer yields a fresh MemorySaver() per test function.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why maintain .test_counter.json in the tests directory rather than a temp location?
# A: The counter must persist across pytest invocations to track cumulative failures.
#    Temp locations are wiped between runs. The tests directory is inside the target project
#    and is version-control visible, making the loop state auditable.
# Q: Why use pytest_sessionfinish for counter management and not pytest_runtest_logreport?
# A: Session-level hooks give a single decision point per run. Per-test hooks would require
#    complex state tracking across multiple tests. The constraint says NEVER increment from
#    inside test bodies — session hooks are the only compliant approach.
# Q: Why is fake_llm now async-aware (has .ainvoke() instead of .invoke())?
# A: All node functions now call await llm.ainvoke(messages) via _invoke_llm_async. The
#    fake_llm must provide an async-capable interface to avoid coroutine errors in tests.
#    We implement .ainvoke() as an async method returning AIMessage directly (no MagicMock
#    autospec needed — the interface is minimal).
# Q: Why does fake_llm implement BOTH .ainvoke() and .invoke()?
# A: Forward compatibility: if any test path accidentally calls .invoke() on the mock,
#    it gets a proper response rather than an AttributeError. Defense in depth.
# Q: Why session-scoped event_loop?
# A: test_async_core.py tests open AsyncSqliteSaver and chain start+resume in a SINGLE
#    test. A session-scoped loop prevents new-loop-created-per-test errors with aiosqlite.
# Q: Why does fake_search_async have a configurable sleep delay?
# A: test_parallel_search.py needs to prove concurrency by timing wall-clock < delay * N * 0.75.
#    The delay must be non-zero (otherwise the timing assertion is meaningless) but
#    configurable so tests can use whatever value they choose.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v2.0.0 — async migration; fake_llm upgraded to async-aware (ainvoke);
#              added fake_search_async fixture; added event_loop session fixture;
#              added memory_checkpointer fixture; Anti-Loop hooks preserved verbatim.
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial implementation; Anti-Loop hooks + fake_llm + ldd_capture.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 8 [pytest session start hook — loads counter and prints checklist if failures] => pytest_sessionstart
# FUNC 8 [pytest session finish hook — updates counter based on pass/fail result] => pytest_sessionfinish
# FUNC 7 [Session-scoped event loop fixture for async tests (pytest-asyncio)] => event_loop
# FUNC 7 [Fixture: MemorySaver per test for graph compilation tests] => memory_checkpointer
# FUNC 7 [Fixture: injectable async fake LLM returning scripted AIMessage content] => fake_llm
# FUNC 7 [Fixture: injectable async search callable with configurable sleep delay] => fake_search_async
# FUNC 7 [Helper fixture: filters and prints IMP:7-10 log records from caplog] => ldd_capture
# END_MODULE_MAP

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Callable, List

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

# Counter file lives in the same directory as this conftest
_COUNTER_FILE = Path(__file__).parent / ".test_counter.json"

# Add brainstorm root to sys.path for clean imports without conftest path hacks
_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))


# START_FUNCTION_pytest_sessionstart
# START_CONTRACT:
# PURPOSE: Load attempt counter from .test_counter.json and print Anti-Loop checklist
#          if prior failures exist.
# INPUTS:
# - pytest session object => session: pytest.Session
# OUTPUTS: None (side effect: prints checklist to stdout if counter > 0)
# SIDE_EFFECTS: Reads .test_counter.json. Prints to stdout.
# KEYWORDS: [PATTERN(9): AntiLoop; CONCEPT(8): SessionHook]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def pytest_sessionstart(session) -> None:
    """
    Session start hook. Reads .test_counter.json to determine the cumulative failure
    count. If failures exist, prints the Anti-Loop checklist to alert the developer/agent
    about recurring issues and escalate with each additional attempt.
    """

    # START_BLOCK_LOAD_COUNTER: [Read current attempt counter]
    counter_data = {"failures": 0}
    if _COUNTER_FILE.exists():
        try:
            with open(_COUNTER_FILE) as f:
                counter_data = json.load(f)
        except (json.JSONDecodeError, KeyError):
            counter_data = {"failures": 0}

    failures = counter_data.get("failures", 0)
    # END_BLOCK_LOAD_COUNTER

    # START_BLOCK_CHECKLIST: [Print Anti-Loop checklist based on failure count]
    if failures >= 1:
        print(f"\n{'='*70}")
        print(f"ANTI-LOOP PROTOCOL — Attempt #{failures + 1}")
        print(f"{'='*70}")
        print("CHECKLIST (common failure causes):")
        print("  [ ] Import errors: is brainstorm root in sys.path?")
        print("  [ ] LangGraph node IDs must match scenario_1_flow.xml exactly (e.g. '3.5_Weight_Parser')")
        print("  [ ] AsyncSqliteSaver requires from_conn_string() as async context manager")
        print("  [ ] fake_llm must return object with async .ainvoke() method returning AIMessage")
        print("  [ ] safe_json_parse must handle all 4 modes (plain, fenced-json, fenced-bare, prose)")
        print("  [ ] rewrite_count starts at 0; Anti-Loop fires at >= 2, NOT > 2")
        print("  [ ] conftest.py sys.path insert must use brainstorm ROOT (not src/)")
        print("  [ ] DoubleTrueError requires explicit _needs_data and _ready_for_weights in state")
        print("  [ ] @pytest.mark.asyncio required on all async test functions")
        print("  [ ] event_loop fixture must be session-scoped for AsyncSqliteSaver tests")
        print("  [ ] fake_search_async must be async callable; sleep delay proves parallelism")

    if failures == 2:
        print("\nAttempt 3: Use WebSearch or Context 7 MCP to find a solution online.")

    if failures == 3:
        print("\nWARNING: Looping risk! Pause and reflect. Are you repeating a failed strategy? Consider alternatives (Superposition).")

    if failures >= 4:
        print("\nCRITICAL ERROR: Agent looping detected. STOP. Formulate a help request for the operator.")
    # END_BLOCK_CHECKLIST
# END_FUNCTION_pytest_sessionstart


# START_FUNCTION_pytest_sessionfinish
# START_CONTRACT:
# PURPOSE: Update .test_counter.json after the session ends. Reset to 0 on 100% PASS,
#          increment on any failure.
# INPUTS:
# - pytest session object => session: pytest.Session
# - Exit code from pytest run => exitstatus: int
# OUTPUTS: None (side effect: writes .test_counter.json)
# SIDE_EFFECTS: Writes .test_counter.json.
# KEYWORDS: [PATTERN(9): AntiLoop; CONCEPT(8): SessionHook]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def pytest_sessionfinish(session, exitstatus: int) -> None:
    """
    Session finish hook. Reads existing counter, then:
    - If exitstatus == 0 (all passed): resets failures to 0.
    - If exitstatus != 0 (any failure): increments failures by 1.
    Writes updated counter back to .test_counter.json.
    """

    # START_BLOCK_LOAD_COUNTER: [Read current counter]
    counter_data = {"failures": 0}
    if _COUNTER_FILE.exists():
        try:
            with open(_COUNTER_FILE) as f:
                counter_data = json.load(f)
        except (json.JSONDecodeError, KeyError):
            counter_data = {"failures": 0}
    # END_BLOCK_LOAD_COUNTER

    # START_BLOCK_UPDATE_COUNTER: [Update based on exit status]
    if exitstatus == 0:
        counter_data["failures"] = 0
        print(f"\n[AntiLoop] All tests PASSED. Counter reset to 0.")
    else:
        counter_data["failures"] = counter_data.get("failures", 0) + 1
        print(f"\n[AntiLoop] Test failures detected. Counter now: {counter_data['failures']}")
    # END_BLOCK_UPDATE_COUNTER

    # START_BLOCK_WRITE_COUNTER: [Persist counter to file]
    try:
        with open(_COUNTER_FILE, "w") as f:
            json.dump(counter_data, f)
    except OSError as e:
        print(f"[AntiLoop][WARNING] Could not write counter file: {e}")
    # END_BLOCK_WRITE_COUNTER
# END_FUNCTION_pytest_sessionfinish


# START_FUNCTION_event_loop
# START_CONTRACT:
# PURPOSE: Session-scoped pytest-asyncio event loop fixture. Required for AsyncSqliteSaver
#          tests that chain start_session + resume_session in a single test function.
# INPUTS: None (pytest fixture)
# OUTPUTS: asyncio.AbstractEventLoop — session-scoped event loop
# SIDE_EFFECTS: Creates and closes an asyncio event loop for the test session.
# KEYWORDS: [PATTERN(8): AsyncFixture; CONCEPT(8): PytestAsyncio; TECH(9): aiosqlite]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.fixture(scope="session")
def event_loop_policy():
    """
    Session-scoped event loop policy fixture for pytest-asyncio (0.24.x compatible).

    Provides an asyncio event loop policy scoped to the whole test session. This allows
    test_async_core.py to chain start+resume on the same AsyncSqliteSaver DB across
    event-loop transitions without triggering the function-scope loop teardown between calls.

    Uses event_loop_policy (not event_loop) to comply with pytest-asyncio 0.24+ deprecation
    guidance: override the policy, not the loop instance, for session-level scope.
    """
    # START_BLOCK_CREATE_POLICY: [Return default asyncio policy for session scope]
    return asyncio.DefaultEventLoopPolicy()
    # END_BLOCK_CREATE_POLICY
# END_FUNCTION_event_loop


# START_FUNCTION_memory_checkpointer
# START_CONTRACT:
# PURPOSE: Per-test fixture providing a fresh MemorySaver checkpointer for graph compilation
#          and other tests that do NOT need SQLite persistence.
# INPUTS: None (pytest fixture)
# OUTPUTS: MemorySaver — in-memory checkpointer satisfying BaseCheckpointSaver interface
# SIDE_EFFECTS: None. Memory only.
# KEYWORDS: [PATTERN(8): DependencyInjection; CONCEPT(7): MemorySaver; PATTERN(6): TestFixture;
#            TECH(7): LangGraph]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
@pytest.fixture
def memory_checkpointer():
    """
    Per-test fixture that yields a fresh MemorySaver() instance.

    Used by test_graph_compilation.py to build the graph without creating any SQLite file.
    MemorySaver satisfies the BaseCheckpointSaver interface, so build_graph(checkpointer)
    compiles correctly. This proves the DI pattern in build_graph works for both
    MemorySaver (tests) and AsyncSqliteSaver (production).
    """
    # START_BLOCK_CREATE_SAVER: [Yield fresh MemorySaver]
    yield MemorySaver()
    # END_BLOCK_CREATE_SAVER
# END_FUNCTION_memory_checkpointer


# START_FUNCTION_fake_llm
# START_CONTRACT:
# PURPOSE: Pytest fixture providing an async-aware fake LLM factory for Dependency Injection
#          into node functions. Returns scripted JSON responses via .ainvoke(messages).
# INPUTS: None (pytest fixture)
# OUTPUTS:
# - Callable — a factory that returns a fake LLM object with async .ainvoke() method
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(8): DependencyInjection; CONCEPT(7): FakeLLM; PATTERN(6): TestFixture;
#            CONCEPT(9): AsyncIO]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
@pytest.fixture
def fake_llm():
    """
    Pytest fixture providing an async-aware fake LLM factory for Dependency Injection.

    Returns a factory callable. When called (as llm_factory()), it returns a fake LLM
    object whose .ainvoke() method is an async function returning a scripted AIMessage
    with needs_rewrite=True in the JSON body. This is used by test_anti_loop.py to
    simulate a CoVe Critique response that would request a rewrite — but with
    rewrite_count=2 the Anti-Loop cap should override it.

    The factory pattern matches the llm_factory parameter signature in node functions:
        cove_critique(state, llm_factory=fake_llm)
    where fake_llm is the fixture value (a Callable returning the fake LLM instance).

    Provides BOTH .ainvoke() (async — primary path in v2.0.0) and .invoke() (sync —
    fallback for compatibility) to prevent AttributeError in any code path.
    """

    # START_BLOCK_CREATE_FAKE: [Create fake LLM class with async ainvoke and sync invoke]
    class _FakeLLM:
        """Minimal async-aware fake LLM for test injection."""

        def __init__(self, content: str):
            self._content = content

        async def ainvoke(self, messages) -> AIMessage:
            """Async invoke: returns scripted AIMessage immediately."""
            return AIMessage(content=self._content)

        def invoke(self, messages) -> AIMessage:
            """Sync invoke: fallback compatibility method."""
            return AIMessage(content=self._content)

    # Script the response: needs_rewrite=True so Anti-Loop must override it
    scripted_content = '{"needs_rewrite": true, "critique_feedback": "Scripted critique for Anti-Loop test"}'

    def make_fake_llm() -> _FakeLLM:
        return _FakeLLM(content=scripted_content)

    return make_fake_llm
    # END_BLOCK_CREATE_FAKE
# END_FUNCTION_fake_llm


# START_FUNCTION_fake_llm_factory_for_graph
# START_CONTRACT:
# PURPOSE: Extended fake LLM factory fixture for graph integration tests (test_async_core.py).
#          Returns node-aware scripted responses: different JSON for different node calls.
#          Specifically ensures context_analyzer returns ready_for_weights=True on first call
#          to avoid a tool_node loop (no search needed), weight_questioner returns a question,
#          weight_parser returns weights, draft_generator returns analysis, cove_critique
#          returns needs_rewrite=False, final_synthesizer returns a final answer.
# INPUTS: None (pytest fixture)
# OUTPUTS: Callable factory returning a smart fake LLM with context-aware responses
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(8): DependencyInjection; CONCEPT(9): AsyncIO; CONCEPT(8): ScriptedResponses]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.fixture
def fake_llm_for_graph():
    """
    Extended async fake LLM factory for full graph integration tests.

    Returns a factory that produces a stateful fake LLM which cycles through scripted
    responses appropriate for each graph node in sequence:
    1. context_analyzer call: returns {dilemma, needs_data: false, ready_for_weights: true}
    2. weight_questioner call: returns {last_question: "What are your priorities?"}
    3. weight_parser call: returns {weights: {cost: 7, flexibility: 8}}
    4. draft_generator call: returns draft analysis text (plain string — no JSON required)
    5. cove_critique call: returns {needs_rewrite: false, critique_feedback: ""}
    6. final_synthesizer call: returns final answer text

    The LLM uses a call counter to return the correct scripted response per invocation.
    """

    # START_BLOCK_CREATE_STATEFUL_FAKE: [Create call-counter-based fake LLM]
    class _StatefulFakeLLM:
        """Async fake LLM that returns different content on each successive call."""

        _RESPONSES = [
            # Call 0 — context_analyzer (Node 1): skip tool node, go directly to questioner
            '{"dilemma": "Buy house vs rent", "needs_data": false, "search_queries": [], "ready_for_weights": true}',
            # Call 1 — weight_questioner (Node 3): generate calibration question
            '{"last_question": "What is more important to you — cost savings or flexibility?"}',
            # Call 2 — weight_parser (Node 3.5): parse user answer into weights
            '{"weights": {"cost": 7, "flexibility": 8}, "assumptions": ""}',
            # Call 3 — draft_generator (Node 4): return plain draft text
            "Based on your priorities, renting offers more flexibility in the short term.",
            # Call 4 — cove_critique (Node 5): approve draft — no rewrite needed
            '{"needs_rewrite": false, "critique_feedback": ""}',
            # Call 5 — final_synthesizer (Node 6): return final markdown answer
            "## Decision Analysis\n\nGiven your priorities, renting is recommended.",
        ]

        def __init__(self):
            self._call_count = 0

        async def ainvoke(self, messages) -> AIMessage:
            """Return response for current call index, then increment counter."""
            idx = min(self._call_count, len(self._RESPONSES) - 1)
            content = self._RESPONSES[idx]
            self._call_count += 1
            return AIMessage(content=content)

        def invoke(self, messages) -> AIMessage:
            """Sync fallback — mirrors ainvoke logic."""
            idx = min(self._call_count, len(self._RESPONSES) - 1)
            content = self._RESPONSES[idx]
            self._call_count += 1
            return AIMessage(content=content)

    # The factory must return a NEW LLM instance each time it is called
    # so that each node gets a fresh call counter during graph execution.
    # However, for full-graph integration we need a SHARED instance across all nodes
    # so the call counter advances correctly. We use a container to share the instance.
    _shared_instance: dict = {}

    def make_fake_llm_for_graph() -> _StatefulFakeLLM:
        if "instance" not in _shared_instance:
            _shared_instance["instance"] = _StatefulFakeLLM()
        return _shared_instance["instance"]

    return make_fake_llm_for_graph
    # END_BLOCK_CREATE_STATEFUL_FAKE
# END_FUNCTION_fake_llm_factory_for_graph


# START_FUNCTION_fake_search_async
# START_CONTRACT:
# PURPOSE: Fixture providing an async search callable with configurable sleep delay.
#          Used by test_parallel_search to prove asyncio.gather parallelism.
# INPUTS: None (pytest fixture)
# OUTPUTS:
# - Callable — async function accepting (query: str, delay: float=0.2) and returning List[Dict]
# SIDE_EFFECTS: Sleeps for `delay` seconds (default 0.2) to simulate network latency.
# KEYWORDS: [PATTERN(8): DependencyInjection; CONCEPT(9): AsyncIO; CONCEPT(8): FakeSearch;
#            PATTERN(7): ConcurrencyTest]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.fixture
def fake_search_async():
    """
    Pytest fixture providing an async search callable with configurable sleep delay.

    The returned callable (not a factory — the callable itself) accepts:
    - query: str — the search query (echoed in result for assertion)
    - delay: float = 0.2 — sleep duration simulating network latency

    Used by test_parallel_search.py to prove that 3 queries with DELAY=0.2s each
    complete in wall-clock < 0.5s when executed via asyncio.gather (as opposed to
    sequential execution which would take >= 0.6s).

    Also emits IMP:7 [PENDING] and IMP:8 [SUCCESS] LDD log lines per query so that
    test_parallel_search can assert exactly one PENDING + one SUCCESS pair per query.
    """
    from src.core.logger import setup_ldd_logger as _get_logger

    _fixture_logger = _get_logger()

    # START_BLOCK_DEFINE_FAKE_SEARCH: [Async callable with delay and LDD telemetry]
    async def _fake_search(query: str, delay: float = 0.2):
        """Async stub search with configurable latency for concurrency testing."""
        _fixture_logger.info(
            f"[API][IMP:7][tool_node][BLOCK_EXECUTE_SEARCHES][ExternalCall] "
            f"query={query!r} [PENDING]"
        )
        await asyncio.sleep(delay)
        result = [{"query": query, "result": f"<fake-result-for-{query}>", "source": "fake"}]
        _fixture_logger.info(
            f"[API][IMP:8][tool_node][BLOCK_EXECUTE_SEARCHES][ResponseReceived] "
            f"query={query!r} items={len(result)} [SUCCESS]"
        )
        return result
    # END_BLOCK_DEFINE_FAKE_SEARCH

    return _fake_search
# END_FUNCTION_fake_search_async


# START_FUNCTION_ldd_capture
# START_CONTRACT:
# PURPOSE: Fixture helper that filters caplog records for IMP:7-10 and prints them
#          to stdout. Returns filtered list for assertion use.
# INPUTS:
# - pytest caplog fixture => caplog: pytest.LogCaptureFixture
# OUTPUTS:
# - Callable — accepts optional list of log records, returns List[str] with IMP >= 7
# SIDE_EFFECTS: Prints filtered LDD trajectory to stdout.
# KEYWORDS: [CONCEPT(8): LDDTelemetry; PATTERN(7): CaplogFilter]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.fixture
def ldd_capture(caplog):
    """
    Fixture helper that processes caplog records to extract and print IMP:7-10 log lines.

    Call as: high_imp_logs = ldd_capture(caplog) AFTER the code under test has run.
    The fixture yields a callable that filters and prints, returning the filtered list.
    """

    # START_BLOCK_DEFINE_CAPTURE: [Define the capture callable]
    def capture(log_records=None) -> List[str]:
        records = log_records if log_records is not None else caplog.records
        found = []
        print("\n--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in records:
            msg = record.message if hasattr(record, "message") else str(record.getMessage())
            if "[IMP:" in msg:
                try:
                    imp_level = int(msg.split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
                        found.append(msg)
                except (IndexError, ValueError):
                    continue
        if not found:
            print("(no IMP:7-10 records found in caplog)")
        print("--- END LDD TRAJECTORY ---\n")
        return found
    # END_BLOCK_DEFINE_CAPTURE

    return capture
# END_FUNCTION_ldd_capture
