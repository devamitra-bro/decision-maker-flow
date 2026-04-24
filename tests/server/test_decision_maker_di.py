# FILE: tests/server/test_decision_maker_di.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Tests for the Slice B DI seams in src/features/decision_maker/graph.py and
#          the new build_llm_client() in src/core/llm_client.py. Verifies:
#          backward compat (Gradio UI path: no kwargs → no regression),
#          injected checkpointer (from_conn_string NOT called when checkpointer= given),
#          injected llm_client (env leak detection), and all 4 public functions.
# SCOPE: graph.py DI kwargs; nodes._LLM_CLIENT_OVERRIDE; build_llm_client env-isolation.
# INPUT: pytest monkeypatch, tmp_path, MemorySaver for checkpointer mocking.
# OUTPUT: Tests verifying contract of DI seams without real LLM or network calls.
# KEYWORDS: [DOMAIN(9): Testing; CONCEPT(9): DependencyInjection; PATTERN(9): MockCheckpointer;
#            CONCEPT(8): EnvIsolation; PATTERN(8): AntiLoop; TECH(8): MonkeyPatch]
# LINKS: [READS_DATA_FROM(9): src/features/decision_maker/graph.py;
#         READS_DATA_FROM(8): src/features/decision_maker/nodes.py;
#         READS_DATA_FROM(8): src/core/llm_client.py]
# LINKS_TO_SPECIFICATION: [§2.2 Slice B; §9.3 build_llm_client; §9.4 DI seams]
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why use MemorySaver rather than a full mock for injected checkpointer tests?
# A: MemorySaver is the LangGraph-provided in-memory saver used in all existing
#    decision_maker tests. It satisfies the same BaseCheckpointSaver interface and
#    allows the graph to actually run (with FakeLLM). This avoids needing to mock
#    the compiled graph's internal saver calls.
# Q: Why monkeypatch AsyncSqliteSaver.from_conn_string rather than inspecting call counts?
# A: We need to assert it is NOT called when checkpointer= is injected. The simplest
#    approach is to replace it with a function that increments a counter and raises
#    an AssertionError if called. This is aggressive but gives a clear failure signal.
# Q: Why is FakeLLM needed? Can we use unittest.mock.Mock?
# A: Using a real async mock without the ainvoke method would fail at graph invocation.
#    FakeLLM wraps a callable that returns a deterministic fake response, matching the
#    pattern used in existing decision_maker unit tests (DI over mocking).
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice B: DI seam tests.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 7 [Test backward compat: start_session without DI kwargs] => test_start_session_backward_compat_no_kwargs
# FUNC 8 [Test injected checkpointer skips from_conn_string — start_session] => test_start_session_with_injected_checkpointer_skips_async_cm
# FUNC 7 [Same test for resume_session] => test_resume_session_with_injected_checkpointer_skips_async_cm
# FUNC 7 [Same test for stream_session] => test_stream_session_with_injected_checkpointer_skips_async_cm
# FUNC 7 [Same test for stream_resume_session] => test_stream_resume_session_with_injected_checkpointer_skips_async_cm
# FUNC 8 [Test llm_client injection propagates to nodes via override] => test_llm_client_injection_propagates_to_nodes
# FUNC 8 [Test build_llm_client ignores OPENROUTER_API_KEY env var] => test_build_llm_client_ignores_openrouter_env
# END_MODULE_MAP
#
# START_USE_CASES:
# - [test_start_session_backward_compat_no_kwargs]: CI -> start_session old signature -> no exception
# - [test_start_session_with_injected_checkpointer_skips_async_cm]: CI -> inject CP -> from_conn_string not called
# - [test_build_llm_client_ignores_openrouter_env]: CI -> OPENROUTER_API_KEY set -> not leaked into client
# END_USE_CASES

import sys
import asyncio
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure brainstorm root is on sys.path
_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

class FakeLLMResponse:
    """Minimal response object that looks like an LLM response."""
    def __init__(self, content: str = "fake_response"):
        self.content = content


class FakeLLM:
    """
    Minimal async-compatible fake LLM for DI injection in tests.
    Records invocations so tests can assert it was (or was not) called.
    Returns a deterministic FakeLLMResponse on ainvoke.
    """
    def __init__(self, content: str = "FAKE_LLM_RESPONSE"):
        self.invocations: list = []
        self._content = content

    async def ainvoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
        """Record the invocation and return a fake response."""
        self.invocations.append(messages)
        return FakeLLMResponse(content=self._content)

    def invoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
        """Sync variant for completeness."""
        self.invocations.append(messages)
        return FakeLLMResponse(content=self._content)


