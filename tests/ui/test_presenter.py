# FILE: tests/ui/test_presenter.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Table-driven pure-function tests for presenter.render() covering all 9 UIEvent kinds.
#          Verifies that render() is a pure function with correct return tuple shape and content.
#          No async, no gradio import, no side effects.
# SCOPE: Tests for render(), _filter_state_for_display(), _format_status() in src/ui/presenter.py.
# INPUT: Scripted UIEvent dicts and initial (chat_history, state_snapshot) pairs.
# OUTPUT: Assertions on returned (list, dict, str) tuples.
# KEYWORDS: [DOMAIN(8): TestInfra; PATTERN(9): TableDriven; CONCEPT(8): PureFunction;
#            PATTERN(8): HeadlessTestable; CONCEPT(7): ZeroContextSurvival]
# LINKS: [USES_API(10): src.ui.presenter.render; USES_API(7): src.ui.presenter._filter_state_for_display]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md AC_UI_10, AC_UI_05
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; 9 event kind tests + filter helper tests + purity test.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# (Format: TYPE [Weight 1-10] [Entity description in English] => [entity_name_latin])
# TEST 7 [Verifies render() returns a 3-tuple (list, dict, str) for all 9 event kinds] => test_render_returns_correct_tuple_shape
# TEST 6 [Verifies session_started event resets state_snapshot to empty dict] => test_session_started_resets_snapshot
# TEST 6 [Verifies awaiting_user event appends assistant turn with question text] => test_awaiting_user_appends_question
# TEST 6 [Verifies cove_rewrite event appends assistant message with critique_feedback] => test_cove_rewrite_appends_critique_to_chat
# TEST 6 [Verifies final_answer event appends answer as assistant message] => test_final_answer_appends_answer
# TEST 7 [Verifies node_completed merges filtered state_delta into snapshot] => test_node_completed_merges_delta_into_snapshot
# TEST 8 [Verifies render() does not mutate input chat_history or state_snapshot — purity test] => test_render_does_not_mutate_inputs
# TEST 6 [Verifies _filter_state_for_display truncates strings >500 chars with ellipsis] => test_filter_state_truncates_long_strings
# TEST 5 [Verifies unknown event kind passes through without crash or mutation] => test_unknown_event_kind_passes_through
# END_MODULE_MAP

import sys
from pathlib import Path

# Ensure brainstorm root on sys.path (conftest also does this, but redundant for clarity)
_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from src.ui.presenter import _filter_state_for_display, _format_status, render


# START_FUNCTION_test_render_returns_correct_tuple_shape
# START_CONTRACT:
# PURPOSE: Verify render() ALWAYS returns a 3-tuple of (list, dict, str) for all 9 event kinds.
# INPUTS: caplog, ldd_capture fixtures.
# KEYWORDS: [PATTERN(9): TableDriven; CONCEPT(8): Invariant]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_render_returns_correct_tuple_shape(caplog, ldd_capture):
    """
    Table-driven test verifying that render() returns a 3-tuple (list, dict, str) for
    all 9 UIEvent kinds. Each row in the test matrix is an independent input scenario.
    """
    caplog.set_level("INFO")

    # START_BLOCK_TEST_MATRIX: [Define test cases for all 9 event kinds]
    test_cases = [
        # (event_dict, initial_chat, initial_snapshot, description)
        (
            {"kind": "session_started", "thread_id": "tid1"},
            [],
            {"old_key": "old_value"},
            "session_started resets snapshot to {}",
        ),
        (
            {"kind": "node_started", "node": "1_Context_Analyzer", "thread_id": "tid1"},
            [{"role": "user", "content": "dilemma"}],
            {},
            "node_started — no chat change",
        ),
        (
            {
                "kind": "node_completed",
                "node": "1_Context_Analyzer",
                "state_delta": {"dilemma": "buy or rent", "needs_data": False},
                "thread_id": "tid1",
            },
            [{"role": "user", "content": "dilemma"}],
            {},
            "node_completed merges delta into snapshot",
        ),
        (
            {
                "kind": "state_snapshot",
                "node": "2_Tool_Node",
                "state_snapshot": {"search_queries": ["query1"], "tool_facts": []},
                "thread_id": "tid1",
            },
            [],
            {"dilemma": "buy or rent"},
            "state_snapshot updates X-Ray",
        ),
        (
            {
                "kind": "cove_rewrite",
                "critique_feedback": "Missing financial context",
                "thread_id": "tid1",
            },
            [{"role": "user", "content": "dilemma"}],
            {"rewrite_count": 1},
            "cove_rewrite appends assistant message with critique",
        ),
        (
            {
                "kind": "awaiting_user",
                "question": "What are your priorities?",
                "thread_id": "tid1",
            },
            [{"role": "user", "content": "dilemma"}],
            {},
            "awaiting_user appends assistant turn with question",
        ),
        (
            {"kind": "resume_started", "thread_id": "tid1"},
            [],
            {},
            "resume_started — no chat change",
        ),
        (
            {
                "kind": "final_answer",
                "final_answer": "## Recommendation\n\nRent is better.",
                "thread_id": "tid1",
            },
            [{"role": "user", "content": "dilemma"}],
            {},
            "final_answer appends assistant turn with answer",
        ),
        (
            {
                "kind": "error",
                "error_message": "GraphRuntime error: timeout",
                "thread_id": "tid1",
            },
            [],
            {},
            "error appends assistant error message",
        ),
    ]
    # END_BLOCK_TEST_MATRIX

    # START_BLOCK_RUN_ALL_CASES: [Execute table-driven test loop]
    for event, initial_chat, initial_snapshot, description in test_cases:
        new_chat, new_snapshot, status_text = render(event, initial_chat, initial_snapshot)

        # Shape assertions — the core invariant
        assert isinstance(new_chat, list), f"[{description}] Expected list, got {type(new_chat)}"
        assert isinstance(new_snapshot, dict), f"[{description}] Expected dict, got {type(new_snapshot)}"
        assert isinstance(status_text, str), f"[{description}] Expected str, got {type(status_text)}"
        assert len(status_text) > 0, f"[{description}] Status text must be non-empty"
    # END_BLOCK_RUN_ALL_CASES

    # START_BLOCK_LDD_TELEMETRY: [Print LDD trajectory for Anti-Illusion check]
    high_imp = ldd_capture()
    print(f"\n[test_render_shape] All 9 event kind shapes validated. high_imp_count={len(high_imp)}")
    # END_BLOCK_LDD_TELEMETRY
