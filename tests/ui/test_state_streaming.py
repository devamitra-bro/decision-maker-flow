# FILE: tests/ui/test_state_streaming.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: State Streaming Tests for the orchestrate_start / orchestrate_resume pipeline.
#          Constructs mocked graph streams, injects them via monkeypatch, and verifies that:
#          (a) 3 state updates → exactly 3 node_completed + 3 state_snapshot events.
#          (b) CoVe-rewrite scenario: cove_rewrite event emitted with correct text, BEFORE
#              the corresponding node_completed event (AC_UI_07, AC_UI_08).
#          All tests are headless (no Gradio launch).
# SCOPE: AC_UI_07 and AC_UI_08 coverage; mocked stream_session injection via monkeypatch.
# INPUT: Scripted async generator stubs replacing stream_session.
# OUTPUT: Assertions on collected UIEvent lists and render() pipeline output.
# KEYWORDS: [DOMAIN(8): TestInfra; CONCEPT(10): StreamModeUpdates; PATTERN(9): Monkeypatch;
#            PATTERN(8): HeadlessTestable; CONCEPT(9): ChainOfVerification; CONCEPT(10): AsyncIO]
# LINKS: [USES_API(10): src.ui.controllers.orchestrate_start;
#         USES_API(10): src.ui.controllers.orchestrate_resume;
#         USES_API(10): src.ui.presenter.render]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md AC_UI_07, AC_UI_08, §2 Flow C
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; three_updates test; cove_rewrite ordering test;
#              resume pipeline test.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# (Format: TYPE [Weight 1-10] [Entity description in English] => [entity_name_latin])
# TEST 8 [AC_UI_07 — mocked 3-update graph stream produces exactly 3 node_completed + 3 state_snapshot events] => test_three_updates_produce_three_uievents
# TEST 8 [AC_UI_08 — CoVe critique chunk emits cove_rewrite event BEFORE node_completed for 5_CoVe_Critique] => test_cove_rewrite_emits_dedicated_event
# TEST 7 [Full orchestrate_start + render() pipeline produces growing chat history and correct snapshot keys] => test_render_pipeline_produces_chatbot_updates
# TEST 7 [orchestrate_resume pipeline terminates with final_answer event containing correct answer text] => test_orchestrate_resume_produces_final_answer_event
# END_MODULE_MAP

import sys
from pathlib import Path
from typing import AsyncIterator

import pytest

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from src.ui.controllers import orchestrate_resume, orchestrate_start
from src.ui.presenter import render


