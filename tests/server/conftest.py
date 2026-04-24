# FILE: tests/server/conftest.py
# VERSION: 1.1.0
# START_MODULE_CONTRACT:
# PURPOSE: Shared pytest fixtures and session hooks for the server test suite.
#          Anti-Loop Protocol: session-scoped hooks manage tests/server/.test_counter.json
#          (separate from tests/ui/.test_counter.json — per plan §5.2).
#          Provides shared fixtures: signed_token_factory, token_secret, fixed_now,
#          and calls scripts/verify_zero_knowledge.sh in pytest_sessionstart.
# SCOPE: Anti-Loop counter management (pytest_sessionstart/finish); shared token fixtures;
#        zero-knowledge shell check; sys.path injection for clean src imports.
# INPUT: pytest session lifecycle events, environment variables.
# OUTPUT: .test_counter.json maintained per session; shared fixtures injected into tests.
# KEYWORDS: [DOMAIN(8): TestInfra; CONCEPT(9): AntiLoop; PATTERN(8): SessionHook;
#            CONCEPT(9): TokenFixture; CONCEPT(8): ZeroKnowledgeCheck]
# LINKS: [USES_API(7): pytest; READS_DATA_FROM(6): tests/server/.test_counter.json;
#         CALLS_SCRIPT(8): scripts/verify_zero_knowledge.sh]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §5.2 (Anti-Loop), §5.3 (Go-compat fixture)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Counter file is tests/server/.test_counter.json (NOT shared with tests/ui/).
# - Counter increment ONLY in pytest_sessionfinish hook, NEVER in test bodies.
# - Counter resets to 0 ONLY when exitstatus == 0 (all tests pass).
# - verify_zero_knowledge.sh is called in pytest_sessionstart; failure aborts session.
# - signed_token_factory generates tokens byte-for-byte compatible with Go Issue().
# - fixed_now is set to base_now from fixtures (1735689600) to match Go fixture exp values.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why call verify_zero_knowledge.sh in pytest_sessionstart rather than a separate test?
# A: Zero-knowledge invariants (AC1, AC7) must fail the ENTIRE session if violated.
#    A test would only fail one test function; a session hook aborts all collection.
#    This mirrors the plan §5.5 specification exactly.
# Q: Why is signed_token_factory a fixture returning a callable (factory pattern)?
# A: Multiple tests need tokens with different parameters. A factory allows parameterised
#    token creation without fixture scope explosion. The secret bytes are shared via the
#    token_secret fixture, ensuring consistent signing across tests.
# Q: Why is fixed_now set to base_now (1735689600) rather than time.time()?
# A: The Go-generated tokens have exp = base_now + 3600 = 1735693200. Tests using those
#    tokens must supply a fixed_now < exp or the verifier returns "expired". freezegun
#    is used in test_auth_go_compat.py to freeze time at fixed_now.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.1.0 - Slice A': Restored strict ZK check in pytest_sessionstart.
#               Removed workaround NOTE block that suppressed exit-1 from the old script.
#               Gate now correctly exits 0 (scope narrowed to src/server/ in script v2.0.0).]
# PREV_CHANGE_SUMMARY: [v1.0.0 - Initial creation as Slice A: Anti-Loop hooks, token factory,
#               zero-knowledge shell check, ldd_capture fixture.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 8 [pytest session start: load counter, print checklist, run ZK check] => pytest_sessionstart
# FUNC 8 [pytest session finish: update counter based on pass/fail] => pytest_sessionfinish
# FUNC 8 [Fixture factory: creates signed v1 tokens for testing] => signed_token_factory
# FUNC 6 [Fixture: shared HMAC secret bytes] => token_secret
# FUNC 6 [Fixture: fixed Unix timestamp matching Go fixture base_now] => fixed_now
# FUNC 7 [Fixture: filters caplog for IMP:7-10 and prints trajectory] => ldd_capture
# END_MODULE_MAP
#
# START_USE_CASES:
# - [signed_token_factory]: test_auth.py -> factory(session_id, exp) -> Bearer token string
# - [fixed_now]: test_auth_go_compat.py -> freeze time at 1735689600 -> match Go fixture exp
# - [pytest_sessionstart]: session start -> run ZK check -> abort on violation
# END_USE_CASES

import base64
import hashlib
import hmac as hmac_module
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List

import pytest