def _make_fake_json_response(content: str) -> FakeLLMResponse:
    """Return a FakeLLMResponse with valid JSON for context_analyzer node."""
    import json
    payload = json.dumps({
        "dilemma": "test dilemma",
        "is_data_sufficient": True,
        "search_queries": [],
    })
    return FakeLLMResponse(content=payload)


class FakeLLMContextAnalyzer:
    """
    FakeLLM that returns appropriate JSON for context_analyzer so the graph
    can proceed to 3_Weight_Questioner without needing real LLM.
    """
    def __init__(self):
        self.invocations: list = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
        import json
        self.invocations.append(messages)
        # Return valid JSON for different nodes
        # context_analyzer needs: dilemma, is_data_sufficient, search_queries
        context_response = json.dumps({
            "dilemma": "test decision dilemma",
            "is_data_sufficient": True,
            "search_queries": [],
        })
        # weight_questioner needs: last_question
        questioner_response = json.dumps({"last_question": "What is your priority?"})
        # Return context_response for first call, questioner for rest
        if len(self.invocations) <= 1:
            return FakeLLMResponse(content=context_response)
        return FakeLLMResponse(content=questioner_response)

    def invoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
        return asyncio.get_event_loop().run_until_complete(self.ainvoke(messages))


# ---------------------------------------------------------------------------
# START_FUNCTION_test_start_session_backward_compat_no_kwargs
# START_CONTRACT:
# PURPOSE: Verify start_session still accepts the OLD signature (no DI kwargs) and
#          produces no exception during import or call shape validation.
#          Mocks build_llm to prevent env reads; does NOT actually run the LangGraph.
# INPUTS:
#   - tmp_path, monkeypatch
# KEYWORDS: [CONCEPT(9): BackwardCompat; PATTERN(8): SignatureVerification]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_start_session_backward_compat_no_kwargs(tmp_path, monkeypatch):
    """
    Verify the signature of start_session is backward compatible: it can be called
    with only user_input, thread_id, checkpoint_path (the old positional/keyword form)
    without any TypeError. We verify the function signature rather than actually
    running the async graph to avoid env/LLM requirements.
    """
    import inspect
    from src.features.decision_maker import graph as g

    # START_BLOCK_SIGNATURE_CHECK: [Verify DI kwargs are Optional with None defaults]
    sig = inspect.signature(g.start_session)
    params = sig.parameters

    assert "user_input" in params, "user_input param must exist"
    assert "thread_id" in params, "thread_id param must exist"
    assert "checkpoint_path" in params, "checkpoint_path param must exist"
    assert params["checkpoint_path"].default is None, "checkpoint_path default must be None"
    assert "checkpointer" in params, "checkpointer DI param must be added"
    assert params["checkpointer"].default is None, "checkpointer default must be None"
    assert "llm_client" in params, "llm_client DI param must be added"
    assert params["llm_client"].default is None, "llm_client default must be None"
    # END_BLOCK_SIGNATURE_CHECK

    # START_BLOCK_SAME_FOR_ALL_FOUR: [Same check for all 4 public functions]
    for fn_name in ("start_session", "resume_session", "stream_session", "stream_resume_session"):
        fn = getattr(g, fn_name)
        fn_sig = inspect.signature(fn)
        fn_params = fn_sig.parameters
        assert "checkpointer" in fn_params, f"{fn_name} must have checkpointer kwarg"
        assert fn_params["checkpointer"].default is None, f"{fn_name}.checkpointer default must be None"
        assert "llm_client" in fn_params, f"{fn_name} must have llm_client kwarg"
        assert fn_params["llm_client"].default is None, f"{fn_name}.llm_client default must be None"
    # END_BLOCK_SAME_FOR_ALL_FOUR

# END_FUNCTION_test_start_session_backward_compat_no_kwargs


