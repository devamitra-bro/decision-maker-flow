# FILE: src/features/decision_maker/tests/test_json_utils.py
# VERSION: 1.1.0
# START_MODULE_CONTRACT:
# PURPOSE: Test suite for safe_json_parse() covering all input modes (AC8) including
#          reasoning-model prefix normalisation (Strategy 0, v1.1.0).
# SCOPE: Plain JSON, ```json fenced, bare ``` fenced, prose-embedded; reasoning-block prefixes
#        (<thinking>, <think>, <reasoning>, <reflection>); malformed raises JsonParseError;
#        raw_snippet preservation test confirms ORIGINAL input survives normalisation.
# INPUT: Synthetic raw strings mimicking LLM output. No LLM calls. No file I/O.
# OUTPUT: pytest pass/fail assertions + IMP:7-10 LDD trajectory printed to stdout.
# KEYWORDS: [DOMAIN(8): Tests; CONCEPT(9): JSONParsing; PATTERN(8): ParametrizedTest;
#            PATTERN(7): AtomicTest; CONCEPT(10): ReasoningModelNormalization]
# LINKS: [READS_DATA_FROM(10): src.core.json_utils.safe_json_parse;
#         READS_DATA_FROM(9): src.core.json_utils.JsonParseError]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §4 Test Matrix row 4; AC8, §5.7
# END_MODULE_CONTRACT:
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.1.0 - Added 5 reasoning-block regression cases + 1 snippet-preservation test
#              (BUG_FIX_CONTEXT: grok-4.1-fast smoke-run failure at weight_parser).
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial implementation; 4 positive modes + malformed error test.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Parametrized test: all 9 positive parsing modes succeed (4 original + 5 reasoning-block)] => test_safe_json_parse_positive_modes
# FUNC 9 [Test: malformed input raises JsonParseError with raw snippet] => test_safe_json_parse_malformed_raises
# FUNC 8 [Test: JsonParseError carries correct raw_snippet attribute] => test_json_parse_error_snippet
# FUNC 9 [Test: raw_snippet on error preserves ORIGINAL input despite normalisation] => test_thinking_block_stripped_preserves_original_snippet_on_error
# END_MODULE_MAP

import logging
import pytest

from src.core.json_utils import JsonParseError, safe_json_parse


# ---------------------------------------------------------------------------
# Parametrized positive mode tests
# ---------------------------------------------------------------------------

# Test data: (description, raw_input, expected_key, expected_value)
_POSITIVE_TEST_CASES = [
    (
        "plain_json",
        '{"a": 1, "b": "hello"}',
        "a",
        1,
    ),
    (
        "fenced_json_with_language",
        '```json\n{"a": 2, "result": "ok"}\n```',
        "a",
        2,
    ),
    (
        "fenced_json_bare",
        '```\n{"a": 3, "mode": "bare"}\n```',
        "a",
        3,
    ),
    (
        "prose_embedded",
        'Here is the analysis result: {"a": 4, "embedded": true} — that concludes the output.',
        "a",
        4,
    ),
    # --- v1.1.0: Strategy 0 reasoning-block regression cases ---
    (
        "thinking_prefix_simple",
        '<thinking>reasoning here without braces</thinking>\n{"a": 5, "ok": true}',
        "a",
        5,
    ),
    (
        "thinking_prefix_uppercase",
        '<Thinking>mixed case</Thinking>\n{"a": 6}',
        "a",
        6,
    ),
    (
        "thinking_with_nested_braces",
        '<thinking>example dict: {"fake": "val"} — not real</thinking>\n{"a": 7}',
        "a",
        7,
    ),
    (
        "think_short_tag",
        '<think>deepseek-r1 style</think>\n{"a": 8}',
        "a",
        8,
    ),
    (
        "reasoning_block_before_fenced_json",
        '<reasoning>consider the options</reasoning>\n```json\n{"a": 9}\n```',
        "a",
        9,
    ),
]


