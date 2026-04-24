# FILE: tests/smoke/conftest.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Shared pytest session hooks for the smoke unit test suite (tests/smoke/).
#          Anti-Loop Protocol: session-scoped hooks manage tests/smoke/.test_counter.json
#          (separate counter file from tests/server/ and tests/ui/).
#          Provides sys.path setup so that src.server.auth imports correctly.
# SCOPE: Anti-Loop counter management (pytest_sessionstart/finish); sys.path injection.
# INPUT: pytest session lifecycle events.
# OUTPUT: tests/smoke/.test_counter.json maintained per session.
# KEYWORDS: [DOMAIN(7): TestInfra; CONCEPT(9): AntiLoop; PATTERN(8): SessionHook]
# LINKS: [USES_API(7): pytest; READS_DATA_FROM(6): tests/smoke/.test_counter.json]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §5.2 (Anti-Loop Protocol)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Counter file is tests/smoke/.test_counter.json (NOT shared with other suites).
# - Counter increment ONLY in pytest_sessionfinish hook, NEVER in test bodies.
# - Counter resets to 0 ONLY when exitstatus == 0 (all tests pass).
# END_INVARIANTS
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice E: Anti-Loop session hooks for smoke tests.]
# END_CHANGE_SUMMARY

import json
import os
import sys

import pytest

# START_BLOCK_PATH_SETUP: [Ensure project root on sys.path for src.server.auth imports]
_SMOKE_DIR = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.dirname(_SMOKE_DIR)
_PROJECT_ROOT = os.path.dirname(_TESTS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
# END_BLOCK_PATH_SETUP

_COUNTER_FILE = os.path.join(_SMOKE_DIR, ".test_counter.json")


def _load_counter() -> dict:
    """Load the Anti-Loop counter JSON; return default if absent or corrupt."""
    if not os.path.exists(_COUNTER_FILE):
        return {"attempts": 0}
    try:
        with open(_COUNTER_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"attempts": 0}


def _save_counter(data: dict) -> None:
    """Persist the Anti-Loop counter JSON atomically."""
    with open(_COUNTER_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def pytest_sessionstart(session: pytest.Session) -> None:
    """
    Anti-Loop: load current attempt count and print a banner so that escalation
    levels (checklist → external help → reflection → escalation) are visible.
    """
    counter = _load_counter()
    attempts = counter.get("attempts", 0)

    print(f"\n[SMOKE][AntiLoop] Attempt #{attempts + 1} of smoke unit tests.")

    if attempts == 1:
        print(
            "[SMOKE][AntiLoop][CHECKLIST] Common failure causes:\n"
            "  1. src.server.auth not importable — check sys.path setup in conftest.\n"
            "  2. _b64url_encode/_b64url_decode mismatch — verify roundtrip manually.\n"
            "  3. mint_session_token JSON key ordering differs from verify expectation.\n"
            "  4. Test uses time.time() for exp that races test execution (use fixed future).\n"
            "  5. pytest.mark.unit not registered in pytest.ini — add 'unit' to markers."
        )
    elif attempts == 2:
        print(
            "[SMOKE][AntiLoop][Attempt 3] Use WebSearch or Context7 MCP to find solution online."
        )
    elif attempts == 3:
        print(
            "[SMOKE][AntiLoop][Attempt 4] WARNING: Looping risk! Pause and reflect. "
            "Are you repeating a failed strategy? Consider alternatives (Superposition)."
        )
    elif attempts >= 4:
        print(
            "[SMOKE][AntiLoop][Attempt 5+] CRITICAL ERROR: Agent looping detected. "
            "STOP. Formulate a help request for the operator."
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """
    Anti-Loop: increment counter on failure, reset to 0 on full PASS.
    """
    counter = _load_counter()
    if exitstatus == 0:
        counter["attempts"] = 0
        print("\n[SMOKE][AntiLoop] All smoke unit tests PASSED. Counter reset to 0.")
    else:
        counter["attempts"] = counter.get("attempts", 0) + 1
        print(
            f"\n[SMOKE][AntiLoop] Tests FAILED. "
            f"Attempt count incremented to {counter['attempts']}."
        )
    _save_counter(counter)
