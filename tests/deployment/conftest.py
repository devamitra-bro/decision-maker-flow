# FILE: tests/deployment/conftest.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Shared fixtures and Anti-Loop session hooks for the deployment test suite.
#          Manages tests/deployment/.test_counter.json for attempt tracking.
#          Provides shared path fixtures (PROJECT_ROOT, K8S_DIR, DOCKERFILE_PATH)
#          accessible to all tests in tests/deployment/.
# SCOPE: Anti-Loop Protocol (pytest_sessionstart/finish); project path fixtures;
#        deployment-test-counter management separate from tests/server/.test_counter.json.
# INPUT: pytest session lifecycle events.
# OUTPUT: .test_counter.json maintained per session; path constants injected into tests.
# KEYWORDS: [DOMAIN(8): TestInfra; CONCEPT(9): AntiLoop; PATTERN(8): SessionHook;
#            CONCEPT(8): PathFixtures]
# LINKS: [READS_DATA_FROM(6): tests/deployment/.test_counter.json]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §5.2 (Anti-Loop), §2.4 (Slice D).
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Counter file is tests/deployment/.test_counter.json (separate from server counter).
# - Counter increments ONLY in pytest_sessionfinish hook when exitstatus != 0.
# - Counter resets to 0 ONLY when exitstatus == 0 (all tests pass).
# - PROJECT_ROOT resolves to the absolute path of flows/brainstorm/ regardless of cwd.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why a separate counter file from tests/server/.test_counter.json?
# A: Deployment tests fail for different reasons (Docker/k8s tooling absent, manifest
#    shape errors) than server tests (logic bugs). Mixing counters would cause false
#    escalations. Per plan §5.2, each slice has its own counter.
# Q: Why provide PROJECT_ROOT as a fixture rather than a module-level constant?
# A: Fixtures are injectable into test functions and conftest fixtures downstream.
#    Module constants would require an import statement in each test file. The fixture
#    pattern is cleaner and consistent with the rest of the test suite.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice D.]
# END_CHANGE_SUMMARY

import json
import pathlib

import pytest

# Resolve project root from this file's location:
# tests/deployment/conftest.py -> tests/deployment/ -> tests/ -> brainstorm/ (root)
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_COUNTER_FILE = _PROJECT_ROOT / "tests" / "deployment" / ".test_counter.json"

# ---------------------------------------------------------------------------
# Anti-Loop Protocol — session hooks
# ---------------------------------------------------------------------------


def _read_counter() -> dict:
    """Read the attempt counter JSON, returning defaults if the file is absent or corrupt."""
    if _COUNTER_FILE.exists():
        try:
            return json.loads(_COUNTER_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {"failures": 0}
    return {"failures": 0}


def _write_counter(data: dict) -> None:
    """Persist the counter JSON to disk."""
    _COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COUNTER_FILE.write_text(json.dumps(data, indent=2))


def pytest_sessionstart(session) -> None:
    """
    Anti-Loop Protocol: read and display the current attempt counter at session start.
    Outputs a CHECKLIST when failures > 0 to help debug repeated failures.
    """
    counter = _read_counter()
    failures = counter.get("failures", 0)

    print(f"\n[AntiLoop][deployment] Attempt #{failures + 1}")

    if failures == 1:
        print("\n[AntiLoop] CHECKLIST (Attempt 2 — common errors):")
        print("  [ ] jsonschema library not installed — tests use structural validator (no jsonschema dep)")
        print("  [ ] pyyaml not available — check: python3.12 -c 'import yaml'")
        print("  [ ] Dockerfile path resolution — tests use __file__.resolve().parents[2]")
        print("  [ ] k8s/ YAML files missing — check ls k8s/")
        print("  [ ] kubectl not on PATH — k8s tests auto-skip if absent")
        print("  [ ] manifest.json invalid JSON — validate with python3.12 -m json.tool")

    if failures == 2:
        print("\n[AntiLoop] Attempt 3 — Use WebSearch or Context 7 MCP to find a solution online.")

    if failures == 3:
        print("\n[AntiLoop] WARNING: Looping risk! Pause and reflect. Are you repeating a failed strategy? Consider alternatives (Superposition).")

    if failures >= 4:
        print("\n[AntiLoop] CRITICAL ERROR: Agent looping detected. STOP. Formulate a help request for the operator.")


def pytest_sessionfinish(session, exitstatus) -> None:
    """
    Anti-Loop Protocol: update the counter at session end.
    Reset to 0 on full PASS; increment on any failure.
    """
    counter = _read_counter()
    if exitstatus == 0:
        counter["failures"] = 0
    else:
        counter["failures"] = counter.get("failures", 0) + 1
    _write_counter(counter)
    print(f"\n[AntiLoop][deployment] Session finished with exitstatus={exitstatus}. "
          f"failures={counter['failures']}")


# ---------------------------------------------------------------------------
# Shared path fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def project_root() -> pathlib.Path:
    """
    Absolute path to the brainstorm project root (flows/brainstorm/).
    Resolved relative to this conftest file — independent of pytest invocation cwd.
    """
    return _PROJECT_ROOT


@pytest.fixture(scope="session")
def k8s_dir(project_root: pathlib.Path) -> pathlib.Path:
    """Absolute path to k8s/ directory inside project root."""
    return project_root / "k8s"


@pytest.fixture(scope="session")
def dockerfile_path(project_root: pathlib.Path) -> pathlib.Path:
    """Absolute path to the Dockerfile in project root."""
    return project_root / "Dockerfile"


@pytest.fixture(scope="session")
def manifest_path(k8s_dir: pathlib.Path) -> pathlib.Path:
    """Absolute path to k8s/brainstorm.manifest.json."""
    return k8s_dir / "brainstorm.manifest.json"