# Counter file lives in tests/server/ (separate from tests/ui/.test_counter.json)
_COUNTER_FILE = Path(__file__).parent / ".test_counter.json"

# Brainstorm root for sys.path injection
_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

# Script path for zero-knowledge check
_ZK_SCRIPT = _BRAINSTORM_ROOT / "scripts" / "verify_zero_knowledge.sh"

# Go fixture base_now constant — must match the Go generator
FIXTURE_BASE_NOW: int = 1735689600
# Go fixture future exp = base_now + 3600
FIXTURE_FUTURE_EXP: int = FIXTURE_BASE_NOW + 3600
# Deterministic test secret — must match the Go generator
_TEST_SECRET_STR: str = "brainstorm-test-secret-32bytes!!"


# START_FUNCTION_pytest_sessionstart
# START_CONTRACT:
# PURPOSE: Load attempt counter, print Anti-Loop checklist if prior failures exist,
#          and run scripts/verify_zero_knowledge.sh to enforce AC1/AC7 invariants.
#          If ZK check fails, the entire session is aborted before collection begins.
# INPUTS:
#   - pytest session object => session: pytest.Session
# OUTPUTS: None (side effects: prints checklist, may sys.exit on ZK violation)
# SIDE_EFFECTS: Reads .test_counter.json; runs shell script; prints to stdout.
# KEYWORDS: [PATTERN(9): AntiLoop; CONCEPT(9): ZeroKnowledgeCheck; PATTERN(8): SessionHook]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def pytest_sessionstart(session) -> None:
    """
    Session start hook for tests/server/. Reads .test_counter.json to determine
    cumulative failure count and prints the Anti-Loop checklist with server-specific
    items. Also runs verify_zero_knowledge.sh to enforce AC1/AC7 structural invariants
    before any test executes.
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
        print(f"ANTI-LOOP PROTOCOL — tests/server/ — Attempt #{failures + 1}")
        print(f"{'='*70}")
        print("CHECKLIST (common server test failure causes):")
        print("  [ ] Import error: is brainstorm root in sys.path? (conftest adds it)")
        print("  [ ] BRAINSTORM_HMAC_SECRET env var not set in test environment")
        print("  [ ] GATEWAY_LLM_PROXY_URL env var not set (required by Config)")
        print("  [ ] GATEWAY_LLM_API_KEY env var not set (required by Config)")
        print("  [ ] TokenClaims must have EXACTLY 3 fields: service_id, session_id, exp")
        print("  [ ] verify_session_token must pop user_id and iat from raw payload")
        print("  [ ] hmac.compare_digest used (not ==) for signature comparison")
        print("  [ ] base64url pad-repair: (-len(s)) % 4 appended '=' chars")
        print("  [ ] AuthError reason must be in valid taxonomy (6 values only)")
        print("  [ ] Go fixture tokens use base_now=1735689600 — set fixed_now correctly")
        print("  [ ] lru_cache on get_cfg() — call get_cfg.cache_clear() between tests")
        print("  [ ] conftest env fixtures must set ALL required Config env vars")
        print("  [ ] Test imports use 'src.server.auth' not relative imports")
        print("  [ ] verify_zero_knowledge.sh must be chmod +x")
        print("  [ ] IMP:9 logs must appear — set caplog level to logging.DEBUG or INFO")

    if failures == 2:
        print("\nAttempt 3: Use WebSearch or Context 7 MCP to find a solution online.")

    if failures == 3:
        print("\nWARNING: Looping risk! Pause and reflect. Are you repeating a failed strategy? Consider alternatives (Superposition).")

    if failures >= 4:
        print("\nCRITICAL ERROR: Agent looping detected. STOP. Formulate a help request for the operator.")
    # END_BLOCK_CHECKLIST

    # START_BLOCK_ZK_CHECK: [Run verify_zero_knowledge.sh — strict enforcement of AC1/AC7]
    # Script v2.0.0 scopes the scan to src/server/ only and uses word-boundary regex for
    # billing vocabulary, eliminating the false positive on "balanced" in src/core/.
    # A non-zero exit here is a genuine AC1/AC7 violation — abort the session.
    if _ZK_SCRIPT.exists():
        try:
            result = subprocess.run(
                [str(_ZK_SCRIPT)],
                capture_output=True,
                text=True,
                cwd=str(_BRAINSTORM_ROOT),
            )
            if result.returncode != 0:
                print(f"\n[ZK_CHECK][FAIL] AC1/AC7 violation detected in src/server/:")
                print(result.stdout)
                print(result.stderr)
                raise SystemExit(
                    "[ZK_CHECK] Session aborted: R4 zero-knowledge invariant violated. "
                    "Fix the violation in src/server/ before running tests."
                )
            else:
                print(f"\n[ZK_CHECK] {result.stdout.strip()}")
        except SystemExit:
            raise
        except Exception as exc:
            print(f"\n[ZK_CHECK] WARNING: Could not run verify_zero_knowledge.sh: {exc}")
    else:
        print(f"\n[ZK_CHECK] WARNING: Script not found at {_ZK_SCRIPT} — skipping check")
    # END_BLOCK_ZK_CHECK

# END_FUNCTION_pytest_sessionstart


# START_FUNCTION_pytest_sessionfinish
# START_CONTRACT:
# PURPOSE: Update tests/server/.test_counter.json after the session ends.
#          Reset to 0 on 100% PASS, increment on any failure.
# INPUTS:
#   - pytest session object => session: pytest.Session
#   - Exit code from pytest run => exitstatus: int
# OUTPUTS: None (side effect: writes .test_counter.json)
# KEYWORDS: [PATTERN(9): AntiLoop; CONCEPT(8): SessionHook]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def pytest_sessionfinish(session, exitstatus: int) -> None:
    """
    Session finish hook. Updates .test_counter.json:
    - exitstatus == 0: resets failures to 0.
    - exitstatus != 0: increments failures by 1.
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
        print(f"\n[AntiLoop] tests/server/ — All tests PASSED. Counter reset to 0.")
    else:
        counter_data["failures"] = counter_data.get("failures", 0) + 1
        print(f"\n[AntiLoop] tests/server/ — Failures detected. Counter: {counter_data['failures']}")
    # END_BLOCK_UPDATE_COUNTER

    # START_BLOCK_WRITE_COUNTER: [Persist counter to file]
    try:
        with open(_COUNTER_FILE, "w") as f:
            json.dump(counter_data, f)
    except OSError as exc:
        print(f"[AntiLoop][WARNING] Could not write counter file: {exc}")
    # END_BLOCK_WRITE_COUNTER

