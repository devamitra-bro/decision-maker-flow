# FILE: tests/ui/conftest.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Shared pytest fixtures and session hooks for the UI test suite.
#          Anti-Loop Protocol: session-scoped hooks manage .test_counter.json.
#          Provides pytest_asyncio mode="auto", shared ldd_capture helper, and
#          a session-scoped event loop policy for async tests.
# SCOPE: pytest_sessionstart/pytest_sessionfinish for Anti-Loop counter management;
#        pytest-asyncio asyncio_mode="auto"; ldd_capture helper fixture.
# INPUT: pytest session lifecycle events.
# OUTPUT: .test_counter.json maintained per session; async-aware test infrastructure.
# KEYWORDS: [DOMAIN(8): TestInfra; CONCEPT(9): AntiLoop; PATTERN(8): SessionHook;
#            CONCEPT(7): LDDTelemetry; CONCEPT(10): AsyncIO; PATTERN(9): AsyncFixture]
# LINKS: [USES_API(7): pytest; USES_API(8): pytest_asyncio; READS_DATA_FROM(6): .test_counter.json]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md §1 (tests_ui_conftest_py); AC_UI_13
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Counter increment ONLY happens in pytest_sessionfinish hook, NEVER in test bodies.
# - Counter resets to 0 ONLY when all tests pass (exitstatus == 0).
# - pytest_asyncio mode is "auto" for the entire tests/ui/ suite.
# - ldd_capture fixture returns a callable — not a list directly.
# - CRITICAL: Gradio launch() is NEVER called inside any test (N4 invariant). A grep
#   assertion verifies this in conftest to satisfy AC_UI_11.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why use pytest_sessionfinish for counter management and not pytest_runtest_logreport?
# A: Same reasoning as backend conftest: session-level hooks give a single decision point
#    per run. Per-test hooks require complex state tracking. Mode-code spec mandates
#    NEVER increment from inside test bodies — session hooks are compliant.
# Q: Why asyncio_mode = "auto" in pytest.ini_options?
# A: All UI tests are async generators or async functions. Auto mode removes the need for
#    @pytest.mark.asyncio on every test and matches pytest-asyncio 0.24.x recommended config.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; Anti-Loop session hooks; asyncio_mode auto;
#              ldd_capture fixture; headless assertion (no launch() in tests).
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 8 [pytest session start hook — loads counter and prints checklist if failures] => pytest_sessionstart
# FUNC 8 [pytest session finish hook — updates counter based on pass/fail result] => pytest_sessionfinish
# FUNC 7 [Helper fixture: filters and prints IMP:7-10 log records from caplog] => ldd_capture
# END_MODULE_MAP

import asyncio
import json
import sys
from pathlib import Path
from typing import List

import pytest

# Counter file lives in the same directory as this conftest (tests/ui/)
_COUNTER_FILE = Path(__file__).parent / ".test_counter.json"

# Add brainstorm root to sys.path for clean imports without relative path hacks
_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

# pytest-asyncio auto mode for all tests/ui/ tests
pytest_plugins = ["pytest_asyncio"]


# START_FUNCTION_pytest_sessionstart
# START_CONTRACT:
# PURPOSE: Load attempt counter from .test_counter.json and print Anti-Loop checklist
#          if prior failures exist.
# INPUTS:
# - pytest session object => session: pytest.Session
# OUTPUTS: None (side effect: prints checklist to stdout if counter > 0)
# SIDE_EFFECTS: Reads .test_counter.json; prints to stdout.
# KEYWORDS: [PATTERN(9): AntiLoop; CONCEPT(8): SessionHook]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def pytest_sessionstart(session) -> None:
    """
    Session start hook. Reads .test_counter.json to determine cumulative failure count.
    If failures exist, prints the Anti-Loop checklist with UI-specific items.
    Escalates warnings at attempt 3, 4, 5+.
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
        print(f"ANTI-LOOP PROTOCOL — tests/ui/ — Attempt #{failures + 1}")
        print(f"{'='*70}")
        print("CHECKLIST (common UI test failure causes):")
        print("  [ ] Import errors: is brainstorm root in sys.path? (conftest adds it)")
        print("  [ ] N3 violation: gradio must NOT be imported in controllers.py or presenter.py")
        print("  [ ] N4 violation: demo.launch() or ui.launch() must NOT appear in any test file")
        print("  [ ] UIEvent 'kind' field must match exactly one of the 9 kinds in P3")
        print("  [ ] orchestrate_start must emit session_started FIRST and awaiting_user LAST")
        print("  [ ] orchestrate_resume must emit resume_started FIRST and final_answer LAST")
        print("  [ ] cove_rewrite must be emitted BEFORE node_completed (AC_UI_08 ordering)")
        print("  [ ] render() must return tuple (list, dict, str) — never None, never 2-tuple")
        print("  [ ] Async tests require asyncio_mode='auto' in pytest.ini or pyproject.toml")
        print("  [ ] Monkeypatch target must be 'src.ui.controllers.stream_session' (not graph)")
        print("  [ ] Async generator mock: use async def with yield, not MagicMock")
        print("  [ ] on_submit output tuple must have exactly 6 elements (see app.py contract)")
        print("  [ ] thread_id in yields must equal the thread_id passed in (AC_UI_17)")
        print("  [ ] caplog level must be set to INFO before calling business logic")
        print("  [ ] LDD Anti-Illusion: found_log must be True from IMP:9 assertion")

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
    - exitstatus == 0 (all passed): resets failures to 0.
    - exitstatus != 0 (any failure): increments failures by 1.
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
        print(f"\n[AntiLoop] tests/ui/ — All tests PASSED. Counter reset to 0.")
    else:
        counter_data["failures"] = counter_data.get("failures", 0) + 1
        print(f"\n[AntiLoop] tests/ui/ — Test failures detected. Counter now: {counter_data['failures']}")
    # END_BLOCK_UPDATE_COUNTER

    # START_BLOCK_WRITE_COUNTER: [Persist counter to file]
    try:
        with open(_COUNTER_FILE, "w") as f:
            json.dump(counter_data, f)
    except OSError as e:
        print(f"[AntiLoop][WARNING] Could not write counter file: {e}")
    # END_BLOCK_WRITE_COUNTER
# END_FUNCTION_pytest_sessionfinish


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

    Call as: high_imp_logs = ldd_capture() AFTER the code under test has run.
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
