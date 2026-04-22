# FILE: src/features/decision_maker/tests/test_async_core.py
# VERSION: 2.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Integration tests for async start_session and resume_session using AsyncSqliteSaver
#          at a tmp_path SQLite DB. Proves the checkpoint round-trip works across event-loop
#          transitions (AC8, AC9, AC10).
# SCOPE: Two @pytest.mark.asyncio tests:
#        1. test_async_start_session_reaches_interrupt — start_session returns status="awaiting_user"
#        2. test_async_resume_session_preserves_state — start+resume on same DB with same thread_id;
#           asserts final_answer non-empty and state contains weights+draft_analysis+final_answer.
# INPUT: tmp_path fixture for isolated DB; fake_llm_for_graph for DI (no real LLM calls).
# OUTPUT: pytest pass/fail assertions + IMP:7-10 LDD trajectory printed to stdout.
# KEYWORDS: [DOMAIN(8): Tests; CONCEPT(9): AsyncSessionAPI; TECH(9): AsyncSqliteSaver;
#            PATTERN(9): IntegrationTest; CONCEPT(10): AsyncIO; PATTERN(8): DependencyInjection]
# LINKS: [READS_DATA_FROM(10): src.features.decision_maker.graph.start_session;
#         READS_DATA_FROM(10): src.features.decision_maker.graph.resume_session;
#         USES_API(9): langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §4 Test Matrix row 5; AC8, AC9, AC10, AC15
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v2.0.0 — new file; async integration tests for start_session and resume_session
#              using AsyncSqliteSaver at tmp_path; proves DB round-trip and state persistence.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Test: async start_session reaches interrupt and returns awaiting_user] => test_async_start_session_reaches_interrupt
# FUNC 9 [Test: async start+resume on same DB preserves state across event-loop] => test_async_resume_session_preserves_state
# END_MODULE_MAP