# START_FUNCTION_test_safe_json_parse_positive_modes
# START_CONTRACT:
# PURPOSE: Verify safe_json_parse handles all 9 LLM output formats (4 original + 5 reasoning-block)
#          and returns correct dict.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# - Parametrized raw string inputs (9 test cases)
# KEYWORDS: [PATTERN(8): ParametrizedTest; CONCEPT(9): JSONParsing; CONCEPT(10): ReasoningModelNormalization]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
@pytest.mark.parametrize(
    "description, raw_input, expected_key, expected_value",
    _POSITIVE_TEST_CASES,
    ids=[tc[0] for tc in _POSITIVE_TEST_CASES],
)
def test_safe_json_parse_positive_modes(
    description, raw_input, expected_key, expected_value, caplog, ldd_capture
):
    """
    Parametrized test covering all 9 input modes that safe_json_parse must handle:
    1. Plain JSON string
    2. JSON inside ```json ... ``` fence
    3. JSON inside bare ``` ... ``` fence (no language specifier)
    4. JSON embedded inside prose text
    5-9. (v1.1.0) JSON after <thinking>, <Thinking>, <thinking> with nested braces,
         <think>, and <reasoning> prefix blocks (reasoning-model outputs).

    Each mode must return a dict containing the expected key-value pair.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Parse each raw input]
    result = safe_json_parse(raw_input)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert correct parse result]
    assert isinstance(result, dict), (
        f"[{description}] safe_json_parse must return dict, got: {type(result).__name__}"
    )
    assert expected_key in result, (
        f"[{description}] Expected key '{expected_key}' in result dict. Got keys: {list(result.keys())}"
    )
    assert result[expected_key] == expected_value, (
        f"[{description}] Expected {expected_key}={expected_value}, got: {result[expected_key]!r}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_safe_json_parse_positive_modes


# START_FUNCTION_test_safe_json_parse_malformed_raises
# START_CONTRACT:
# PURPOSE: Verify safe_json_parse raises JsonParseError (not json.JSONDecodeError) on
#          a completely malformed input that cannot be parsed by any strategy.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(8): ErrorHandling; PATTERN(7): ExceptionAssertion]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_safe_json_parse_malformed_raises(caplog, ldd_capture):
    """
    When the input is not valid JSON in any form, safe_json_parse must raise
    JsonParseError (the custom exception class), NOT the underlying json.JSONDecodeError.
    This ensures callers can rely on a single consistent exception type for error handling.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Attempt to parse completely invalid input]
    malformed_input = "This is not JSON at all. No braces, no valid structure here."

    with pytest.raises(JsonParseError) as exc_info:
        safe_json_parse(malformed_input)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert JsonParseError was raised]
    assert isinstance(exc_info.value, JsonParseError), (
        "Expected JsonParseError to be raised for malformed input"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_safe_json_parse_malformed_raises


# START_FUNCTION_test_json_parse_error_snippet
# START_CONTRACT:
# PURPOSE: Verify that JsonParseError carries the raw_snippet attribute set to
#          the first 200 chars of the malformed input.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(8): LDDEnrichment; PATTERN(7): ExceptionAttributeTest]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_json_parse_error_snippet(caplog, ldd_capture):
    """
    Verifies that JsonParseError.raw_snippet is set to the first 200 characters
    of the malformed input string. This snippet is critical for LDD trace logging
    at IMP:10 in node functions when parse failures occur during live sessions.
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_EXECUTION: [Create input longer than 200 chars to test truncation]
    long_malformed = "X" * 300 + " not JSON"

    with pytest.raises(JsonParseError) as exc_info:
        safe_json_parse(long_malformed)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert raw_snippet is set and truncated to 200 chars]
    error = exc_info.value
    assert hasattr(error, "raw_snippet"), (
        "JsonParseError must have 'raw_snippet' attribute for LDD trace logging"
    )
    assert len(error.raw_snippet) <= 200, (
        f"raw_snippet must be at most 200 chars, but got {len(error.raw_snippet)} chars"
    )
    assert error.raw_snippet == long_malformed[:200], (
        f"raw_snippet must be first 200 chars of input. "
        f"Expected: {long_malformed[:200]!r}, got: {error.raw_snippet!r}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_json_parse_error_snippet


# START_FUNCTION_test_thinking_block_stripped_preserves_original_snippet_on_error
# START_CONTRACT:
# PURPOSE: Verify that when parsing fails after Strategy 0 normalisation, JsonParseError.raw_snippet
#          contains the ORIGINAL raw input (starting with '<thinking>'), NOT the normalised version.
#          This confirms Requirement 4 from FILE 1: operators see authentic LLM output in error traces.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# KEYWORDS: [CONCEPT(10): ReasoningModelNormalization; CONCEPT(8): LDDEnrichment;
#            PATTERN(7): SnippetPreservation; CONCEPT(9): BugFixRegression]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_thinking_block_stripped_preserves_original_snippet_on_error(caplog, ldd_capture):
    """
    Regression test for BUG_FIX_CONTEXT (v1.1.0): when grok-4.1-fast emits a <thinking>
    block followed by content that still cannot be parsed as JSON, the JsonParseError
    raised must carry the ORIGINAL raw input in raw_snippet — not the normalised string
    with the <thinking> block removed.

    This matters for LDD telemetry: operators debugging weight_parser failures must see
    the authentic raw LLM output, including the reasoning prefix, to diagnose issues.

    Test inputs: '<thinking>garbage</thinking>trailing garbage with no JSON'
    Expected: JsonParseError raised; raw_snippet starts with '<thinking>'
    """
    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_PREPARE_INPUT: [Build input that has a thinking block but no valid JSON after it]
    raw_with_thinking_no_json = "<thinking>garbage reasoning text</thinking>trailing garbage with no JSON"
    # END_BLOCK_PREPARE_INPUT

    # START_BLOCK_EXECUTION: [Attempt parse — must fail because no JSON present even after normalisation]
    with pytest.raises(JsonParseError) as exc_info:
        safe_json_parse(raw_with_thinking_no_json)
    # END_BLOCK_EXECUTION

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory BEFORE assertions]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert raw_snippet is from ORIGINAL, not normalised string]
    error = exc_info.value

    assert hasattr(error, "raw_snippet"), (
        "JsonParseError must have 'raw_snippet' attribute for LDD trace logging"
    )

    # Core assertion: snippet must start with the <thinking> tag from the ORIGINAL input,
    # proving that normalisation did NOT overwrite the snippet used for error reporting.
    assert error.raw_snippet.startswith("<thinking>"), (
        f"raw_snippet must start with '<thinking>' (original input prefix), but got: {error.raw_snippet!r}. "
        f"This means raw_snippet was taken from the normalised string, violating Requirement 4."
    )

    # Also verify it matches the first 200 chars of the original input exactly
    assert error.raw_snippet == raw_with_thinking_no_json[:200], (
        f"raw_snippet must equal first 200 chars of original raw input. "
        f"Expected: {raw_with_thinking_no_json[:200]!r}, got: {error.raw_snippet!r}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_thinking_block_stripped_preserves_original_snippet_on_error