# ---------------------------------------------------------------------------
# START_FUNCTION_test_start_session_with_injected_checkpointer_skips_async_cm
# START_CONTRACT:
# PURPOSE: Verify that passing checkpointer=<MemorySaver> to start_session causes the
#          injected path to be used and AsyncSqliteSaver.from_conn_string is NOT called.
# INPUTS:
#   - tmp_path, monkeypatch, caplog
# KEYWORDS: [PATTERN(9): MonkeyPatchGuard; CONCEPT(9): DIBypass; TECH(8): MemorySaver]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_start_session_with_injected_checkpointer_skips_async_cm(tmp_path, monkeypatch, caplog):
    """
    Verify the MCP server DI path: when checkpointer= is supplied to start_session,
    AsyncSqliteSaver.from_conn_string is NOT called (call_count == 0).

    Uses MemorySaver (the standard LangGraph in-memory saver) as the injected checkpointer
    and FakeLLMContextAnalyzer to produce valid node outputs without real LLM calls.
    """
    import logging
    caplog.set_level(logging.INFO)

    from langgraph.checkpoint.memory import MemorySaver
    from src.features.decision_maker import graph as g
    from src.features.decision_maker import nodes as n

    # START_BLOCK_TRACK_FROM_CONN_STRING: [Replace from_conn_string with a call-counting guard]
    call_count = {"from_conn_string": 0}
    original_fcs = g.AsyncSqliteSaver.from_conn_string

    def guarded_from_conn_string(path):
        call_count["from_conn_string"] += 1
        return original_fcs(path)

    monkeypatch.setattr(g.AsyncSqliteSaver, "from_conn_string", staticmethod(guarded_from_conn_string))
    # END_BLOCK_TRACK_FROM_CONN_STRING

    # START_BLOCK_INJECT_FAKE_LLM: [Set module-level LLM override to FakeLLMContextAnalyzer]
    fake_llm = FakeLLMContextAnalyzer()
    # END_BLOCK_INJECT_FAKE_LLM

    # START_BLOCK_RUN_START_SESSION: [Call start_session with injected checkpointer and llm_client]
    memory_saver = MemorySaver()
    result = await g.start_session(
        user_input="Should I invest in stocks or bonds?",
        thread_id="di-test-thread-001",
        checkpointer=memory_saver,
        llm_client=fake_llm,
    )
    # END_BLOCK_RUN_START_SESSION

    # START_BLOCK_ASSERTIONS: [Core DI contract assertions]
    assert call_count["from_conn_string"] == 0, (
        f"AsyncSqliteSaver.from_conn_string must NOT be called when checkpointer= is injected. "
        f"Call count was {call_count['from_conn_string']}"
    )

    assert result.get("status") in ("awaiting_user", "done"), (
        f"start_session should return a valid status dict. Got: {result}"
    )

    # Verify _LLM_CLIENT_OVERRIDE was cleared after the call
    assert n._LLM_CLIENT_OVERRIDE is None, (
        "_LLM_CLIENT_OVERRIDE must be cleared to None after start_session completes"
    )

    # FakeLLM must have been invoked at least once (at least context_analyzer ran)
    assert len(fake_llm.invocations) >= 1, (
        "FakeLLM should have been invoked at least once by the graph nodes"
    )
    # END_BLOCK_ASSERTIONS

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-9 logs]
    high_imp_logs = [
        r.message for r in caplog.records
        if "[IMP:" in r.message and any(f"[IMP:{n}]" in r.message for n in ("7", "8", "9"))
    ]
    print("\n--- LDD TRAJECTORY (IMP:7-9) ---")
    for msg in high_imp_logs[:10]:
        print(msg)
    print("--- END LDD TRAJECTORY ---")
    # END_BLOCK_LDD_TELEMETRY

# END_FUNCTION_test_start_session_with_injected_checkpointer_skips_async_cm


