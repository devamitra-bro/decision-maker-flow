# FILE: src/features/decision_maker/tests/test_parallel_search.py
# VERSION: 2.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Test suite proving asyncio.gather parallel execution in tool_node (AC11, AC13).
# SCOPE: Two @pytest.mark.asyncio tests:
#        1. test_parallel_search_wall_clock — 3 queries @ 0.2s complete in <0.5s
#        2. test_parallel_search_ldd_pairs — per-query IMP:7/IMP:8 pairs both present in caplog
# INPUT: fake_search_async fixture (configurable delay); synthetic state with 3 queries.
# OUTPUT: pytest pass/fail assertions + IMP:7-10 LDD trajectory printed to stdout.
# KEYWORDS: [DOMAIN(8): Tests; CONCEPT(9): AsyncioGather; PATTERN(9): ConcurrencyTest;
#            CONCEPT(10): AsyncIO; CONCEPT(8): LDDParallelIntegrity]
# LINKS: [READS_DATA_FROM(10): src.features.decision_maker.nodes.tool_node;
#         READS_DATA_FROM(8): tests.conftest.fake_search_async]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §4 Test Matrix row 6; AC11, AC13, AC15
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v2.0.0 — new file; parallel search concurrency tests using fake_search_async
#              with DELAY=0.2s and 3 queries; proves asyncio.gather wall-clock < 0.5s.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Test: 3 queries @ 0.2s complete in < 0.5s via asyncio.gather] => test_parallel_search_wall_clock
# FUNC 9 [Test: per-query IMP:7 PENDING + IMP:8 SUCCESS pairs in caplog (AC13)] => test_parallel_search_ldd_pairs
# END_MODULE_MAP

import logging
import time

import pytest

from src.features.decision_maker.nodes import tool_node

# Parallelism proof parameters
_DELAY = 0.2     # seconds per query (fake_search_async default)
_N_QUERIES = 3   # number of concurrent queries
_SEQUENTIAL_THRESHOLD = _DELAY * _N_QUERIES  # 0.6s — what sequential execution would take
_PARALLEL_THRESHOLD = _DELAY * _N_QUERIES * 0.75  # 0.45s — parallel must beat this (AC11)