# START_FUNCTION_test_three_updates_produce_three_uievents
# START_CONTRACT:
# PURPOSE: AC_UI_07 — mocked graph emitting 3 state updates produces exactly
#          3 node_completed + 3 state_snapshot events + 1 session_started + 1 awaiting_user.
# INPUTS: monkeypatch, caplog, ldd_capture fixtures.
# KEYWORDS: [PATTERN(9): Monkeypatch; CONCEPT(10): StreamModeUpdates; CONCEPT(8): EventCounting]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_three_updates_produce_three_uievents(monkeypatch, caplog, ldd_capture):
    """
    AC_UI_07: Verify that a mocked graph stream yielding 3 state updates produces
    exactly 3 node_completed UIEvents + 3 state_snapshot UIEvents, plus 1 session_started
    and 1 awaiting_user terminator event.

    Monkeypatches src.ui.controllers.stream_session with a fake async generator that
    yields 3 chunks and then the awaiting_user sentinel.
    """
    caplog.set_level("INFO")

    # START_BLOCK_FAKE_STREAM: [Define fake stream_session async generator]
    fake_chunks = [
        {"1_Context_Analyzer": {"dilemma": "buy or rent", "needs_data": False, "ready_for_weights": True}},
        {"2_Tool_Node": {"search_queries": ["mortgage rates 2026"], "tool_facts": []}},
        {"3_Weight_Questioner": {"last_question": "What is more important to you?"}},
    ]

    async def fake_stream_session(user_input, thread_id, checkpoint_path=None):
        for chunk in fake_chunks:
            yield chunk
        # Emit the awaiting_user sentinel that stream_session yields after loop
        yield {
            "__awaiting_user__": True,
            "last_question": "What is more important to you?",
            "thread_id": thread_id,
        }

    monkeypatch.setattr("src.ui.controllers.stream_session", fake_stream_session)
    # END_BLOCK_FAKE_STREAM

    # START_BLOCK_COLLECT_EVENTS: [Collect all UIEvents from orchestrate_start]
    events = []
    async for event in orchestrate_start("buy or rent", "test_thread_001"):
        events.append(event)
    # END_BLOCK_COLLECT_EVENTS

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 logs BEFORE asserts — Anti-Illusion]
    high_imp = ldd_capture()
    print(f"\n[AC_UI_07] Total events collected: {len(events)}")
    for ev in events:
        print(f"  event kind={ev.get('kind', 'UNKNOWN')} node={ev.get('node', '-')}")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_ASSERTIONS: [Verify event counts and kinds]
    node_completed_events = [e for e in events if e["kind"] == "node_completed"]
    state_snapshot_events = [e for e in events if e["kind"] == "state_snapshot"]
    session_started_events = [e for e in events if e["kind"] == "session_started"]
    awaiting_user_events = [e for e in events if e["kind"] == "awaiting_user"]

    assert len(node_completed_events) == 3, (
        f"AC_UI_07: Expected 3 node_completed events, got {len(node_completed_events)}"
    )
    assert len(state_snapshot_events) == 3, (
        f"AC_UI_07: Expected 3 state_snapshot events, got {len(state_snapshot_events)}"
    )
    assert len(session_started_events) == 1, (
        f"AC_UI_07: Expected 1 session_started event, got {len(session_started_events)}"
    )
    assert len(awaiting_user_events) == 1, (
        f"AC_UI_07: Expected 1 awaiting_user event, got {len(awaiting_user_events)}"
    )

    # Verify session_started is FIRST
    assert events[0]["kind"] == "session_started", (
        f"First event must be session_started, got: {events[0]['kind']!r}"
    )

    # Verify awaiting_user is LAST
    assert events[-1]["kind"] == "awaiting_user", (
        f"Last event must be awaiting_user, got: {events[-1]['kind']!r}"
    )

    # Verify the awaiting_user event has the correct question
    last_event = events[-1]
    assert last_event.get("question") == "What is more important to you?", (
        f"awaiting_user question mismatch: {last_event.get('question')!r}"
    )

    # Anti-Illusion: require at least one IMP:9 log from orchestrate_start
    found_belief_log = any("[IMP:9]" in msg and "orchestrate_start" in msg for msg in high_imp)
    assert found_belief_log, (
        "Anti-Illusion: No IMP:9 BeliefState log found from orchestrate_start. "
        "Telemetry must confirm algorithm trajectory."
    )
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_three_updates_produce_three_uievents