# END_FUNCTION_test_render_returns_correct_tuple_shape


# START_FUNCTION_test_session_started_resets_snapshot
def test_session_started_resets_snapshot():
    """
    Verify session_started event resets the state_snapshot to an empty dict,
    regardless of what was in the snapshot before.
    """
    event = {"kind": "session_started", "thread_id": "tid_reset"}
    initial_snapshot = {"dilemma": "old", "weights": {"cost": 5}, "rewrite_count": 2}
    initial_chat = [{"role": "user", "content": "old dilemma"}]

    new_chat, new_snapshot, status = render(event, initial_chat, initial_snapshot)

    # START_BLOCK_ASSERTIONS: [Verify reset behaviour]
    assert new_snapshot == {}, f"session_started must reset snapshot, got: {new_snapshot}"
    assert new_chat == initial_chat, "session_started must NOT change chat_history"
    assert "Сессия" in status or "запущена" in status, f"Status must indicate session start: {status!r}"
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_session_started_resets_snapshot


# START_FUNCTION_test_awaiting_user_appends_question
def test_awaiting_user_appends_question():
    """
    Verify awaiting_user event appends an assistant turn with the question text.
    The role must be "assistant" and the content must contain the question string.
    """
    question = "What is more important to you — financial security or flexibility?"
    event = {"kind": "awaiting_user", "question": question, "thread_id": "tid_aw"}
    initial_chat = [{"role": "user", "content": "buy or rent?"}]

    new_chat, new_snapshot, status = render(event, initial_chat, {})

    # START_BLOCK_ASSERTIONS: [Verify question appended as assistant turn]
    assert len(new_chat) == 2, f"Expected 2 messages after awaiting_user, got {len(new_chat)}"
    last_msg = new_chat[-1]
    assert last_msg["role"] == "assistant", f"Last message must be assistant, got: {last_msg['role']!r}"
    assert question in last_msg["content"], f"Question must be in content: {last_msg['content']!r}"
    assert "ответ" in status.lower() or "🤔" in status, f"Status must indicate waiting: {status!r}"
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_awaiting_user_appends_question


# START_FUNCTION_test_cove_rewrite_appends_before_node_events
def test_cove_rewrite_appends_critique_to_chat():
    """
    Verify cove_rewrite event appends an assistant message containing the critique_feedback.
    The content must include the CoVe prefix and the critique text.
    """
    feedback = "The analysis misses key economic factors from 2026."
    event = {"kind": "cove_rewrite", "critique_feedback": feedback, "thread_id": "tid_cove"}
    initial_chat = [{"role": "user", "content": "dilemma"}]

    new_chat, new_snapshot, status = render(event, initial_chat, {"rewrite_count": 1})

    # START_BLOCK_ASSERTIONS: [Verify critique appended with correct prefix]
    assert len(new_chat) == 2, f"Expected 2 messages after cove_rewrite, got {len(new_chat)}"
    last_msg = new_chat[-1]
    assert last_msg["role"] == "assistant"
    assert feedback in last_msg["content"], f"Feedback must be in content: {last_msg['content']!r}"
    assert "⚠️" in last_msg["content"] or "CoVe" in last_msg["content"], (
        f"Content must have CoVe prefix: {last_msg['content']!r}"
    )
    assert "🚨" in status or "Критик" in status, f"Status must indicate CoVe: {status!r}"
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_cove_rewrite_appends_critique_to_chat