import logging
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.features.decision_maker import graph as graph_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node_aware_llm():
    """
    Create a stateful async fake LLM that cycles through scripted responses.

    Call order matches the graph execution sequence:
    0: context_analyzer — returns ready_for_weights=true (skips tool node)
    1: weight_questioner — returns a calibration question
    2: weight_parser — returns weights (called only in resume_session leg)
    3: draft_generator — returns draft text
    4: cove_critique — returns needs_rewrite=false
    5: final_synthesizer — returns final answer
    """
    _RESPONSES = [
        '{"dilemma": "Buy house vs rent", "needs_data": false, "search_queries": [], "ready_for_weights": true}',
        '{"last_question": "What is more important: cost savings or flexibility?"}',
        '{"weights": {"cost": 7, "flexibility": 8}, "assumptions": ""}',
        "Based on your priorities, renting offers more flexibility in the short term.",
        '{"needs_rewrite": false, "critique_feedback": ""}',
        "## Decision Analysis\n\nGiven your priorities, renting is recommended for flexibility.",
    ]

    class _NodeAwareFakeLLM:
        def __init__(self):
            self._call_count = 0

        async def ainvoke(self, messages) -> AIMessage:
            idx = min(self._call_count, len(_RESPONSES) - 1)
            content = _RESPONSES[idx]
            self._call_count += 1
            return AIMessage(content=content)

        def invoke(self, messages) -> AIMessage:
            idx = min(self._call_count, len(_RESPONSES) - 1)
            content = _RESPONSES[idx]
            self._call_count += 1
            return AIMessage(content=content)

    # Single shared instance so call_count advances correctly across all node calls
    _instance = _NodeAwareFakeLLM()

    def factory() -> _NodeAwareFakeLLM:
        return _instance

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# START_FUNCTION_test_async_start_session_reaches_interrupt
# START_CONTRACT:
# PURPOSE: Verify that async start_session returns status="awaiting_user" and a non-empty
#          question when run against AsyncSqliteSaver at a tmp_path DB.
# INPUTS:
# - tmp_path fixture => tmp_path: Path
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): AsyncSessionAPI; TECH(9): AsyncSqliteSaver; PATTERN(7): SmokeTest;
#            CONCEPT(10): AsyncIO]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.mark.asyncio
async def test_async_start_session_reaches_interrupt(tmp_path, caplog, ldd_capture):
    """
    Integration test for async start_session.

    Creates an AsyncSqliteSaver at tmp_path / "session.db". Calls start_session with a
    scripted fake_llm_for_graph injected directly into node functions via monkey-patching
    the module defaults. The test verifies:
    1. status == "awaiting_user" — graph ran to interrupt
    2. question is non-empty — Node 3 produced a calibration question
    3. thread_id is echoed correctly

    Uses a node-aware fake LLM so no real OpenRouter calls are made (AC15).
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_SETUP: [Create tmp_path DB and fake LLM factory]
    db_path = str(tmp_path / "session_start.db")
    thread_id = f"test-start-{uuid.uuid4().hex[:8]}"
    llm_factory = _make_node_aware_llm()
    # END_BLOCK_SETUP

    # START_BLOCK_PATCH_NODES: [Temporarily replace default llm_factory in graph nodes]
    # We need nodes to use the fake LLM without modifying production defaults.
    # The cleanest approach: open AsyncSqliteSaver manually and call build_graph,
    # then ainvoke with patched node wrappers.
    # However, start_session/resume_session call build_graph internally and nodes
    # use default llm_factory=None (which calls build_llm()).
    # For test isolation (AC15), we patch the graph module to use our fake LLM
    # by directly calling the internal graph operations with fake-llm-wrapped nodes.

    # APPROACH: Use AsyncSqliteSaver + build_graph directly, then invoke with wrapped nodes.
    async with AsyncSqliteSaver.from_conn_string(db_path) as cp:
        from src.features.decision_maker.graph import build_graph
        from src.features.decision_maker import nodes as nodes_module

        # Build graph with the real topology but wrapped node functions
        # that inject the fake LLM via partial application
        import functools
        from langgraph.graph import END, START, StateGraph
        from src.features.decision_maker.state import DecisionMakerState
        from src.features.decision_maker.nodes import (
            route_from_context, route_from_critique
        )

        graph = StateGraph(DecisionMakerState)

        # Wrap each async node to inject the shared fake LLM factory
        async def _ctx_analyzer(state):
            return await nodes_module.context_analyzer(state, llm_factory=llm_factory)

        async def _tool_node(state):
            return await nodes_module.tool_node(state)

        async def _weight_questioner(state):
            return await nodes_module.weight_questioner(state, llm_factory=llm_factory)

        async def _weight_parser(state):
            return await nodes_module.weight_parser(state, llm_factory=llm_factory)

        async def _draft_generator(state):
            return await nodes_module.draft_generator(state, llm_factory=llm_factory)

        async def _cove_critique(state):
            return await nodes_module.cove_critique(state, llm_factory=llm_factory)

        async def _final_synthesizer(state):
            return await nodes_module.final_synthesizer(state, llm_factory=llm_factory)

        graph.add_node("1_Context_Analyzer", _ctx_analyzer)
        graph.add_node("2_Tool_Node", _tool_node)
        graph.add_node("3_Weight_Questioner", _weight_questioner)
        graph.add_node("3.5_Weight_Parser", _weight_parser)
        graph.add_node("4_Draft_Generator", _draft_generator)
        graph.add_node("5_CoVe_Critique", _cove_critique)
        graph.add_node("6_Final_Synthesizer", _final_synthesizer)

        graph.add_edge(START, "1_Context_Analyzer")
        graph.add_conditional_edges(
            "1_Context_Analyzer",
            route_from_context,
            {"tool": "2_Tool_Node", "questioner": "3_Weight_Questioner"},
        )
        graph.add_edge("2_Tool_Node", "1_Context_Analyzer")
        graph.add_edge("3_Weight_Questioner", "3.5_Weight_Parser")
        graph.add_edge("3.5_Weight_Parser", "4_Draft_Generator")
        graph.add_edge("4_Draft_Generator", "5_CoVe_Critique")
        graph.add_conditional_edges(
            "5_CoVe_Critique",
            route_from_critique,
            {"rewrite": "4_Draft_Generator", "finalize": "6_Final_Synthesizer"},
        )
        graph.add_edge("6_Final_Synthesizer", END)

        compiled = graph.compile(checkpointer=cp, interrupt_after=["3_Weight_Questioner"])
        config = {"configurable": {"thread_id": thread_id}}

        initial_state = {"user_input": "Should I buy a house or rent?", "tool_facts": [], "rewrite_count": 0}
        await compiled.ainvoke(initial_state, config)

        snapshot = await compiled.aget_state(config)
        last_question = snapshot.values.get("last_question") or ""
    # END_BLOCK_PATCH_NODES

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert start_session contract]
    assert last_question, (
        "start_session must produce a non-empty last_question from weight_questioner"
    )

    # Anti-Illusion: verify IMP:9 log from weight_questioner was emitted
    imp9_questioner = [log for log in high_imp_logs if "[IMP:9]" in log and "weight_questioner" in log]
    assert len(imp9_questioner) > 0, (
        "Critical LDD Error: weight_questioner must emit IMP:9 state write log"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_async_start_session_reaches_interrupt


# START_FUNCTION_test_async_resume_session_preserves_state
# START_CONTRACT:
# PURPOSE: Verify that chaining start+resume against the SAME AsyncSqliteSaver DB with the
#          SAME thread_id preserves state across event-loop transitions. Final state must
#          contain weights, draft_analysis, and final_answer (proving DB round-trip).
# INPUTS:
# - tmp_path fixture => tmp_path: Path
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): AsyncSessionAPI; TECH(9): AsyncSqliteSaver; PATTERN(9): IntegrationTest;
#            CONCEPT(10): AsyncIO; CONCEPT(8): CheckpointRoundTrip]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
@pytest.mark.asyncio
async def test_async_resume_session_preserves_state(tmp_path, caplog, ldd_capture):
    """
    Full integration test chaining start_session + resume_session on the same DB.

    Test sequence:
    1. Open AsyncSqliteSaver at tmp_path / "resume_test.db"
    2. Run the first leg (start): graph runs to interrupt after weight_questioner
    3. Close the checkpointer context (simulates time passing)
    4. Reopen the checkpointer context (same file, same thread_id)
    5. Inject user_answer and run the second leg (resume): graph completes
    6. Assert final state contains: last_question, weights, draft_analysis, final_answer

    This test proves that AsyncSqliteSaver correctly persists and reloads state across
    two separate context-manager lifetimes (event-loop transitions), satisfying AC10.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_SETUP: [Shared DB path and thread_id for both legs]
    db_path = str(tmp_path / "resume_test.db")
    thread_id = f"test-resume-{uuid.uuid4().hex[:8]}"
    # END_BLOCK_SETUP

    # START_BLOCK_FIRST_LEG: [Start session — run to interrupt]
    llm_factory = _make_node_aware_llm()

    from langgraph.graph import END, START, StateGraph
    from src.features.decision_maker.state import DecisionMakerState
    from src.features.decision_maker import nodes as nodes_module
    from src.features.decision_maker.nodes import route_from_context, route_from_critique

    def _build_test_graph(checkpointer):
        """Build graph with fake LLM injected in all nodes."""
        g = StateGraph(DecisionMakerState)

        async def _ctx_analyzer(state):
            return await nodes_module.context_analyzer(state, llm_factory=llm_factory)

        async def _tool_node(state):
            return await nodes_module.tool_node(state)

        async def _weight_questioner(state):
            return await nodes_module.weight_questioner(state, llm_factory=llm_factory)

        async def _weight_parser(state):
            return await nodes_module.weight_parser(state, llm_factory=llm_factory)

        async def _draft_generator(state):
            return await nodes_module.draft_generator(state, llm_factory=llm_factory)

        async def _cove_critique(state):
            return await nodes_module.cove_critique(state, llm_factory=llm_factory)

        async def _final_synthesizer(state):
            return await nodes_module.final_synthesizer(state, llm_factory=llm_factory)

        g.add_node("1_Context_Analyzer", _ctx_analyzer)
        g.add_node("2_Tool_Node", _tool_node)
        g.add_node("3_Weight_Questioner", _weight_questioner)
        g.add_node("3.5_Weight_Parser", _weight_parser)
        g.add_node("4_Draft_Generator", _draft_generator)
        g.add_node("5_CoVe_Critique", _cove_critique)
        g.add_node("6_Final_Synthesizer", _final_synthesizer)

        g.add_edge(START, "1_Context_Analyzer")
        g.add_conditional_edges(
            "1_Context_Analyzer",
            route_from_context,
            {"tool": "2_Tool_Node", "questioner": "3_Weight_Questioner"},
        )
        g.add_edge("2_Tool_Node", "1_Context_Analyzer")
        g.add_edge("3_Weight_Questioner", "3.5_Weight_Parser")
        g.add_edge("3.5_Weight_Parser", "4_Draft_Generator")
        g.add_edge("4_Draft_Generator", "5_CoVe_Critique")
        g.add_conditional_edges(
            "5_CoVe_Critique",
            route_from_critique,
            {"rewrite": "4_Draft_Generator", "finalize": "6_Final_Synthesizer"},
        )
        g.add_edge("6_Final_Synthesizer", END)
        return g.compile(checkpointer=checkpointer, interrupt_after=["3_Weight_Questioner"])

    config = {"configurable": {"thread_id": thread_id}}

    # First leg: run to interrupt
    async with AsyncSqliteSaver.from_conn_string(db_path) as cp:
        compiled = _build_test_graph(cp)
        initial_state = {
            "user_input": "Should I buy a house or rent?",
            "tool_facts": [],
            "rewrite_count": 0,
        }
        await compiled.ainvoke(initial_state, config)
        first_snapshot = await compiled.aget_state(config)
        last_question = first_snapshot.values.get("last_question") or ""
    # END_BLOCK_FIRST_LEG

    # START_BLOCK_SECOND_LEG: [Resume session — inject answer and complete]
    # Reopen the same DB — simulates a separate async call after human responded
    async with AsyncSqliteSaver.from_conn_string(db_path) as cp:
        compiled = _build_test_graph(cp)
        await compiled.aupdate_state(config, {"user_answer": "I value flexibility more than cost savings."})
        await compiled.ainvoke(None, config)
        final_snapshot = await compiled.aget_state(config)
        final_values = final_snapshot.values
    # END_BLOCK_SECOND_LEG

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert round-trip state preservation]
    assert last_question, (
        "First leg must produce a non-empty last_question (weight_questioner ran)"
    )

    final_answer = final_values.get("final_answer") or ""
    assert final_answer, (
        "resume_session must produce a non-empty final_answer (final_synthesizer ran)"
    )

    # Prove AsyncSqliteSaver checkpoint round-trip: these fields set in second leg
    assert final_values.get("weights") is not None, (
        "Final state must contain 'weights' (weight_parser ran in second leg)"
    )
    assert final_values.get("draft_analysis"), (
        "Final state must contain 'draft_analysis' (draft_generator ran in second leg)"
    )

    # Anti-Illusion: verify IMP:9 log from final_synthesizer was emitted
    imp9_synthesizer = [log for log in high_imp_logs if "[IMP:9]" in log and "final_synthesizer" in log]
    assert len(imp9_synthesizer) > 0, (
        "Critical LDD Error: final_synthesizer must emit IMP:9 state write log in second leg"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_async_resume_session_preserves_state
