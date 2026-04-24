# FILE: src/server/sweeper.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Background asyncio coroutine that periodically scans the checkpointer for
#          stale sessions and deletes them, decrementing the active_sessions gauge.
#          Implements the TTL-based eviction predicate from §9.4 touch-based exclusion.
#          Designed for clock-injection (deterministic tests) and clean CancelledError handling.
# SCOPE: Sweeper class with __init__ accepting injected dependencies and run() coroutine.
#        Clock injection via Callable[[], float] for freezegun-compatible testing.
# INPUT: TouchingCheckpointer, threshold_sec, interval_sec, Metrics instance, optional clock.
# OUTPUT: Deleted stale sessions; gauge decremented; LDD log emitted per §4.5.
# KEYWORDS: [DOMAIN(9): SessionLifecycle; TECH(9): asyncio_task; CONCEPT(9): TTL_Eviction;
#            PATTERN(9): ClockInjection; CONCEPT(8): CancellationSafe; PATTERN(8): ConstructorDI]
# LINKS: [USES_API(9): src.server.checkpoint_factory.TouchingCheckpointer;
#         USES_API(9): src.server.metrics.Metrics;
#         USES_API(8): asyncio.sleep; USES_API(8): asyncio.CancelledError]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §4.5 (Sweeper coroutine), §9.4 (touch predicate)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - run() is an infinite loop; it exits ONLY on asyncio.CancelledError.
# - CancelledError is propagated (not swallowed) to allow clean asyncio.Task cancellation.
# - Every tick increments metrics.sweeper_runs_total exactly once.
# - metrics.sweeper_deleted_total is incremented by the count of deleted sessions per tick.
# - metrics.active_sessions is decremented once per deleted session.
# - Session FP in [Cleanup] log is sha256:<8hex> of the thread_id — never raw thread_id.
# - Clock is injectable for deterministic test control (freezegun-friendly).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why inject the clock as Callable[[], float] instead of using time.time directly?
# A: Tests using freezegun need to control "now" without monkeypatching the entire time
#    module. An injected clock parameter allows precise tick-level control in unit tests
#    while keeping production behavior identical (default: time.time).
# Q: Why does run() propagate CancelledError instead of catching it?
# A: The lifespan handler (§4.6) calls task.cancel() + await with exception-swallow.
#    The standard asyncio pattern for cooperative shutdown is: CancelledError propagates
#    from within an awaited coroutine (asyncio.sleep). Catching it would create a zombie
#    task that never terminates, preventing clean shutdown.
# Q: Why not acquire a lock around sweeper deletions (§4.5 step 5)?
# A: Plan §9.4 explicitly defers LockRegistry to BACKLOG.md (Postgres/multi-worker future).
#    The touch-based exclusion (SWEEP_THRESHOLD >= 5 * TURN_TIMEOUT) is the accepted
#    race-window contract for SQLite MVP. Documenting this here for future agents.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: Sweeper class with run() coroutine,
#               clock injection, TouchingCheckpointer.adelete_thread, LDD logs per §4.5.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 9 [Background sweeper: infinite loop, TTL eviction, gauge decrement, LDD logging] => Sweeper
# END_MODULE_MAP
#
# START_USE_CASES:
# - [Sweeper]: Lifespan -> Sweeper(checkpointer, threshold, interval, metrics) ->
#   asyncio.create_task(sweeper.run()) -> periodic TTL eviction of stale sessions
# END_USE_CASES

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.server.checkpoint_factory import TouchingCheckpointer
    from src.server.metrics import Metrics

logger = logging.getLogger(__name__)


def _session_fp(thread_id: str) -> str:
    """Return sha256:<8hex> fingerprint of a thread_id for safe log inclusion."""
    return "sha256:" + hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:8]