# ---------------------------------------------------------------------------
# START_FUNCTION_test_resume_session_with_injected_checkpointer_skips_async_cm
# START_CONTRACT:
# PURPOSE: Same as start_session test but for resume_session.
# KEYWORDS: [CONCEPT(9): DIBypass; TECH(8): MemorySaver]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.mark.asyncio
async def test_resume_session_with_injected_checkpointer_skips_async_cm(monkeypatch, caplog):
    """
    Verify resume_session with injected checkpointer= does not call from_conn_string.
    We run a start_session first (with MemorySaver) to create the interrupted checkpoint,
    then call resume_session with the SAME MemorySaver to complete it.
    """
    import logging
    caplog.set_level(logging.INFO)

    from langgraph.checkpoint.memory import MemorySaver
    from src.features.decision_maker import graph as g

    call_count = {"from_conn_string": 0}
    original_fcs = g.AsyncSqliteSaver.from_conn_string

    def guarded_from_conn_string(path):
        call_count["from_conn_string"] += 1
        return original_fcs(path)

    monkeypatch.setattr(g.AsyncSqliteSaver, "from_conn_string", staticmethod(guarded_from_conn_string))

    # Use a fake LLM that can handle all nodes
    class FullGraphFakeLLM:
        """
        FakeLLM that returns appropriate responses for all nodes through graph completion.
        IMPORTANT: cove_critique route_from_critique uses critique_feedback emptiness as
        the proxy for needs_rewrite. To avoid the rewrite loop, critique_feedback must be
        empty string (not "Draft is acceptable." which would trigger a rewrite).
        """
        def __init__(self):
            self.invocations: list = []

        async def ainvoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
            import json
            self.invocations.append(messages)
            call_n = len(self.invocations)
            if call_n == 1:
                # context_analyzer
                return FakeLLMResponse(content=json.dumps({
                    "dilemma": "test dilemma for resume",
                    "is_data_sufficient": True,
                    "search_queries": [],
                }))
            elif call_n == 2:
                # weight_questioner
                return FakeLLMResponse(content=json.dumps({"last_question": "What is your timeline?"}))
            elif call_n == 3:
                # weight_parser (after resume with user_answer)
                return FakeLLMResponse(content=json.dumps({
                    "weights": {"timeline": 0.8, "risk": 0.2},
                    "assumptions": "short term",
                }))
            elif call_n == 4:
                # draft_generator
                return FakeLLMResponse(content="Draft analysis content here.")
            elif call_n == 5:
                # cove_critique — MUST return empty critique_feedback to avoid rewrite loop
                # route_from_critique: needs_rewrite = bool(critique_feedback) and rewrite_count < 2
                return FakeLLMResponse(content=json.dumps({
                    "needs_rewrite": False,
                    "critique_feedback": "",  # empty = no rewrite needed
                }))
            else:
                # final_synthesizer
                return FakeLLMResponse(content="Final answer content here.")

        def invoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
            self.invocations.append(messages)
            return FakeLLMResponse(content="sync_fake")

    # Run start_session first to create checkpoint
    memory_saver = MemorySaver()
    fake_llm_full = FullGraphFakeLLM()

    start_result = await g.start_session(
        user_input="Test resume DI",
        thread_id="di-resume-thread-001",
        checkpointer=memory_saver,
        llm_client=fake_llm_full,
    )
    assert start_result.get("status") in ("awaiting_user", "done")

    start_call_count = call_count["from_conn_string"]

    # Now call resume_session with the SAME MemorySaver
    resume_result = await g.resume_session(
        user_answer="I prefer short term investments",
        thread_id="di-resume-thread-001",
        checkpointer=memory_saver,
        llm_client=fake_llm_full,
    )

    # Assert from_conn_string was NOT called in resume_session
    resume_calls = call_count["from_conn_string"] - start_call_count
    assert resume_calls == 0, (
        f"from_conn_string must NOT be called in resume_session when checkpointer= is injected. "
        f"Resume-phase call count was {resume_calls}"
    )

    assert resume_result.get("status") in ("done",), (
        f"resume_session should return status='done'. Got: {resume_result}"
    )

# END_FUNCTION_test_resume_session_with_injected_checkpointer_skips_async_cm


# ---------------------------------------------------------------------------
# START_FUNCTION_test_stream_session_with_injected_checkpointer_skips_async_cm
# START_CONTRACT:
# PURPOSE: Same as start_session test but for stream_session (async generator).
# KEYWORDS: [PATTERN(10): AsyncGeneratorDI; CONCEPT(9): DIBypass]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.mark.asyncio
async def test_stream_session_with_injected_checkpointer_skips_async_cm(monkeypatch, caplog):
    """
    Verify stream_session with injected checkpointer= does not call from_conn_string.
    Stream the generator to completion and assert the awaiting_user sentinel is emitted.
    """
    import logging
    caplog.set_level(logging.INFO)

    from langgraph.checkpoint.memory import MemorySaver
    from src.features.decision_maker import graph as g

    call_count = {"from_conn_string": 0}
    original_fcs = g.AsyncSqliteSaver.from_conn_string

    def guarded_from_conn_string(path):
        call_count["from_conn_string"] += 1
        return original_fcs(path)

    monkeypatch.setattr(g.AsyncSqliteSaver, "from_conn_string", staticmethod(guarded_from_conn_string))

    fake_llm = FakeLLMContextAnalyzer()
    memory_saver = MemorySaver()

    # Consume the async generator
    chunks = []
    async for chunk in g.stream_session(
        user_input="streaming DI test",
        thread_id="di-stream-thread-001",
        checkpointer=memory_saver,
        llm_client=fake_llm,
    ):
        chunks.append(chunk)

    assert call_count["from_conn_string"] == 0, (
        f"from_conn_string must NOT be called in stream_session when checkpointer= is injected. "
        f"Got: {call_count['from_conn_string']}"
    )

    # Verify awaiting_user sentinel was yielded
    sentinel_chunks = [c for c in chunks if c.get("__awaiting_user__") is True]
    assert len(sentinel_chunks) >= 1, (
        f"stream_session should yield awaiting_user sentinel. Chunks: {chunks}"
    )