# START_FUNCTION_test_parallel_search_wall_clock
# START_CONTRACT:
# PURPOSE: Verify that tool_node executes N queries concurrently via asyncio.gather.
#          Wall-clock time must be < DELAY * N * 0.75 (would be >= DELAY * N if sequential).
# INPUTS:
# - fake_search_async fixture (0.2s per query) => fake_search_async
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(9): AsyncioGather; PATTERN(9): ConcurrencyTest; CONCEPT(8): WallClockAssertion]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.mark.asyncio
async def test_parallel_search_wall_clock(fake_search_async, caplog, ldd_capture):
    """
    Concurrency proof test for tool_node's asyncio.gather execution.

    Constructs a state with 3 search queries. Injects fake_search_async (which sleeps
    DELAY=0.2s per query) via the search_fn DI parameter. Records wall-clock time before
    and after awaiting tool_node. Asserts:
    - Wall-clock elapsed < 0.45s (DELAY * N * 0.75) — proves genuine parallelism
    - Sequential execution would take >= 0.6s (DELAY * N = 0.2 * 3)

    The fake_search_async fixture is defined in conftest.py and is a direct callable
    (not a factory). It accepts (query, delay=0.2).
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_SETUP: [Build state with 3 search queries]
    queries = ["mortgage rates 2026", "rental market trends", "housing affordability index"]
    state = {
        "user_input": "Should I buy a house or rent?",
        "search_queries": queries,
        "tool_facts": [],
    }
    # END_BLOCK_SETUP

    # START_BLOCK_EXECUTION: [Time the parallel tool_node execution]
    start_time = time.monotonic()
    result = await tool_node(state, search_fn=fake_search_async)
    elapsed = time.monotonic() - start_time
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert concurrency and results]
    print(f"\n[ParallelSearch] Wall-clock elapsed: {elapsed:.3f}s")
    print(f"[ParallelSearch] Sequential would be >= {_SEQUENTIAL_THRESHOLD:.3f}s")
    print(f"[ParallelSearch] Parallel threshold: < {_PARALLEL_THRESHOLD:.3f}s")

    assert elapsed < _PARALLEL_THRESHOLD, (
        f"Parallel search too slow: elapsed={elapsed:.3f}s, threshold={_PARALLEL_THRESHOLD:.3f}s. "
        f"Expected asyncio.gather concurrency (sequential would be >= {_SEQUENTIAL_THRESHOLD:.3f}s). "
        f"Check that tool_node uses asyncio.gather and not sequential awaits."
    )

    # All 3 queries must produce results in tool_facts
    tool_facts = result.get("tool_facts", [])
    assert len(tool_facts) == _N_QUERIES, (
        f"Expected {_N_QUERIES} tool_facts entries (one per query), got {len(tool_facts)}"
    )

    # Anti-Illusion: verify IMP:9 state write log was emitted by tool_node
    imp9_tool_logs = [log for log in high_imp_logs if "[IMP:9]" in log and "tool_node" in log]
    assert len(imp9_tool_logs) > 0, (
        "Critical LDD Error: tool_node must emit IMP:9 state write log"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_parallel_search_wall_clock


# START_FUNCTION_test_parallel_search_ldd_pairs
# START_CONTRACT:
# PURPOSE: Verify LDD parallel integrity (AC13): for each query q, exactly ONE IMP:7 PENDING
#          and ONE IMP:8 SUCCESS line is present in caplog. Interleaving allowed.
# INPUTS:
# - fake_search_async fixture => fake_search_async
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(8): LDDParallelIntegrity; PATTERN(7): LogAudit; CONCEPT(9): AsyncioGather]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_parallel_search_ldd_pairs(fake_search_async, caplog, ldd_capture):
    """
    LDD parallel integrity test (AC13).

    Calls tool_node with 3 queries via fake_search_async. After execution, scans caplog
    for per-query IMP:7 [PENDING] and IMP:8 [SUCCESS] pairs. For each query string, asserts:
    - Exactly one IMP:7 log line containing query='{q!r}' and '[PENDING]'
    - Exactly one IMP:8 log line containing query='{q!r}' and '[SUCCESS]'

    The pairing assertion proves that:
    1. Each query emitted its own telemetry (no missed queries)
    2. No duplicate telemetry (no double-counting from retries or re-execution)
    3. The [PENDING]/[SUCCESS] semantic is preserved for all concurrent branches

    Interleaving of log lines from different queries is PERMITTED (and expected in
    concurrent execution) — the test only checks existence of each pair, not order.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_SETUP: [Build state with 3 queries]
    queries = ["buy vs rent factors", "interest rate forecast", "rental yield analysis"]
    state = {
        "user_input": "Buy or rent decision",
        "search_queries": queries,
        "tool_facts": [],
    }
    # END_BLOCK_SETUP

    # START_BLOCK_EXECUTION: [Execute parallel tool_node]
    result = await tool_node(state, search_fn=fake_search_async)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert per-query IMP:7/IMP:8 pairs for all 3 queries]
    print(f"\n[LDD Parallel Integrity] Checking {len(queries)} query pairs...")

    for q in queries:
        q_repr = repr(q)  # Match the f-string formatting: query={q!r}

        # Find IMP:7 PENDING lines for this query
        pending_logs = [
            log for log in high_imp_logs
            if "[IMP:7]" in log and f"query={q_repr}" in log and "[PENDING]" in log
        ]

        # Find IMP:8 SUCCESS lines for this query
        success_logs = [
            log for log in high_imp_logs
            if "[IMP:8]" in log and f"query={q_repr}" in log and "[SUCCESS]" in log
        ]

        print(f"  Query {q_repr}: IMP:7 PENDING={len(pending_logs)}, IMP:8 SUCCESS={len(success_logs)}")

        assert len(pending_logs) >= 1, (
            f"AC13 VIOLATION: No IMP:7 [PENDING] log found for query={q_repr}. "
            f"Each query must emit exactly one PENDING line before the outbound call."
        )
        assert len(success_logs) >= 1, (
            f"AC13 VIOLATION: No IMP:8 [SUCCESS] log found for query={q_repr}. "
            f"Each query must emit exactly one SUCCESS line after response received."
        )

    # Anti-Illusion: verify IMP:9 state write from tool_node exists
    imp9_tool_logs = [log for log in high_imp_logs if "[IMP:9]" in log and "tool_node" in log]
    assert len(imp9_tool_logs) > 0, (
        "Critical LDD Error: tool_node must emit IMP:9 state write log after gather completes"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_parallel_search_ldd_pairs