# START_FUNCTION_test_cove_rewrite_emits_dedicated_event
# START_CONTRACT:
# PURPOSE: AC_UI_08 — when mocked 5_CoVe_Critique returns decision="rewrite" with feedback,
#          a cove_rewrite UIEvent is emitted with exact critique_feedback text, BEFORE
#          the corresponding node_completed event.
# INPUTS: monkeypatch, caplog, ldd_capture fixtures.
# KEYWORDS: [PATTERN(9): Monkeypatch; CONCEPT(9): ChainOfVerification; CONCEPT(8): EventOrdering]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_cove_rewrite_emits_dedicated_event(monkeypatch, caplog, ldd_capture):
    """
    AC_UI_08: Verify cove_rewrite UIEvent is emitted with the correct critique_feedback text
    and appears BEFORE the node_completed event for 5_CoVe_Critique.

    Constructs a fake stream_session that yields a chunk from 5_CoVe_Critique with
    decision="rewrite" and a known critique_feedback string.
    """
    caplog.set_level("INFO")

    critique_text = "The draft lacks quantitative analysis of mortgage rates impact."

    # START_BLOCK_FAKE_STREAM: [Define fake stream including CoVe critique chunk]
    async def fake_stream_with_cove(user_input, thread_id, checkpoint_path=None):
        # Node 1 chunk
        yield {"1_Context_Analyzer": {"dilemma": "buy or rent", "ready_for_weights": True}}
        # Node 5 chunk with rewrite decision — this should trigger cove_rewrite BEFORE node_completed
        yield {
            "5_CoVe_Critique": {
                "decision": "rewrite",
                "critique_feedback": critique_text,
                "rewrite_count": 1,
            }
        }
        # Sentinel — normally this would come after more nodes; for this test we stop early
        yield {
            "__awaiting_user__": True,
            "last_question": "Follow-up question?",
            "thread_id": thread_id,
        }

    monkeypatch.setattr("src.ui.controllers.stream_session", fake_stream_with_cove)
    # END_BLOCK_FAKE_STREAM

    # START_BLOCK_COLLECT_EVENTS: [Collect all UIEvents]
    events = []
    async for event in orchestrate_start("buy or rent", "test_thread_002"):
        events.append(event)
    # END_BLOCK_COLLECT_EVENTS

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 logs BEFORE asserts]
    high_imp = ldd_capture()
    print(f"\n[AC_UI_08] Total events: {len(events)}")
    for ev in events:
        print(f"  kind={ev.get('kind', 'UNKNOWN')} node={ev.get('node', '-')}")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_ASSERTIONS: [Verify cove_rewrite event content and ordering]
    cove_events = [e for e in events if e["kind"] == "cove_rewrite"]
    assert len(cove_events) == 1, (
        f"AC_UI_08: Expected exactly 1 cove_rewrite event, got {len(cove_events)}"
    )

    cove_event = cove_events[0]
    assert cove_event.get("critique_feedback") == critique_text, (
        f"AC_UI_08: critique_feedback mismatch. "
        f"Expected {critique_text!r}, got {cove_event.get('critique_feedback')!r}"
    )

    # Verify ordering: cove_rewrite must appear BEFORE the node_completed for 5_CoVe_Critique
    cove_index = events.index(cove_event)
    cove_node_completed_events = [
        (i, e) for i, e in enumerate(events)
        if e["kind"] == "node_completed" and e.get("node") == "5_CoVe_Critique"
    ]
    assert len(cove_node_completed_events) >= 1, (
        "AC_UI_08: Expected at least 1 node_completed for 5_CoVe_Critique"
    )
    cove_completed_index = cove_node_completed_events[0][0]
    assert cove_index < cove_completed_index, (
        f"AC_UI_08: cove_rewrite (index {cove_index}) must appear BEFORE "
        f"node_completed for 5_CoVe_Critique (index {cove_completed_index})"
    )

    # Anti-Illusion
    found_belief_log = any("[IMP:8]" in msg and "cove_rewrite" in msg for msg in high_imp)
    assert found_belief_log, (
        "Anti-Illusion: No IMP:8 cove_rewrite detection log found. Telemetry must confirm CoVe path."
    )
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_cove_rewrite_emits_dedicated_event


# START_FUNCTION_test_render_pipeline_produces_chatbot_updates
@pytest.mark.asyncio
async def test_render_pipeline_produces_chatbot_updates(monkeypatch, caplog, ldd_capture):
    """
    Verify that the full orchestrate_start + render() pipeline with 3 mocked updates
    produces a progressively growing chat history and an updated state_snapshot.

    After applying render() to all events in sequence, the final chat_history must
    contain at least 1 assistant turn (the awaiting_user question), and state_snapshot
    must contain the 'dilemma' key from the first chunk's state_delta.
    """
    caplog.set_level("INFO")

    # START_BLOCK_FAKE_STREAM: [Define fake stream]
    async def fake_stream(user_input, thread_id, checkpoint_path=None):
        yield {"1_Context_Analyzer": {"dilemma": "buy or rent", "ready_for_weights": True}}
        yield {"2_Tool_Node": {"search_queries": ["mortgage 2026"], "tool_facts": []}}
        yield {"3_Weight_Questioner": {"last_question": "What are your priorities?"}}
        yield {
            "__awaiting_user__": True,
            "last_question": "What are your priorities?",
            "thread_id": thread_id,
        }

    monkeypatch.setattr("src.ui.controllers.stream_session", fake_stream)
    # END_BLOCK_FAKE_STREAM

    # START_BLOCK_PIPELINE_TEST: [Collect events and apply render() sequentially]
    events = []
    async for event in orchestrate_start("buy or rent", "test_thread_003"):
        events.append(event)

    # Apply render() sequentially to simulate what on_submit does
    chat_history = []
    state_snapshot = {}
    for event in events:
        chat_history, state_snapshot, status = render(event, chat_history, state_snapshot)
    # END_BLOCK_PIPELINE_TEST

    # START_BLOCK_LDD_TELEMETRY: [Print trajectory before asserts]
    high_imp = ldd_capture()
    print(f"\n[render_pipeline] Final chat_history length={len(chat_history)}")
    print(f"[render_pipeline] Final state_snapshot keys={list(state_snapshot.keys())}")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_ASSERTIONS: [Verify final state of pipeline output]
    assistant_messages = [m for m in chat_history if m.get("role") == "assistant"]
    assert len(assistant_messages) >= 1, (
        "Pipeline must produce at least 1 assistant message (awaiting_user question)"
    )

    # The awaiting_user question must appear in the last assistant message
    question_text = "What are your priorities?"
    last_assistant = assistant_messages[-1]
    assert question_text in last_assistant["content"], (
        f"awaiting_user question must be in last assistant message: {last_assistant['content']!r}"
    )

    # state_snapshot must contain 'dilemma' from chunk 1 (via render node_completed)
    assert "dilemma" in state_snapshot, (
        f"state_snapshot must contain 'dilemma' after render pipeline. Keys: {list(state_snapshot.keys())}"
    )
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_render_pipeline_produces_chatbot_updates