# END_FUNCTION_test_stream_session_with_injected_checkpointer_skips_async_cm


# ---------------------------------------------------------------------------
# START_FUNCTION_test_stream_resume_session_with_injected_checkpointer_skips_async_cm
# START_CONTRACT:
# PURPOSE: Same as resume_session test but for stream_resume_session (async generator).
# KEYWORDS: [PATTERN(10): AsyncGeneratorDI; CONCEPT(9): DIBypass]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.mark.asyncio
async def test_stream_resume_session_with_injected_checkpointer_skips_async_cm(monkeypatch, caplog):
    """
    Verify stream_resume_session with injected checkpointer= does not call from_conn_string.
    Run start_session first to create the checkpoint, then stream_resume_session.
    """
    import logging
    caplog.set_level(logging.INFO)

    from langgraph.checkpoint.memory import MemorySaver
    from src.features.decision_maker import graph as g

    call_count = {"from_conn_string": 0}
    original_fcs = g.AsyncSqliteSaver.from_conn_string

    def guarded_from_conn_string(path):
        call_count["from_conn_string"] += 1
        return original_fcs(path)

    monkeypatch.setattr(g.AsyncSqliteSaver, "from_conn_string", staticmethod(guarded_from_conn_string))

    # Full graph fake LLM for all nodes
    class FullGraphFakeLLM2:
        def __init__(self):
            self.invocations: list = []

        async def ainvoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
            import json
            self.invocations.append(messages)
            call_n = len(self.invocations)
            if call_n == 1:
                return FakeLLMResponse(content=json.dumps({
                    "dilemma": "DI stream test",
                    "is_data_sufficient": True,
                    "search_queries": [],
                }))
            elif call_n == 2:
                return FakeLLMResponse(content=json.dumps({"last_question": "What timeframe?"}))
            elif call_n == 3:
                return FakeLLMResponse(content=json.dumps({"weights": {"t": 1.0}}))
            elif call_n == 4:
                return FakeLLMResponse(content="Draft here.")
            elif call_n == 5:
                # Empty critique_feedback to avoid rewrite loop (route_from_critique uses emptiness)
                return FakeLLMResponse(content=json.dumps({"needs_rewrite": False, "critique_feedback": ""}))
            else:
                return FakeLLMResponse(content="Final answer here.")

        def invoke(self, messages: Any, **kwargs: Any) -> FakeLLMResponse:
            self.invocations.append(messages)
            return FakeLLMResponse(content="sync")

    fake_llm = FullGraphFakeLLM2()
    memory_saver = MemorySaver()
    thread_id = "di-stream-resume-thread-001"

    # Start session first
    start_chunks = []
    async for chunk in g.stream_session(
        user_input="DI stream resume test",
        thread_id=thread_id,
        checkpointer=memory_saver,
        llm_client=fake_llm,
    ):
        start_chunks.append(chunk)

    start_call_count = call_count["from_conn_string"]

    # Now stream_resume
    resume_chunks = []
    async for chunk in g.stream_resume_session(
        user_answer="short term",
        thread_id=thread_id,
        checkpointer=memory_saver,
        llm_client=fake_llm,
    ):
        resume_chunks.append(chunk)

    resume_calls = call_count["from_conn_string"] - start_call_count
    assert resume_calls == 0, (
        f"from_conn_string must NOT be called in stream_resume_session with injected CP. "
        f"Got: {resume_calls}"
    )

    done_chunks = [c for c in resume_chunks if c.get("__done__") is True]
    assert len(done_chunks) >= 1, (
        f"stream_resume_session should yield done sentinel. Chunks: {resume_chunks}"
    )