# START_FUNCTION_test_final_answer_appends_answer
def test_final_answer_appends_answer():
    """
    Verify final_answer event appends the answer as an assistant message and
    sets status to indicate completion.
    """
    answer = "## Decision\n\nBased on your priorities, renting is recommended."
    event = {"kind": "final_answer", "final_answer": answer, "thread_id": "tid_fa"}
    initial_chat = [{"role": "user", "content": "dilemma"}, {"role": "assistant", "content": "question"}]

    new_chat, new_snapshot, status = render(event, initial_chat, {})

    # START_BLOCK_ASSERTIONS: [Verify final answer appended]
    assert len(new_chat) == 3, f"Expected 3 messages after final_answer, got {len(new_chat)}"
    last_msg = new_chat[-1]
    assert last_msg["role"] == "assistant"
    assert answer in last_msg["content"]
    assert "✅" in status or "Готово" in status, f"Status must indicate done: {status!r}"
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_final_answer_appends_answer


# START_FUNCTION_test_node_completed_merges_delta
def test_node_completed_merges_delta_into_snapshot():
    """
    Verify node_completed event merges the state_delta (filtered) into the state_snapshot.
    Fields in the display allowlist must appear in new_snapshot; internal fields must be dropped.
    """
    state_delta = {
        "dilemma": "buy or rent",
        "user_input": "INTERNAL FIELD - should be dropped",
        "search_queries": ["query1", "query2"],
        "rewrite_count": 0,
    }
    event = {
        "kind": "node_completed",
        "node": "1_Context_Analyzer",
        "state_delta": state_delta,
        "thread_id": "tid_nc",
    }

    new_chat, new_snapshot, status = render(event, [], {})

    # START_BLOCK_ASSERTIONS: [Verify delta merge and field filtering]
    assert "dilemma" in new_snapshot, "dilemma must be in filtered snapshot"
    assert "search_queries" in new_snapshot, "search_queries must be in filtered snapshot"
    assert "rewrite_count" in new_snapshot, "rewrite_count must be in filtered snapshot"
    assert "user_input" not in new_snapshot, "user_input is internal — must be filtered out"
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_node_completed_merges_delta


# START_FUNCTION_test_render_does_not_mutate_inputs
def test_render_does_not_mutate_inputs():
    """
    Purity test: render() must NOT mutate the input chat_history or state_snapshot.
    The originals must be unchanged after the call.
    """
    original_chat = [{"role": "user", "content": "dilemma"}]
    original_snapshot = {"dilemma": "buy or rent"}

    # Take copies to compare after
    chat_before = list(original_chat)
    snapshot_before = dict(original_snapshot)

    event = {"kind": "final_answer", "final_answer": "## Answer", "thread_id": "tid_purity"}
    render(event, original_chat, original_snapshot)

    # START_BLOCK_ASSERTIONS: [Verify originals unchanged]
    assert original_chat == chat_before, "render() must not mutate chat_history input"
    assert original_snapshot == snapshot_before, "render() must not mutate state_snapshot input"
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_render_does_not_mutate_inputs


# START_FUNCTION_test_filter_state_truncates_long_strings
def test_filter_state_truncates_long_strings():
    """
    Verify _filter_state_for_display truncates string values longer than 500 characters
    with a trailing '...' and keeps short values intact.
    """
    long_string = "X" * 600
    raw_state = {
        "dilemma": long_string,
        "search_queries": ["short query", "Y" * 600],
        "user_input": "should be dropped",
    }

    filtered = _filter_state_for_display(raw_state)

    # START_BLOCK_ASSERTIONS: [Verify truncation and filtering]
    assert "dilemma" in filtered
    assert len(filtered["dilemma"]) <= 503, f"Truncated string must be <= 503 chars: {len(filtered['dilemma'])}"
    assert filtered["dilemma"].endswith("..."), "Truncated string must end with '...'"
    assert "user_input" not in filtered, "user_input is not in allowlist, must be excluded"
    assert "search_queries" in filtered
    assert filtered["search_queries"][1].endswith("..."), "Long list items must also be truncated"
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_filter_state_truncates_long_strings


# START_FUNCTION_test_unknown_event_kind_passes_through
def test_unknown_event_kind_passes_through():
    """
    Verify that render() with an unknown event kind returns the input unchanged
    (defensive fallback — no crash, no mutation).
    """
    event = {"kind": "totally_unknown_kind_xyz", "thread_id": "tid_unk"}
    initial_chat = [{"role": "user", "content": "test"}]
    initial_snapshot = {"dilemma": "test"}

    new_chat, new_snapshot, status = render(event, initial_chat, initial_snapshot)

    # START_BLOCK_ASSERTIONS: [Verify pass-through behaviour]
    assert isinstance(new_chat, list)
    assert isinstance(new_snapshot, dict)
    assert isinstance(status, str)
    # Chat and snapshot should be unchanged for unknown kind
    assert new_chat == initial_chat or new_chat == list(initial_chat)
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_unknown_event_kind_passes_through
