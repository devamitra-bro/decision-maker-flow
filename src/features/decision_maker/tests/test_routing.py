# FILE: src/features/decision_maker/tests/test_routing.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Test suite for route_from_context conditional router (AC7).
# SCOPE: Verifies mutual exclusion guard (DoubleTrueError), "tool" routing, "questioner" routing.
# INPUT: Synthetic state dicts injected directly without LLM calls.
# OUTPUT: pytest pass/fail assertions + IMP:7-10 LDD trajectory printed to stdout.
# KEYWORDS: [DOMAIN(8): Tests; CONCEPT(9): RoutingLogic; PATTERN(7): AtomicTest; PATTERN(9): AntiLoop]
# LINKS: [READS_DATA_FROM(9): src.features.decision_maker.nodes.route_from_context;
#         READS_DATA_FROM(8): src.features.decision_maker.nodes.DoubleTrueError]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §4 Test Matrix row 1; AC7
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial implementation; 3 atomic tests + LDD telemetry output.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Test: double-True raises DoubleTrueError at IMP:10] => test_route_double_true_raises
# FUNC 8 [Test: needs_data=True routes to "tool"] => test_route_needs_data_returns_tool
# FUNC 8 [Test: ready_for_weights=True routes to "questioner"] => test_route_ready_returns_questioner
# END_MODULE_MAP

import logging
import pytest

from src.features.decision_maker.nodes import DoubleTrueError, route_from_context


# START_FUNCTION_test_route_double_true_raises
# START_CONTRACT:
# PURPOSE: Verify that route_from_context raises DoubleTrueError when both
#          _needs_data=True and _ready_for_weights=True are set in state.
# INPUTS:
# - caplog fixture => caplog: pytest.LogCaptureFixture
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): MutualExclusion; PATTERN(7): ExceptionAssertion]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_route_double_true_raises(caplog, ldd_capture):
    """
    When both needs_data and ready_for_weights are True simultaneously,
    route_from_context must raise DoubleTrueError. This state violates the
    mutual exclusion invariant in the Context Analyzer prompt.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Call router with double-True state]
    state = {
        "_needs_data": True,
        "_ready_for_weights": True,
        "is_data_sufficient": False,
        "search_queries": ["some query"],
    }

    with pytest.raises(DoubleTrueError) as exc_info:
        route_from_context(state)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert DoubleTrueError was raised with correct context]
    assert isinstance(exc_info.value, DoubleTrueError), (
        "Expected DoubleTrueError but got a different exception type"
    )

    # Anti-Illusion: verify IMP:10 log was emitted (SafetyTripped event)
    imp10_logs = [log for log in high_imp_logs if "[IMP:10]" in log]
    assert len(imp10_logs) > 0, (
        "Critical LDD Error: route_from_context must emit IMP:10 log before raising DoubleTrueError"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_route_double_true_raises


# START_FUNCTION_test_route_needs_data_returns_tool
# START_CONTRACT:
# PURPOSE: Verify that route_from_context returns "tool" when needs_data=True
#          (ready_for_weights=False).
# INPUTS:
# - caplog fixture => caplog: pytest.LogCaptureFixture
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(8): RoutingLogic; PATTERN(7): AtomicTest]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_route_needs_data_returns_tool(caplog, ldd_capture):
    """
    When needs_data=True and ready_for_weights=False, route_from_context should
    return the string "tool" directing the graph to Node 2 (2_Tool_Node).
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Call router with needs_data=True state]
    state = {
        "_needs_data": True,
        "_ready_for_weights": False,
        "is_data_sufficient": False,
        "search_queries": ["mortgage rates", "rental market trends"],
    }

    result = route_from_context(state)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert routing result]
    assert result == "tool", (
        f"Expected route 'tool' when needs_data=True, but got: {result!r}"
    )

    # Anti-Illusion: verify IMP:9 routing decision log was emitted
    imp9_logs = [log for log in high_imp_logs if "[IMP:9]" in log and "route_from_context" in log]
    assert len(imp9_logs) > 0, (
        "Critical LDD Error: route_from_context must emit IMP:9 routing decision log"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_route_needs_data_returns_tool


# START_FUNCTION_test_route_ready_returns_questioner
# START_CONTRACT:
# PURPOSE: Verify that route_from_context returns "questioner" when ready_for_weights=True
#          (needs_data=False).
# INPUTS:
# - caplog fixture => caplog: pytest.LogCaptureFixture
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(8): RoutingLogic; PATTERN(7): AtomicTest]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_route_ready_returns_questioner(caplog, ldd_capture):
    """
    When ready_for_weights=True and needs_data=False, route_from_context should
    return the string "questioner" directing the graph to Node 3 (3_Weight_Questioner).
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Call router with ready_for_weights=True state]
    state = {
        "_needs_data": False,
        "_ready_for_weights": True,
        "is_data_sufficient": True,
        "search_queries": [],
    }

    result = route_from_context(state)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert routing result]
    assert result == "questioner", (
        f"Expected route 'questioner' when ready_for_weights=True, but got: {result!r}"
    )

    # Anti-Illusion: verify IMP:9 routing decision log was emitted
    imp9_logs = [log for log in high_imp_logs if "[IMP:9]" in log and "route_from_context" in log]
    assert len(imp9_logs) > 0, (
        "Critical LDD Error: route_from_context must emit IMP:9 routing decision log"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_route_ready_returns_questioner
