# FILE: tests/server/test_zero_knowledge_gate.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Precision tests for scripts/verify_zero_knowledge.sh — the structural
#          gate that enforces AC1 (no user identity leakage) and AC7 (no billing
#          vocabulary) inside src/server/.
# SCOPE:   Shell script behaviour: positive (violation detected → exit 1) and
#          negative (clean code → exit 0) test cases.
# INPUT:   Temporary .py files written to src/server/ during each test, cleaned up
#          unconditionally in try/finally blocks.
# OUTPUT:  pytest pass/fail for the 3 required gate-precision cases plus 2 bonus cases.
# KEYWORDS: [DOMAIN(9): ZeroKnowledge; CONCEPT(9): ShellGateVerification;
#            TECH(8): subprocess; PATTERN(8): TryFinally; CONCEPT(7): FileSideEffect]
# LINKS: [CALLS_SCRIPT(10): scripts/verify_zero_knowledge.sh;
#         READS_DATA_FROM(6): src/server/_*_tmp.py (transient)]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §1.5 AC1, AC7; §5.5; §9
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why does this module use subprocess.run when business-logic tests are forbidden from it?
# A: This file tests a SHELL SCRIPT (scripts/verify_zero_knowledge.sh), not Python business
#    logic. The only correct way to verify that a shell script exits with the expected code
#    is to invoke it via subprocess. This is a structural/integration gate test, not a unit
#    test of Python code. The exception to the "no subprocess.run for business logic" rule
#    is explicitly documented in the Architect's Slice A' scope definition and in the
#    module RATIONALE here. Any future agent refactoring this file must preserve this
#    distinction — do NOT replace subprocess.run with direct Python grep equivalents,
#    because that would test the regex in Python isolation, not the actual shell script.
# Q: Why are temp files written to src/server/ rather than tmp_path?
# A: The shell script specifically scans src/server/*.py. Files written to tmp_path are
#    outside that scan perimeter and would produce false-negative test results. The temp
#    files use a distinct prefix (_bad_tmp.py, _ok_tmp.py) and are always removed in
#    finally blocks to avoid polluting the real codebase between test runs.
# Q: Why use try/finally rather than a pytest fixture for cleanup?
# A: try/finally guarantees cleanup even if pytest itself is interrupted between the
#    assertion and any teardown fixture. It is also more readable for this specific
#    pattern where each test owns exactly one temp file.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice A': precision tests for the
#               verify_zero_knowledge.sh gate with subprocess.run exception documented.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 10 [AC7: creates credits assignment; expects exit 1] => test_zk_gate_flags_energy_field
# FUNC 10 [AC7 neg: "balanced" in comment; expects exit 0] => test_zk_gate_allows_balanced_comment
# FUNC 10 [AC1: claims.user_id field access; expects exit 1] => test_zk_gate_flags_user_id_access
# FUNC  8 [AC7 bonus: ruble literal; expects exit 1]        => test_zk_gate_flags_ruble_literal
# FUNC  8 [AC7 bonus: stars + deposit vocab; expects exit 1] => test_zk_gate_flags_star_deposit
# END_MODULE_MAP
#
# START_USE_CASES:
# - [test_zk_gate_flags_energy_field]: CI gate -> injects billing violation -> assert exit 1
# - [test_zk_gate_allows_balanced_comment]: CI gate -> injects clean comment -> assert exit 0
# - [test_zk_gate_flags_user_id_access]: CI gate -> injects identity leak -> assert exit 1
# END_USE_CASES

import subprocess
from pathlib import Path

import pytest

# Brainstorm project root — two levels up from tests/server/
_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent

# Absolute path to the gate script
_ZK_SCRIPT = _BRAINSTORM_ROOT / "scripts" / "verify_zero_knowledge.sh"

# All temp files land inside src/server/ so the script's scan perimeter covers them.
# Prefix "_" + suffix "_tmp.py" makes them visually distinct in git status.
_SERVER_DIR = _BRAINSTORM_ROOT / "src" / "server"


