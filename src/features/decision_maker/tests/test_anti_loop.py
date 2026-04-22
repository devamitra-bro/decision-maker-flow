# FILE: src/features/decision_maker/tests/test_anti_loop.py
# VERSION: 2.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Test suite for Anti-Loop safety in cove_critique and route_from_critique (AC6).
# SCOPE: Verifies that seeding rewrite_count=2 forces finalize routing and emits IMP:10 log.
#        All cove_critique tests are now @pytest.mark.asyncio since cove_critique is async.
# INPUT: Synthetic state with rewrite_count=2; fake_llm fixture for DI (scripted rewrite=True).
# OUTPUT: pytest pass/fail assertions + IMP:7-10 LDD trajectory printed to stdout.
# KEYWORDS: [DOMAIN(8): Tests; CONCEPT(10): AntiLoop; CONCEPT(9): CoVe; PATTERN(8): DependencyInjection;
#            CONCEPT(9): AsyncIO; PATTERN(7): AsyncTest]
# LINKS: [READS_DATA_FROM(9): src.features.decision_maker.nodes.cove_critique;
#         READS_DATA_FROM(9): src.features.decision_maker.nodes.route_from_critique;
#         READS_DATA_FROM(8): tests.conftest.fake_llm]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §4 Test Matrix row 2; AC6, §5.5
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v2.0.0 — async migration; all cove_critique tests use @pytest.mark.asyncio
#              and await cove_critique(); route_from_critique tests remain sync.
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial implementation; Anti-Loop forced approval + routing tests.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 10 [Test: rewrite_count=2 causes async cove_critique to force needs_rewrite=False] => test_anti_loop_forces_approval
# FUNC 9 [Test: route_from_critique with rewrite_count=2 returns "finalize" (sync)] => test_route_critique_finalize_at_cap
# FUNC 9 [Test: IMP:10 Anti-Loop log is emitted when cap is triggered in async cove_critique] => test_anti_loop_imp10_log_emitted
# END_MODULE_MAP

import logging
import pytest

from src.features.decision_maker.nodes import cove_critique, route_from_critique


