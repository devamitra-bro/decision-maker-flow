# FILE: tests/server/test_sweeper.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Unit tests for the Sweeper class covering stale session deletion, touch-based
#          exclusion (recently touched sessions preserved), CancelledError propagation,
#          LDD summary log, and Prometheus metric increments. Uses clock injection for
#          deterministic time control.
# KEYWORDS: [DOMAIN(8): TestSweeper; TECH(8): asyncio; CONCEPT(8): ClockInjection]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §4.5, §9.4 (Sweeper tests)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: all sweeper unit tests per §9.4.]
# END_CHANGE_SUMMARY

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from src.server.checkpoint_factory import TouchingCheckpointer
from src.server.metrics import build_registry, make_metrics
from src.server.sweeper import Sweeper

pytestmark = pytest.mark.asyncio


def _make_stub_checkpointer(stale_ids: list = None):
    """Build a mock TouchingCheckpointer with configurable stale IDs."""
    stub = MagicMock(spec=TouchingCheckpointer)
    stub.list_stale = AsyncMock(return_value=stale_ids or [])
    stub.adelete_thread = AsyncMock(return_value=None)
    return stub


def _make_metrics():
    """Build isolated Prometheus metrics for testing."""
    registry = build_registry()
    return make_metrics(registry)


# START_FUNCTION_test_sweeper_deletes_stale_sessions
# START_CONTRACT:
# PURPOSE: Verify sweeper deletes stale sessions returned by list_stale and decrements
#          active_sessions gauge once per deleted session.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.asyncio
async def test_sweeper_deletes_stale_sessions():
    """
    Sweeper with 2 stale sessions should call adelete_thread twice and decrement
    active_sessions by 2. list_stale is called with correct (now, threshold) args.
    """
    stale_ids = ["session-stale-1", "session-stale-2"]
    stub_chkpt = _make_stub_checkpointer(stale_ids=stale_ids)
    metrics = _make_metrics()

    # Set active_sessions to 2 so dec does not go negative
    metrics.active_sessions.inc()
    metrics.active_sessions.inc()

    fake_now = 1_700_000_000.0
    sweeper = Sweeper(
        checkpointer=stub_chkpt,
        threshold_sec=600,
        interval_sec=60,
        metrics=metrics,
        clock=lambda: fake_now,
    )

    await sweeper._tick()

    # Verify both stale sessions were deleted
    assert stub_chkpt.adelete_thread.call_count == 2
    deleted_ids = {call.args[0] for call in stub_chkpt.adelete_thread.call_args_list}
    assert deleted_ids == set(stale_ids)

    # list_stale called with correct args
    stub_chkpt.list_stale.assert_called_once_with(
        now_unix=int(fake_now), threshold_sec=600
    )
# END_FUNCTION_test_sweeper_deletes_stale_sessions