# END_FUNCTION_pytest_sessionfinish


# START_FUNCTION_token_secret
# START_CONTRACT:
# PURPOSE: Fixture providing the shared HMAC secret bytes used across all server tests.
#          Must match the Go fixture generator secret ("brainstorm-test-secret-32bytes!!").
# OUTPUTS:
#   - bytes: secret bytes for HMAC signing/verification
# KEYWORDS: [CONCEPT(8): SharedTestSecret; PATTERN(7): Fixture]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
@pytest.fixture
def token_secret() -> bytes:
    """
    Shared HMAC secret bytes for server tests. Matches the Go generator secret
    "brainstorm-test-secret-32bytes!!" (32 bytes ASCII). Used by signed_token_factory
    and directly in tests verifying HMAC behaviour.
    """
    return _TEST_SECRET_STR.encode("utf-8")
# END_FUNCTION_token_secret


# START_FUNCTION_fixed_now
# START_CONTRACT:
# PURPOSE: Fixture providing the fixed Unix timestamp matching the Go fixture base_now.
#          Tests using Go-generated tokens must supply this as the 'now' parameter to
#          verify_session_token so that future exp values are not in the past.
# OUTPUTS:
#   - int: Unix timestamp 1735689600 (2025-01-01T00:00:00Z)
# KEYWORDS: [CONCEPT(8): FixedTime; PATTERN(7): DeterministicFixture]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
@pytest.fixture
def fixed_now() -> int:
    """
    Fixed Unix timestamp = 1735689600 (2025-01-01T00:00:00Z).
    Go fixture tokens have exp = 1735693200 (base_now + 3600).
    Supplying fixed_now = 1735689600 ensures exp > now (token not expired).
    """
    return FIXTURE_BASE_NOW
# END_FUNCTION_fixed_now