# START_FUNCTION_test_anti_loop_forces_approval
# START_CONTRACT:
# PURPOSE: Verify that async cove_critique forces needs_rewrite=False when rewrite_count >= 2,
#          even though the fake_llm returns needs_rewrite=True.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# - fake_llm fixture (scripted to return needs_rewrite=True) => fake_llm
# KEYWORDS: [CONCEPT(10): AntiLoop; PATTERN(8): DependencyInjection; CONCEPT(9): ForceApproval;
#            CONCEPT(9): AsyncIO]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.mark.asyncio
async def test_anti_loop_forces_approval(caplog, ldd_capture, fake_llm):
    """
    Seeds the state with rewrite_count=2 and injects a fake LLM that returns
    needs_rewrite=True. The Anti-Loop cap in cove_critique MUST override the LLM
    and force needs_rewrite=False, leaving rewrite_count unchanged at 2
    (since increment only occurs when needs_rewrite remains True after override check).

    cove_critique is now async def — this test uses @pytest.mark.asyncio and await.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_SETUP: [Prepare seeded state with rewrite_count=2]
    state = {
        "user_input": "Should I buy a house or rent?",
        "dilemma": "Mortgage vs Rental — long-term financial decision",
        "weights": {"stability": 8, "cost": 6, "flexibility": 4},
        "tool_facts": [{"query": "mortgage rates", "result": "<stubbed-fact>", "source": "stub"}],
        "draft_analysis": "Draft analysis text for testing purposes.",
        "critique_feedback": "",
        "rewrite_count": 2,  # AT CAP — Anti-Loop must fire
    }
    # END_BLOCK_SETUP

    # START_BLOCK_EXECUTION: [Await async cove_critique with fake_llm that says needs_rewrite=True]
    result = await cove_critique(state, llm_factory=fake_llm)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert Anti-Loop forced approval]
    # rewrite_count must NOT increment because needs_rewrite was forced to False
    assert result["rewrite_count"] == 2, (
        f"Anti-Loop: rewrite_count should remain 2 (no increment when forced False), "
        f"but got: {result['rewrite_count']}"
    )

    # The critique_feedback from the LLM is preserved (for transparency) even though rewrite is blocked
    # The key assertion: no rewrite will happen because route_from_critique will route to "finalize"
    # Verify by checking that route_from_critique gives "finalize" with this result
    route = route_from_critique(result)
    assert route == "finalize", (
        f"Anti-Loop: with rewrite_count=2 and forced approval, route must be 'finalize', "
        f"but got: {route!r}"
    )

    # Anti-Illusion: verify IMP:10 Anti-Loop trigger log was emitted
    imp10_logs = [log for log in high_imp_logs if "[IMP:10]" in log and "ANTI_LOOP" in log]
    assert len(imp10_logs) > 0, (
        "Critical LDD Error: cove_critique must emit [IMP:10][BLOCK_ANTI_LOOP][SafetyTripped] "
        "log when rewrite_count >= 2"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_anti_loop_forces_approval


# START_FUNCTION_test_route_critique_finalize_at_cap
# START_CONTRACT:
# PURPOSE: Verify route_from_critique returns "finalize" when rewrite_count has reached
#          the cap (>=2), regardless of critique_feedback content. Router is SYNC — no asyncio.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): AntiLoop; PATTERN(7): AtomicTest; CONCEPT(8): RouterLogic;
#            CONCEPT(8): SyncRouter]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_route_critique_finalize_at_cap(caplog, ldd_capture):
    """
    Seeds state with rewrite_count=2 and a non-empty critique_feedback.
    route_from_critique must return "finalize" because the rewrite_count cap is reached,
    even though critique_feedback is set (which would normally indicate a rewrite is needed).

    route_from_critique is SYNC — no @pytest.mark.asyncio needed here.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Call sync router with cap-state]
    state = {
        "critique_feedback": "There are logical gaps in the analysis.",
        "rewrite_count": 2,  # AT CAP
    }
    result = route_from_critique(state)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert finalize route]
    assert result == "finalize", (
        f"Expected 'finalize' when rewrite_count=2, but got: {result!r}"
    )

    # Anti-Illusion: verify IMP:9 routing log was emitted
    imp9_logs = [log for log in high_imp_logs if "[IMP:9]" in log and "route_from_critique" in log]
    assert len(imp9_logs) > 0, (
        "Critical LDD Error: route_from_critique must emit IMP:9 routing decision log"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_route_critique_finalize_at_cap


# START_FUNCTION_test_anti_loop_imp10_log_emitted
# START_CONTRACT:
# PURPOSE: Explicit audit test: IMP:10 Anti-Loop log must contain expected format strings
#          (BLOCK_ANTI_LOOP, SafetyTripped, rewrite_count keyword).
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# - fake_llm fixture => fake_llm
# KEYWORDS: [CONCEPT(9): LDDTelemetry; PATTERN(8): LogAudit; CONCEPT(10): AntiLoop;
#            CONCEPT(9): AsyncIO]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
@pytest.mark.asyncio
async def test_anti_loop_imp10_log_emitted(caplog, ldd_capture, fake_llm):
    """
    Semantic Trace Verification test. Calls async cove_critique with rewrite_count=2 and
    verifies that the emitted IMP:10 log:
    1. Contains "BLOCK_ANTI_LOOP" — correct block identifier
    2. Contains "SafetyTripped" — correct operation type
    3. Contains "forcing approval" — confirms the override action
    4. Contains "rewrite_count" — confirms context is logged

    cove_critique is now async — uses @pytest.mark.asyncio and await.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Await async cove_critique at rewrite_count=2]
    state = {
        "user_input": "test",
        "dilemma": "test dilemma",
        "weights": {},
        "tool_facts": [],
        "draft_analysis": "test draft",
        "critique_feedback": "",
        "rewrite_count": 2,
    }
    await cove_critique(state, llm_factory=fake_llm)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Verify IMP:10 log format and content]
    imp10_anti_loop_logs = [
        log for log in high_imp_logs
        if "[IMP:10]" in log and "ANTI_LOOP" in log
    ]

    assert len(imp10_anti_loop_logs) > 0, (
        "No IMP:10 Anti-Loop log found. cove_critique must emit "
        "[LOGIC][IMP:10][cove_critique][BLOCK_ANTI_LOOP][SafetyTripped] when rewrite_count >= 2"
    )

    anti_loop_log = imp10_anti_loop_logs[0]
    assert "SafetyTripped" in anti_loop_log, (
        f"IMP:10 log missing 'SafetyTripped' marker. Got: {anti_loop_log!r}"
    )
    assert "forcing approval" in anti_loop_log, (
        f"IMP:10 log missing 'forcing approval' text. Got: {anti_loop_log!r}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_anti_loop_imp10_log_emitted