# END_FUNCTION_test_stream_resume_session_with_injected_checkpointer_skips_async_cm


# ---------------------------------------------------------------------------
# START_FUNCTION_test_llm_client_injection_propagates_to_nodes
# START_CONTRACT:
# PURPOSE: Verify that when llm_client= is passed to start_session, the FakeLLM
#          records at least one invocation (confirming the override path was used).
#          Also verifies _LLM_CLIENT_OVERRIDE is cleared to None after the call.
# KEYWORDS: [CONCEPT(9): LLMOverridePropagation; PATTERN(8): OverrideVerification]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_llm_client_injection_propagates_to_nodes(monkeypatch, caplog):
    """
    Verify that injecting llm_client= into start_session causes the override LLM
    to be called by node functions (via _LLM_CLIENT_OVERRIDE in nodes.py).

    After the call, _LLM_CLIENT_OVERRIDE must be cleared (None) to prevent leakage.
    We also assert that the original build_llm was NOT called (since the override
    short-circuits it).
    """
    import logging
    caplog.set_level(logging.INFO)

    from langgraph.checkpoint.memory import MemorySaver
    from src.features.decision_maker import graph as g
    from src.features.decision_maker import nodes as n

    # Track if build_llm was called (it should NOT be if override is used)
    build_llm_call_count = {"count": 0}
    original_build_llm = None

    # We need to monkeypatch build_llm inside the nodes module's lazy import
    import src.core.llm_client as llm_client_module
    original_build_llm = llm_client_module.build_llm

    def tracking_build_llm(*args, **kwargs):
        build_llm_call_count["count"] += 1
        return original_build_llm(*args, **kwargs)

    monkeypatch.setattr(llm_client_module, "build_llm", tracking_build_llm)

    # Create our recording FakeLLM
    fake_llm = FakeLLMContextAnalyzer()

    memory_saver = MemorySaver()

    # Call start_session with the fake LLM injected
    result = await g.start_session(
        user_input="Test llm_client injection",
        thread_id="di-llm-injection-thread",
        checkpointer=memory_saver,
        llm_client=fake_llm,
    )

    # START_BLOCK_ASSERTIONS: [Verify override was used and cleared]
    # FakeLLM must have been called (at least context_analyzer ran)
    assert len(fake_llm.invocations) >= 1, (
        f"FakeLLM should have been invoked by nodes using _LLM_CLIENT_OVERRIDE. "
        f"Invocations: {fake_llm.invocations}"
    )

    # _LLM_CLIENT_OVERRIDE must be None after the call (cleared in finally block)
    assert n._LLM_CLIENT_OVERRIDE is None, (
        "nodes._LLM_CLIENT_OVERRIDE must be None after start_session with llm_client= completes"
    )

    # Since FakeLLM was used, build_llm should NOT have been called
    # (the override short-circuits the else branch in each node)
    assert build_llm_call_count["count"] == 0, (
        f"build_llm should NOT be called when _LLM_CLIENT_OVERRIDE is set. "
        f"Call count: {build_llm_call_count['count']}"
    )
    # END_BLOCK_ASSERTIONS

# END_FUNCTION_test_llm_client_injection_propagates_to_nodes