# START_FUNCTION_test_zk_gate_flags_energy_field
# START_CONTRACT:
# PURPOSE: Verify that AC7 billing vocabulary guard fires correctly when a Python file
#          containing a billing-domain word (credits = 100) is placed in src/server/.
#          This is the primary positive case for the billing vocabulary check.
# INPUTS:
#   - (no pytest fixtures; cleanup is manual via try/finally)
# OUTPUTS:
#   - Assertion: shell script exits with code 1 when billing term detected.
# SIDE_EFFECTS: Creates and unconditionally deletes src/server/_bad_tmp.py.
# KEYWORDS: [PATTERN(9): PositiveViolationTest; CONCEPT(9): AC7BillingVocab;
#            TECH(8): subprocess; PATTERN(8): TryFinally]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_zk_gate_flags_energy_field() -> None:
    """
    Creates a temporary file in src/server/ containing `credits = 100` — a clear
    AC7 billing-vocabulary violation — then runs verify_zero_knowledge.sh and
    asserts that it exits with code 1 (violation detected). The file is deleted
    unconditionally in a finally block regardless of whether the assertion passes
    or fails, preventing test pollution of the real src/server/ directory.
    """

    tmp_file = _SERVER_DIR / "_bad_tmp.py"

    # START_BLOCK_SETUP: [Write violation file to src/server/]
    try:
        tmp_file.write_text("# Intentional AC7 violation for gate test\ncredits = 100\n")
        print(f"\n[ZK_GATE_TEST][IMP:8][test_zk_gate_flags_energy_field][SETUP] "
              f"Created {tmp_file} with 'credits = 100' [OK]")
        # END_BLOCK_SETUP

        # START_BLOCK_RUN_GATE: [Execute shell script and capture result]
        result = subprocess.run(
            [str(_ZK_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(_BRAINSTORM_ROOT),
            timeout=10,
        )
        print(f"[ZK_GATE_TEST][IMP:9][test_zk_gate_flags_energy_field][RUN_GATE] "
              f"Script exit code: {result.returncode} | "
              f"stdout: {result.stdout.strip()!r} [BELIEF: expect 1]")
        # END_BLOCK_RUN_GATE

        # START_BLOCK_ASSERT: [Verify gate detected the violation]
        assert result.returncode == 1, (
            f"ZK gate should exit 1 when billing term 'credits' is present in src/server/, "
            f"but got exit {result.returncode}. stdout={result.stdout!r}"
        )
        # END_BLOCK_ASSERT

    finally:
        # START_BLOCK_CLEANUP: [Unconditional temp file removal]
        if tmp_file.exists():
            tmp_file.unlink()
            print(f"[ZK_GATE_TEST][IMP:7][test_zk_gate_flags_energy_field][CLEANUP] "
                  f"Deleted {tmp_file} [OK]")
        # END_BLOCK_CLEANUP
# END_FUNCTION_test_zk_gate_flags_energy_field


# START_FUNCTION_test_zk_gate_allows_balanced_comment
# START_CONTRACT:
# PURPOSE: Verify that the word "balanced" appearing in a Python comment does NOT trigger
#          the AC7 billing vocabulary guard. This is the primary negative case — the exact
#          false positive that existed in src/core/json_utils.py and motivated v2.0.0.
# INPUTS:
#   - (no pytest fixtures; cleanup is manual via try/finally)
# OUTPUTS:
#   - Assertion: shell script exits with code 0 (clean) when "balanced" is in a comment.
# SIDE_EFFECTS: Creates and unconditionally deletes src/server/_ok_tmp.py.
# KEYWORDS: [PATTERN(9): NegativeGateTest; CONCEPT(9): FalsePositivePrevention;
#            CONCEPT(8): WordBoundaryRegex; TECH(8): subprocess]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_zk_gate_allows_balanced_comment() -> None:
    """
    Creates a temporary file in src/server/ containing a Python comment with the word
    "balanced" — the exact pattern that produced the false positive in src/core/json_utils.py
    under the old script. Runs verify_zero_knowledge.sh and asserts exit code 0 (clean).
    The word "balanced" is not in the billing vocabulary term list; it only contains
    "balance" (and "balances" with the plural suffix). Word-boundary matching ensures
    "balanced" does not match the \bbalances?\b pattern.
    """

    tmp_file = _SERVER_DIR / "_ok_tmp.py"

    # START_BLOCK_SETUP: [Write clean comment file to src/server/]
    try:
        tmp_file.write_text(
            "# balanced whitespace around braces keeps the parser happy\n"
            "def parse_block(text: str) -> dict:\n"
            "    return {}\n"
        )
        print(f"\n[ZK_GATE_TEST][IMP:8][test_zk_gate_allows_balanced_comment][SETUP] "
              f"Created {tmp_file} with 'balanced' in comment [OK]")
        # END_BLOCK_SETUP

        # START_BLOCK_RUN_GATE: [Execute shell script and capture result]
        result = subprocess.run(
            [str(_ZK_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(_BRAINSTORM_ROOT),
            timeout=10,
        )
        print(f"[ZK_GATE_TEST][IMP:9][test_zk_gate_allows_balanced_comment][RUN_GATE] "
              f"Script exit code: {result.returncode} | "
              f"stdout: {result.stdout.strip()!r} [BELIEF: expect 0 — word boundary guards 'balanced']")
        # END_BLOCK_RUN_GATE

        # START_BLOCK_ASSERT: [Verify gate is not triggered by 'balanced' comment]
        assert result.returncode == 0, (
            f"ZK gate must NOT fire on the word 'balanced' in a comment — "
            f"this was the false positive fixed in v2.0.0. "
            f"Got exit {result.returncode}. stdout={result.stdout!r}"
        )
        # END_BLOCK_ASSERT

    finally:
        # START_BLOCK_CLEANUP: [Unconditional temp file removal]
        if tmp_file.exists():
            tmp_file.unlink()
            print(f"[ZK_GATE_TEST][IMP:7][test_zk_gate_allows_balanced_comment][CLEANUP] "
                  f"Deleted {tmp_file} [OK]")
        # END_BLOCK_CLEANUP
# END_FUNCTION_test_zk_gate_allows_balanced_comment


# START_FUNCTION_test_zk_gate_flags_user_id_access
# START_CONTRACT:
# PURPOSE: Verify that AC1 identity-leakage guard fires when Python code in src/server/
#          accesses user_id as a field on a claims object (claims.user_id).
#          This is the primary positive case for the AC1 user-identity check.
# INPUTS:
#   - (no pytest fixtures; cleanup is manual via try/finally)
# OUTPUTS:
#   - Assertion: shell script exits with code 1 when claims.user_id field access detected.
# SIDE_EFFECTS: Creates and unconditionally deletes src/server/_user_id_tmp.py.
# KEYWORDS: [PATTERN(9): PositiveViolationTest; CONCEPT(9): AC1IdentityLeak;
#            TECH(8): subprocess; CONCEPT(8): PreciseAC1Pattern]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_zk_gate_flags_user_id_access() -> None:
    """
    Creates a temporary file in src/server/ containing `claims.user_id` — a field
    access pattern that represents an AC1 identity leakage violation. Runs
    verify_zero_knowledge.sh and asserts exit code 1 (violation detected).
    Note: this pattern is distinct from auth.py's own enforcement code
    raw.pop("user_id", None) which does NOT trigger the gate, because the AC1
    pattern uses `.user_id` anchor to catch field access specifically.
    """

    tmp_file = _SERVER_DIR / "_user_id_tmp.py"

    # START_BLOCK_SETUP: [Write AC1 violation file to src/server/]
    try:
        tmp_file.write_text(
            "# Intentional AC1 violation for gate test\n"
            "def get_identity(claims):\n"
            "    uid = claims.user_id  # leaking identity — AC1 violation\n"
            "    return uid\n"
        )
        print(f"\n[ZK_GATE_TEST][IMP:8][test_zk_gate_flags_user_id_access][SETUP] "
              f"Created {tmp_file} with 'claims.user_id' field access [OK]")
        # END_BLOCK_SETUP

        # START_BLOCK_RUN_GATE: [Execute shell script and capture result]
        result = subprocess.run(
            [str(_ZK_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(_BRAINSTORM_ROOT),
            timeout=10,
        )
        print(f"[ZK_GATE_TEST][IMP:9][test_zk_gate_flags_user_id_access][RUN_GATE] "
              f"Script exit code: {result.returncode} | "
              f"stdout: {result.stdout.strip()!r} [BELIEF: expect 1]")
        # END_BLOCK_RUN_GATE

        # START_BLOCK_ASSERT: [Verify gate detected the AC1 violation]
        assert result.returncode == 1, (
            f"ZK gate should exit 1 when 'claims.user_id' field access is present in src/server/, "
            f"but got exit {result.returncode}. stdout={result.stdout!r}"
        )
        # END_BLOCK_ASSERT

    finally:
        # START_BLOCK_CLEANUP: [Unconditional temp file removal]
        if tmp_file.exists():
            tmp_file.unlink()
            print(f"[ZK_GATE_TEST][IMP:7][test_zk_gate_flags_user_id_access][CLEANUP] "
                  f"Deleted {tmp_file} [OK]")
        # END_BLOCK_CLEANUP
# END_FUNCTION_test_zk_gate_flags_user_id_access


# START_FUNCTION_test_zk_gate_flags_ruble_literal
# START_CONTRACT:
# PURPOSE: Bonus test — verify that the domain-specific monetary unit "ruble" triggers
#          the AC7 billing vocabulary guard. Tests CrabLink-ecosystem-specific terms
#          beyond common English billing words.
# INPUTS:
#   - (no pytest fixtures; cleanup is manual via try/finally)
# OUTPUTS:
#   - Assertion: shell script exits with code 1 when "ruble" appears in src/server/.
# SIDE_EFFECTS: Creates and unconditionally deletes src/server/_ruble_tmp.py.
# KEYWORDS: [PATTERN(8): BonusViolationTest; CONCEPT(8): DomainSpecificVocab;
#            TECH(7): subprocess]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_zk_gate_flags_ruble_literal() -> None:
    """
    Creates a temporary file in src/server/ containing the string "ruble" — a
    CrabLink-ecosystem monetary unit that must never appear in the server layer.
    Runs verify_zero_knowledge.sh and asserts exit code 1.
    """

    tmp_file = _SERVER_DIR / "_ruble_tmp.py"

    # START_BLOCK_SETUP_AND_RUN: [Write violation file, run gate, assert, clean]
    try:
        tmp_file.write_text("price_in_ruble = 500  # billing leak\n")
        print(f"\n[ZK_GATE_TEST][IMP:8][test_zk_gate_flags_ruble_literal][SETUP] "
              f"Created {tmp_file} with 'ruble' [OK]")

        result = subprocess.run(
            [str(_ZK_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(_BRAINSTORM_ROOT),
            timeout=10,
        )
        print(f"[ZK_GATE_TEST][IMP:9][test_zk_gate_flags_ruble_literal][RUN_GATE] "
              f"Exit: {result.returncode} [BELIEF: expect 1]")

        assert result.returncode == 1, (
            f"ZK gate should exit 1 for 'ruble' term, got {result.returncode}."
        )
    finally:
        if tmp_file.exists():
            tmp_file.unlink()
    # END_BLOCK_SETUP_AND_RUN
# END_FUNCTION_test_zk_gate_flags_ruble_literal


# START_FUNCTION_test_zk_gate_flags_star_deposit
# START_CONTRACT:
# PURPOSE: Bonus test — verify that the domain-specific terms "stars" and "deposit"
#          trigger the AC7 billing vocabulary guard. Tests two more CrabLink-ecosystem
#          monetary/payment vocabulary items.
# INPUTS:
#   - (no pytest fixtures; cleanup is manual via try/finally)
# OUTPUTS:
#   - Assertion: shell script exits with code 1 when "stars" and "deposit" appear.
# SIDE_EFFECTS: Creates and unconditionally deletes src/server/_stars_tmp.py.
# KEYWORDS: [PATTERN(8): BonusViolationTest; CONCEPT(8): DomainSpecificVocab;
#            TECH(7): subprocess]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_zk_gate_flags_star_deposit() -> None:
    """
    Creates a temporary file in src/server/ containing both "stars" and "deposit" —
    CrabLink payment vocabulary that must not appear in the server layer.
    Runs verify_zero_knowledge.sh and asserts exit code 1.
    """

    tmp_file = _SERVER_DIR / "_stars_tmp.py"

    # START_BLOCK_SETUP_AND_RUN: [Write violation file, run gate, assert, clean]
    try:
        tmp_file.write_text(
            "user_stars = 50  # stars balance\n"
            "def deposit_stars(amount: int) -> None: ...\n"
        )
        print(f"\n[ZK_GATE_TEST][IMP:8][test_zk_gate_flags_star_deposit][SETUP] "
              f"Created {tmp_file} with 'stars' and 'deposit' [OK]")

        result = subprocess.run(
            [str(_ZK_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(_BRAINSTORM_ROOT),
            timeout=10,
        )
        print(f"[ZK_GATE_TEST][IMP:9][test_zk_gate_flags_star_deposit][RUN_GATE] "
              f"Exit: {result.returncode} [BELIEF: expect 1]")

        assert result.returncode == 1, (
            f"ZK gate should exit 1 for 'stars'/'deposit' terms, got {result.returncode}."
        )
    finally:
        if tmp_file.exists():
            tmp_file.unlink()
    # END_BLOCK_SETUP_AND_RUN
# END_FUNCTION_test_zk_gate_flags_star_deposit