# START_FUNCTION_Sweeper
# START_CONTRACT:
# PURPOSE: Background asyncio coroutine implementing TTL-based session eviction.
#          Constructor accepts all dependencies via injection (checkpointer, threshold_sec,
#          interval_sec, metrics, clock). The run() method is an infinite loop:
#          sleep → scan stale → delete → log → increment metrics.
#          Exits cleanly on asyncio.CancelledError.
# INPUTS:
#   - Active TouchingCheckpointer for stale scan + deletion => checkpointer: TouchingCheckpointer
#   - Inactivity threshold for eviction (seconds) => threshold_sec: int
#   - Sleep interval between sweeper ticks (seconds) => interval_sec: int
#   - Metrics instance for gauge/counter updates => metrics: Metrics
#   - Clock callable returning current time as float (default: time.time) => clock: Callable[[], float]
# OUTPUTS: None (long-running coroutine, exits only on CancelledError).
# SIDE_EFFECTS:
#   - Calls checkpointer.adelete_thread() for each stale session.
#   - Decrements metrics.active_sessions per deleted session.
#   - Increments metrics.sweeper_runs_total once per tick.
#   - Increments metrics.sweeper_deleted_total by count of deletions per tick.
#   - Logs [IMP:5][Sweeper][Sweep][Cleanup] + [Summary] per tick.
# KEYWORDS: [PATTERN(9): InfiniteLoop; CONCEPT(9): TTL_Eviction; TECH(8): asyncio_sleep;
#            PATTERN(8): ClockInjection; CONCEPT(8): CancellationSafe]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
class Sweeper:
    """
    Background asyncio coroutine implementing idle-session TTL eviction.

    Each tick:
    1. sleep(interval_sec) — yields control to the event loop; CancelledError exits here.
    2. Calls checkpointer.list_stale(now, threshold_sec) to find eviction candidates.
    3. For each stale thread_id: adelete_thread, dec active_sessions gauge, log [Cleanup].
    4. Emits [Summary] log with total count.
    5. Increments sweeper_runs_total and sweeper_deleted_total metrics.

    The clock parameter is a Callable[[], float] defaulting to time.time. Injecting a
    fake clock (e.g. lambda: fake_now) in tests allows deterministic tick control
    without freezing the entire asyncio event loop.

    The sweep_threshold_secs invariant (SWEEP_THRESHOLD >= 5 * TURN_TIMEOUT) is
    enforced by Config's model_validator — this class trusts the injected threshold_sec.
    """

    def __init__(
        self,
        checkpointer: "TouchingCheckpointer",
        threshold_sec: int,
        interval_sec: int,
        metrics: "Metrics",
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Initialise Sweeper with all dependencies injected. clock defaults to time.time
        for production use; pass a lambda or MagicMock in tests for deterministic control.
        """
        self._checkpointer = checkpointer
        self._threshold_sec = threshold_sec
        self._interval_sec = interval_sec
        self._metrics = metrics
        self._clock = clock

    # START_BLOCK_RUN_LOOP: [Infinite asyncio loop — exits only on CancelledError]

    async def run(self) -> None:
        """
        Infinite sweeper loop. Sleeps for interval_sec, then scans for stale sessions
        and deletes them. Propagates CancelledError for clean lifespan shutdown.

        The sleep comes FIRST in each iteration so the sweeper does not run immediately
        on startup (the server may not have accepted any sessions yet at t=0).
        """
        logger.info(
            f"[BRAINSTORM][IMP:5][Sweeper][Lifecycle][Start] "
            f"interval_sec={self._interval_sec} threshold_sec={self._threshold_sec} [OK]"
        )

        while True:
            # START_BLOCK_SLEEP: [Yield to event loop; CancelledError propagates here]
            try:
                await asyncio.sleep(self._interval_sec)
            except asyncio.CancelledError:
                logger.info(
                    "[BRAINSTORM][IMP:5][Sweeper][Lifecycle][Cancelled] "
                    "Sweeper task cancelled during sleep [OK]"
                )
                raise
            # END_BLOCK_SLEEP

            # START_BLOCK_SCAN_AND_DELETE: [Scan stale sessions and delete them]
            await self._tick()
            # END_BLOCK_SCAN_AND_DELETE

    # END_BLOCK_RUN_LOOP

    # START_BLOCK_TICK: [Single sweep tick — scan + delete + metrics + log]

    async def _tick(self) -> None:
        """
        Execute one sweep tick: scan stale sessions, delete each, update metrics and logs.
        Called by run() after each sleep interval. Isolated for unit testing.
        """
        now_unix = int(self._clock())

        # START_BLOCK_LIST_STALE: [Query stale thread_ids from TouchingCheckpointer]
        stale_ids = await self._checkpointer.list_stale(
            now_unix=now_unix,
            threshold_sec=self._threshold_sec,
        )
        # END_BLOCK_LIST_STALE

        # START_BLOCK_DELETE_SESSIONS: [Delete each stale session with per-session log]
        deleted_count = 0
        for thread_id in stale_ids:
            try:
                await self._checkpointer.adelete_thread(thread_id)
                self._metrics.active_sessions.dec()
                deleted_count += 1

                fp = _session_fp(thread_id)
                logger.info(
                    f"[BRAINSTORM][IMP:5][Sweeper][Sweep][Cleanup] "
                    f"session_fp={fp} [OK]"
                )
            except Exception as exc:
                fp = _session_fp(thread_id)
                logger.error(
                    f"[BRAINSTORM][IMP:8][Sweeper][Sweep][DeleteError] "
                    f"session_fp={fp} err={exc!r} [FAIL]"
                )
                # Continue sweeping other sessions — single deletion failure is not fatal
        # END_BLOCK_DELETE_SESSIONS

        # START_BLOCK_METRICS_AND_SUMMARY: [Increment run counters and emit summary log]
        self._metrics.sweeper_runs_total.inc()
        if deleted_count > 0:
            self._metrics.sweeper_deleted_total.inc(deleted_count)

        logger.info(
            f"[BRAINSTORM][IMP:5][Sweeper][Sweep][Summary] "
            f"deleted={deleted_count} now_unix={now_unix} [OK]"
        )
        # END_BLOCK_METRICS_AND_SUMMARY

    # END_BLOCK_TICK

# END_FUNCTION_Sweeper