# ---------------------------------------------------------------------------
# START_FUNCTION_test_build_llm_client_ignores_openrouter_env
# START_CONTRACT:
# PURPOSE: Verify §9.3 env-isolation: even with OPENROUTER_API_KEY in env,
#          build_llm_client(cfg) uses cfg.gateway_llm_api_key exclusively.
#          The resulting client's api_key must NOT contain "leak_detector".
# INPUTS:
#   - monkeypatch, server_env
# KEYWORDS: [CONCEPT(9): EnvIsolation; PATTERN(9): LeakDetector; TECH(8): SecretStr]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_build_llm_client_ignores_openrouter_env(monkeypatch, server_env):
    """
    Verify that build_llm_client(cfg) reads api_key from cfg.gateway_llm_api_key
    and NOT from OPENROUTER_API_KEY. This is the §9.3 env-isolation contract.

    Test strategy:
    1. Set OPENROUTER_API_KEY to a sentinel value "leak_detector".
    2. Build client via build_llm_client(cfg) where cfg.gateway_llm_api_key is "real_key".
    3. Assert client.openai_api_base == cfg.gateway_llm_proxy_url.
    4. Assert the client's api_key does NOT contain "leak_detector".
    """
    from pydantic import SecretStr
    from src.server.config import get_cfg
    from src.core.llm_client import build_llm_client

    # START_BLOCK_ENV_SETUP: [Set leak detector env var]
    monkeypatch.setenv("OPENROUTER_API_KEY", "leak_detector_value_should_not_appear_in_client")
    # END_BLOCK_ENV_SETUP

    # START_BLOCK_BUILD_CFG: [Build config with explicit gateway settings]
    get_cfg.cache_clear()
    cfg = get_cfg()

    # Verify the config reads from the right env vars
    assert cfg.gateway_llm_proxy_url == "https://test-llm-proxy.example.com/v1"
    assert cfg.gateway_llm_api_key.get_secret_value() == "test-api-key-for-unit-tests"
    # END_BLOCK_BUILD_CFG

    # START_BLOCK_BUILD_CLIENT: [Build llm_client via build_llm_client(cfg)]
    client = build_llm_client(cfg)
    # END_BLOCK_BUILD_CLIENT

    # START_BLOCK_ASSERT_NO_LEAK: [Assert leak_detector is not in the api_key]
    # ChatOpenAI stores api_key as a SecretStr or string
    if hasattr(client, "openai_api_key"):
        api_key_attr = client.openai_api_key
        if hasattr(api_key_attr, "get_secret_value"):
            raw_key = api_key_attr.get_secret_value()
        else:
            raw_key = str(api_key_attr)
    else:
        # Fallback: check the client_kwargs stored internally
        raw_key = str(getattr(client, "_client_kwargs", {}).get("api_key", ""))

    assert "leak_detector" not in raw_key, (
        f"build_llm_client MUST NOT read OPENROUTER_API_KEY from env. "
        f"Found leak in api_key: {raw_key[:20]}..."
    )

    # Assert the base_url is set correctly from cfg
    if hasattr(client, "openai_api_base"):
        base_url = client.openai_api_base
    else:
        base_url = str(getattr(client, "base_url", ""))

    assert "test-llm-proxy.example.com" in str(base_url), (
        f"build_llm_client must use cfg.gateway_llm_proxy_url as base_url. "
        f"Got: {base_url!r}"
    )
    # END_BLOCK_ASSERT_NO_LEAK

    print(f"\n--- build_llm_client env-isolation PASS ---")
    print(f"base_url = {base_url}")
    print(f"api_key does not contain 'leak_detector' [PASS]")

# END_FUNCTION_test_build_llm_client_ignores_openrouter_env


# ---------------------------------------------------------------------------
# START_FUNCTION_test_lll_client_override_cleared_on_exception
# START_CONTRACT:
# PURPOSE: Verify that _LLM_CLIENT_OVERRIDE is cleared (None) even when the graph
#          raises an exception, confirming the finally block works correctly.
# KEYWORDS: [CONCEPT(8): ExceptionSafety; PATTERN(7): FinallyBlock]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
@pytest.mark.asyncio
async def test_llm_client_override_cleared_on_exception(monkeypatch, caplog):
    """
    Verify _LLM_CLIENT_OVERRIDE is cleared to None even if start_session raises.

    Inject a fake llm_client that raises RuntimeError on ainvoke (simulating LLM failure).
    Assert that after the exception propagates, _LLM_CLIENT_OVERRIDE is None.
    """
    import logging
    caplog.set_level(logging.INFO)

    from langgraph.checkpoint.memory import MemorySaver
    from src.features.decision_maker import graph as g
    from src.features.decision_maker import nodes as n

    class ErrorLLM:
        """LLM that always raises to test the finally block cleanup."""
        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Simulated LLM failure for test")

        def invoke(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Simulated LLM failure for test")

    error_llm = ErrorLLM()
    memory_saver = MemorySaver()

    with pytest.raises(RuntimeError, match="Simulated LLM failure"):
        await g.start_session(
            user_input="Test override cleared on exception",
            thread_id="di-exception-thread",
            checkpointer=memory_saver,
            llm_client=error_llm,
        )

    # START_BLOCK_ASSERT_CLEARED: [Override must be None after exception]
    assert n._LLM_CLIENT_OVERRIDE is None, (
        "nodes._LLM_CLIENT_OVERRIDE must be cleared to None even when start_session raises. "
        "The finally block must always execute."
    )
    # END_BLOCK_ASSERT_CLEARED

# END_FUNCTION_test_lll_client_override_cleared_on_exception