# START_FUNCTION_test_orchestrate_resume_produces_final_answer_event
@pytest.mark.asyncio
async def test_orchestrate_resume_produces_final_answer_event(monkeypatch, caplog, ldd_capture):
    """
    Verify that orchestrate_resume + mocked stream_resume_session produces a final_answer
    event as the last event, with the correct final_answer text.
    """
    caplog.set_level("INFO")

    expected_answer = "## Decision Analysis\n\nRenting is recommended based on your priorities."

    # START_BLOCK_FAKE_RESUME_STREAM: [Define fake stream_resume_session]
    async def fake_resume_stream(user_answer, thread_id, checkpoint_path=None):
        yield {"3.5_Weight_Parser": {"weights": {"cost": 7, "flexibility": 8}}}
        yield {"4_Draft_Generator": {"draft": "Initial draft analysis..."}}
        yield {"5_CoVe_Critique": {"decision": "finalize", "critique_feedback": ""}}
        yield {"6_Final_Synthesizer": {"final_answer": expected_answer}}
        yield {
            "__done__": True,
            "final_answer": expected_answer,
            "thread_id": thread_id,
        }

    monkeypatch.setattr("src.ui.controllers.stream_resume_session", fake_resume_stream)
    # END_BLOCK_FAKE_RESUME_STREAM

    # START_BLOCK_COLLECT_EVENTS: [Collect all UIEvents from orchestrate_resume]
    events = []
    async for event in orchestrate_resume("cost: 7, flexibility: 8", "test_thread_004"):
        events.append(event)
    # END_BLOCK_COLLECT_EVENTS

    # START_BLOCK_LDD_TELEMETRY: [Print trajectory before asserts]
    high_imp = ldd_capture()
    print(f"\n[orchestrate_resume] Total events: {len(events)}")
    for ev in events:
        print(f"  kind={ev.get('kind', 'UNKNOWN')} node={ev.get('node', '-')}")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_ASSERTIONS: [Verify resume event sequence]
    assert events[0]["kind"] == "resume_started", (
        f"First event must be resume_started, got: {events[0]['kind']!r}"
    )
    assert events[-1]["kind"] == "final_answer", (
        f"Last event must be final_answer, got: {events[-1]['kind']!r}"
    )
    assert events[-1].get("final_answer") == expected_answer, (
        f"final_answer text mismatch: {events[-1].get('final_answer')!r}"
    )

    # Verify no cove_rewrite event (critique was "finalize")
    cove_events = [e for e in events if e["kind"] == "cove_rewrite"]
    assert len(cove_events) == 0, (
        f"No cove_rewrite expected when decision='finalize', got {len(cove_events)}"
    )

    # Anti-Illusion
    found_belief = any("[IMP:9]" in msg and "orchestrate_resume" in msg for msg in high_imp)
    assert found_belief, (
        "Anti-Illusion: No IMP:9 BeliefState log found from orchestrate_resume."
    )
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_orchestrate_resume_produces_final_answer_event