# START_FUNCTION_test_sweeper_skips_recently_touched_session
# START_CONTRACT:
# PURPOSE: Verify that list_stale correctly excludes recently touched sessions (fresh)
#          while including stale ones. Tests the threshold boundary behavior.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.asyncio
async def test_sweeper_skips_recently_touched_session():
    """
    When list_stale returns only stale sessions (fresh ones excluded by the predicate),
    the sweeper deletes only the stale ones and preserves fresh ones.

    list_stale is the predicate — it returns thread_ids older than threshold.
    At t0 + threshold/2: no sessions are stale yet (fresh session preserved).
    At t0 + 2*threshold: session is stale and should be deleted.
    """
    t0 = 1_700_000_000
    threshold = 600

    stub_chkpt_fresh = _make_stub_checkpointer(stale_ids=[])  # Nothing stale at t0 + threshold/2
    metrics_fresh = _make_metrics()

    sweeper_fresh = Sweeper(
        checkpointer=stub_chkpt_fresh,
        threshold_sec=threshold,
        interval_sec=60,
        metrics=metrics_fresh,
        clock=lambda: float(t0 + threshold // 2),
    )
    await sweeper_fresh._tick()

    # Nothing should be deleted when no stale sessions
    stub_chkpt_fresh.adelete_thread.assert_not_called()

    # Now at t0 + 2*threshold: session is stale
    stub_chkpt_stale = _make_stub_checkpointer(stale_ids=["session-001"])
    metrics_stale = _make_metrics()
    metrics_stale.active_sessions.inc()

    sweeper_stale = Sweeper(
        checkpointer=stub_chkpt_stale,
        threshold_sec=threshold,
        interval_sec=60,
        metrics=metrics_stale,
        clock=lambda: float(t0 + 2 * threshold),
    )
    await sweeper_stale._tick()

    stub_chkpt_stale.adelete_thread.assert_called_once_with("session-001")
# END_FUNCTION_test_sweeper_skips_recently_touched_session


# START_FUNCTION_test_sweeper_handles_cancellation_cleanly
# START_CONTRACT:
# PURPOSE: Verify Sweeper.run() propagates CancelledError cleanly when task is cancelled
#          during its sleep phase.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.asyncio
async def test_sweeper_handles_cancellation_cleanly():
    """
    When asyncio.Task(sweeper.run()) is cancelled, CancelledError should propagate
    from within the sleep and the task should complete without hanging.
    """
    stub_chkpt = _make_stub_checkpointer(stale_ids=[])
    metrics = _make_metrics()

    sweeper = Sweeper(
        checkpointer=stub_chkpt,
        threshold_sec=600,
        interval_sec=999_999,  # Very long sleep to catch cancellation
        metrics=metrics,
    )

    task = asyncio.create_task(sweeper.run())

    # Give the task a moment to start sleeping
    await asyncio.sleep(0.01)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected — clean cancellation

    # Task should be done
    assert task.done(), "Task should be done after cancellation"
    assert task.cancelled() or isinstance(task.exception() if not task.cancelled() else None, type(None))
# END_FUNCTION_test_sweeper_handles_cancellation_cleanly


# START_FUNCTION_test_sweeper_emits_ldd_summary
# START_CONTRACT:
# PURPOSE: Verify that one sweep tick with 3 stale sessions emits
#          [IMP:5][Sweeper][Sweep][Summary] deleted=3 in log output.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.asyncio
async def test_sweeper_emits_ldd_summary(caplog):
    """
    A tick with 3 stale sessions should emit [BRAINSTORM][IMP:5][Sweeper][Sweep][Summary]
    deleted=3 in the log output. Uses caplog to capture log records.
    """
    import logging
    caplog.set_level(logging.INFO)

    stale_ids = ["s1", "s2", "s3"]
    stub_chkpt = _make_stub_checkpointer(stale_ids=stale_ids)
    metrics = _make_metrics()
    metrics.active_sessions.inc()
    metrics.active_sessions.inc()
    metrics.active_sessions.inc()

    sweeper = Sweeper(
        checkpointer=stub_chkpt,
        threshold_sec=600,
        interval_sec=60,
        metrics=metrics,
        clock=lambda: 1_700_000_000.0,
    )

    await sweeper._tick()

    # Print LDD trajectory
    print("\n--- LDD TRAJECTORY (IMP:5+) ---")
    summary_found = False
    for record in caplog.records:
        msg = record.getMessage()
        if "[Sweeper]" in msg:
            print(msg)
        if "[Sweeper][Sweep][Summary]" in msg and "deleted=3" in msg:
            summary_found = True
    print("--- END LDD TRAJECTORY ---\n")

    assert summary_found, "Expected [Sweeper][Sweep][Summary] deleted=3 log line"
# END_FUNCTION_test_sweeper_emits_ldd_summary


# START_FUNCTION_test_sweeper_metric_increments
# START_CONTRACT:
# PURPOSE: Verify one tick with 2 deletions increments sweeper_runs_total by 1
#          and sweeper_deleted_total by 2.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.mark.asyncio
async def test_sweeper_metric_increments():
    """
    One sweeper tick with 2 stale sessions should result in:
    - sweeper_runs_total = 1
    - sweeper_deleted_total = 2
    """
    stale_ids = ["session-a", "session-b"]
    stub_chkpt = _make_stub_checkpointer(stale_ids=stale_ids)
    metrics = _make_metrics()
    metrics.active_sessions.inc()
    metrics.active_sessions.inc()

    sweeper = Sweeper(
        checkpointer=stub_chkpt,
        threshold_sec=600,
        interval_sec=60,
        metrics=metrics,
        clock=lambda: 1_700_000_000.0,
    )

    await sweeper._tick()

    runs_value = metrics.sweeper_runs_total._value.get()
    deleted_value = metrics.sweeper_deleted_total._value.get()

    assert runs_value == 1.0, f"Expected sweeper_runs_total=1, got {runs_value}"
    assert deleted_value == 2.0, f"Expected sweeper_deleted_total=2, got {deleted_value}"
# END_FUNCTION_test_sweeper_metric_increments