# START_FUNCTION_signed_token_factory
# START_CONTRACT:
# PURPOSE: Fixture factory that creates properly-signed v1 session tokens for testing.
#          Produces tokens byte-for-byte compatible with Go Issue() by using the same
#          wire format: "v1.<base64url(payload_json)>.<base64url(hmac_sha256)>".
# INPUTS (via token_secret, fixed_now fixtures):
#   - token_secret: bytes — shared HMAC secret
# OUTPUTS:
#   - Callable: factory(service_id, session_id, exp, secret=None) -> "Bearer v1.<p>.<s>"
# KEYWORDS: [PATTERN(9): TokenFactory; CONCEPT(8): GoCompatWireFormat; PATTERN(7): Fixture]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
@pytest.fixture
def signed_token_factory(token_secret: bytes) -> Callable:
    """
    Factory fixture producing "Bearer v1.<payload>.<sig>" tokens via the same algorithm
    as Go's sessiontoken.Issue(). Payload JSON uses snake_case keys matching Go Claims
    struct tags. Signature is HMAC-SHA256 over the raw payload bytes.
    Base64 encoding uses RawURLEncoding (no padding) as in Go.

    Usage in tests:
        bearer = signed_token_factory("brainstorm", session_uuid, exp_timestamp)
        # -> "Bearer v1.<base64url(json)>.<base64url(hmac)>"
    """

    def _factory(
        service_id: str,
        session_id: str,
        exp: int,
        user_id: int = 999,
        secret: bytes | None = None,
    ) -> str:
        """
        Create a signed Bearer token for the given claims. Mirrors Go Issue() exactly:
        1. Build claims dict with snake_case keys (user_id, service_id, session_id, exp).
        2. json.dumps with separators=(",", ":") — compact form matches Go json.Marshal.
        3. HMAC-SHA256 over payload bytes.
        4. Base64 RawURL-encode both (no padding) and join with ".".
        5. Prepend "Bearer v1.".
        """
        used_secret = secret if secret is not None else token_secret

        # Go json.Marshal produces compact JSON with no extra spaces
        payload_dict = {
            "user_id": user_id,
            "service_id": service_id,
            "session_id": session_id,
            "exp": exp,
        }
        payload_json = json.dumps(payload_dict, separators=(",", ":"))
        payload_bytes = payload_json.encode("utf-8")

        # HMAC-SHA256 exactly as Go's hmac.New(sha256.New, secret) + mac.Write(payload)
        mac = hmac_module.new(used_secret, payload_bytes, "sha256")
        sig_bytes = mac.digest()

        # base64.RawURLEncoding = urlsafe without padding (same as Go)
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
        sig_b64 = base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode("ascii")

        return f"Bearer v1.{payload_b64}.{sig_b64}"

    return _factory
# END_FUNCTION_signed_token_factory


# START_FUNCTION_ldd_capture
# START_CONTRACT:
# PURPOSE: Fixture helper that filters caplog records for IMP:7-10 and prints them
#          to stdout for LDD telemetry output. Returns filtered list for assertion use.
# INPUTS:
#   - caplog: pytest.LogCaptureFixture
# OUTPUTS:
#   - Callable: accepts optional log records, returns List[str] of IMP >= 7 messages
# KEYWORDS: [CONCEPT(8): LDDTelemetry; PATTERN(7): CaplogFilter]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.fixture
def ldd_capture(caplog):
    """
    Fixture helper for LDD telemetry. Filters caplog records by IMP level and prints
    them to stdout. Returns a callable that produces the filtered list for assertions.

    Usage: high_imp = ldd_capture()  # called AFTER business logic runs
    """

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

    return capture
# END_FUNCTION_ldd_capture


# START_FUNCTION_server_env
# START_CONTRACT:
# PURPOSE: Fixture that sets all required environment variables for Config construction.
#          Yields control to the test, then restores original env. Prevents test pollution.
# OUTPUTS: dict with the env vars set
# KEYWORDS: [PATTERN(8): EnvFixture; CONCEPT(7): TestIsolation]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.fixture
def server_env(monkeypatch):
    """
    Set required environment variables for Config validation in tests.
    All three required fields are set to deterministic test values.
    lru_cache on get_cfg() is cleared before and after each test.
    """
    from src.server.config import get_cfg  # noqa: PLC0415

    # Clear cached config before test
    get_cfg.cache_clear()

    env_vars = {
        "BRAINSTORM_HMAC_SECRET": _TEST_SECRET_STR,
        "GATEWAY_LLM_PROXY_URL": "https://test-llm-proxy.example.com/v1",
        "GATEWAY_LLM_API_KEY": "test-api-key-for-unit-tests",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    yield env_vars

    # Clear cached config after test to prevent leakage between tests
    get_cfg.cache_clear()

# END_FUNCTION_server_env
