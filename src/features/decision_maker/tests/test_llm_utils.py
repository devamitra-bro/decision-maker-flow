# FILE: src/features/decision_maker/tests/test_llm_utils.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Test suite for src.core.llm_utils — covering strip_reasoning_blocks,
#          extract_output_payload, and sanitize_llm_response (unit tests) plus
#          integration regression tests that prove the two production bugs fixed in
#          nodes.py v2.1.0 do not regress: weight_questioner last_question extraction
#          and final_synthesizer clean Markdown output.
# SCOPE: Unit tests (pure sync — no pytest-asyncio needed for unit functions).
#        Integration regression tests (@pytest.mark.asyncio) for weight_questioner,
#        final_synthesizer, and _invoke_llm_async LDD telemetry.
# INPUT: Synthetic raw LLM strings and DI fake_llm factories. No real LLM calls.
# OUTPUT: pytest pass/fail assertions + IMP:7-10 LDD trajectory printed to stdout.
# KEYWORDS: [DOMAIN(8): Tests; CONCEPT(9): LLMOutputSanitization;
#            CONCEPT(10): ReasoningModelNormalization; PATTERN(8): Regression;
#            PATTERN(7): AtomicTest; PATTERN(8): IntegrationTest]
# LINKS: [READS_DATA_FROM(10): src.core.llm_utils.strip_reasoning_blocks;
#         READS_DATA_FROM(10): src.core.llm_utils.extract_output_payload;
#         READS_DATA_FROM(10): src.core.llm_utils.sanitize_llm_response;
#         READS_DATA_FROM(9): src.features.decision_maker.nodes.weight_questioner;
#         READS_DATA_FROM(9): src.features.decision_maker.nodes.final_synthesizer;
#         READS_DATA_FROM(9): src.features.decision_maker.nodes._invoke_llm_async]
# LINKS_TO_SPECIFICATION: Architect prompt — Feature slice §4 (test coverage targets)
# END_MODULE_CONTRACT:
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; 5 strip_reasoning_blocks unit tests,
#              5 extract_output_payload unit tests, 3 sanitize_llm_response unit tests,
#              3 integration regression tests (@pytest.mark.asyncio).
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Unit: strip_reasoning_blocks — 5 parametrized cases] => test_strip_reasoning_blocks
# FUNC 9 [Unit: extract_output_payload — 5 parametrized cases including multi-block LAST preference] => test_extract_output_payload
# FUNC 9 [Unit: sanitize_llm_response — pipeline, idempotent, empty input] => test_sanitize_llm_response
# FUNC 10 [Integration: weight_questioner returns last_question from grok-style wrapped JSON] => test_weight_questioner_extracts_last_question_from_grok_style_response
# FUNC 10 [Integration: final_synthesizer returns clean Markdown without <thinking>/<output> tags] => test_final_synthesizer_strips_reasoning_from_final_answer
# FUNC 9 [Integration: _invoke_llm_async emits both raw_length and cleaned_length LDD log lines] => test_invoke_llm_async_emits_cleaned_length_log
# END_MODULE_MAP

import logging

import pytest
import pytest_asyncio

from src.core.llm_utils import (
    extract_output_payload,
    sanitize_llm_response,
    strip_reasoning_blocks,
)


# ===========================================================================
# Unit tests: strip_reasoning_blocks
# ===========================================================================

# Test data: (description, raw_input, expected_output)
_STRIP_TEST_CASES = [
    (
        "basic_thinking_tag",
        "<thinking>reasoning prose here</thinking>foo",
        "foo",
    ),
    (
        "uppercase_thinking_tag",
        "<THINKING>case insensitive block</THINKING>foo",
        "foo",
    ),
    (
        "multi_tag_think_and_reasoning",
        "<think>DeepSeek block A</think><reasoning>block B</reasoning>foo",
        "foo",
    ),
    (
        "nested_braces_inside_thinking",
        '<thinking>{"x": 1, "y": "example"}</thinking>foo',
        "foo",
    ),
    (
        "no_tags_unchanged",
        "plain text with no tags",
        "plain text with no tags",
    ),
]


# START_FUNCTION_test_strip_reasoning_blocks
# START_CONTRACT:
# PURPOSE: Verify strip_reasoning_blocks correctly removes all known reasoning-model
#          tag families and is a no-op on clean input.
# INPUTS:
# - Parametrized (description, raw_input, expected_output) from _STRIP_TEST_CASES
# OUTPUTS: None (pytest assertions)
# KEYWORDS: [PATTERN(8): ParametrizedTest; CONCEPT(10): ReasoningModelNormalization;
#            PATTERN(7): Idempotent]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.parametrize(
    "description, raw_input, expected_output",
    _STRIP_TEST_CASES,
    ids=[tc[0] for tc in _STRIP_TEST_CASES],
)
def test_strip_reasoning_blocks(description, raw_input, expected_output):
    """
    Unit tests for strip_reasoning_blocks covering five cases:
    1. Basic <thinking>X</thinking>foo -> "foo"
    2. Uppercase <THINKING>X</THINKING>foo -> "foo" (case-insensitive)
    3. Multi-tag: <think>A</think><reasoning>B</reasoning>foo -> "foo"
    4. Braces inside thinking block: must NOT leak into output
    5. No matching tags: input returned unchanged (idempotent no-op)

    Pure sync function — no pytest-asyncio needed.
    """

    # START_BLOCK_EXECUTION: [Call strip_reasoning_blocks on each parametrized input]
    result = strip_reasoning_blocks(raw_input)
    # END_BLOCK_EXECUTION

    # START_BLOCK_VERIFICATION: [Assert exact expected output]
    assert result == expected_output, (
        f"[{description}] strip_reasoning_blocks returned unexpected result.\n"
        f"  Input:    {raw_input!r}\n"
        f"  Expected: {expected_output!r}\n"
        f"  Got:      {result!r}"
    )

    # Anti-Illusion: verify idempotency — calling twice gives the same result
    result_twice = strip_reasoning_blocks(result)
    assert result_twice == result, (
        f"[{description}] strip_reasoning_blocks is NOT idempotent.\n"
        f"  First call:  {result!r}\n"
        f"  Second call: {result_twice!r}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_strip_reasoning_blocks


# ===========================================================================
# Unit tests: extract_output_payload
# ===========================================================================

# Test data: (description, raw_input, expected_output)
_EXTRACT_TEST_CASES = [
    (
        "present_single_output_tag",
        "<output>PAYLOAD</output>",
        "PAYLOAD",
    ),
    (
        "absent_returns_input_unchanged",
        "plain text without any output tag",
        "plain text without any output tag",
    ),
    (
        "multi_line_markdown_payload",
        "<output>\n### Markdown Header\n\n- Item 1\n- Item 2\n</output>",
        "### Markdown Header\n\n- Item 1\n- Item 2",
    ),
    (
        "case_insensitive_output_tag",
        "<OUTPUT>case insensitive</OUTPUT>",
        "case insensitive",
    ),
    (
        "multiple_output_blocks_prefer_last",
        (
            "<output>PROMPT_EXAMPLE_ECHO</output>"
            " some reasoning text "
            "<output>ACTUAL_ANSWER</output>"
        ),
        "ACTUAL_ANSWER",
    ),
]


# START_FUNCTION_test_extract_output_payload
# START_CONTRACT:
# PURPOSE: Verify extract_output_payload correctly unwraps <output> tags, prefers
#          the LAST block when multiple are present, and returns input unchanged when
#          no <output> tag is present.
# INPUTS:
# - Parametrized (description, raw_input, expected_output) from _EXTRACT_TEST_CASES
# OUTPUTS: None (pytest assertions)
# KEYWORDS: [PATTERN(8): ParametrizedTest; CONCEPT(10): ReasoningModelNormalization;
#            PATTERN(8): LastBlockPreference; PATTERN(7): Idempotent]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.parametrize(
    "description, raw_input, expected_output",
    _EXTRACT_TEST_CASES,
    ids=[tc[0] for tc in _EXTRACT_TEST_CASES],
)
def test_extract_output_payload(description, raw_input, expected_output):
    """
    Unit tests for extract_output_payload covering five cases:
    1. Present single <output>PAYLOAD</output> -> "PAYLOAD"
    2. Absent tag: "plain text" -> "plain text" (unchanged)
    3. Multi-line Markdown payload (newlines in content)
    4. Case-insensitive tag matching (<OUTPUT>)
    5. MULTIPLE <output> blocks -> return the LAST one (critical robustness case)

    Pure sync function — no pytest-asyncio needed.
    """

    # START_BLOCK_EXECUTION: [Call extract_output_payload on each parametrized input]
    result = extract_output_payload(raw_input)
    # END_BLOCK_EXECUTION

    # START_BLOCK_VERIFICATION: [Assert exact expected output]
    assert result == expected_output, (
        f"[{description}] extract_output_payload returned unexpected result.\n"
        f"  Input:    {raw_input!r}\n"
        f"  Expected: {expected_output!r}\n"
        f"  Got:      {result!r}"
    )

    # Anti-Illusion: verify idempotency for extracted payloads (no <output> in extracted content)
    result_twice = extract_output_payload(result)
    assert result_twice == result, (
        f"[{description}] extract_output_payload is NOT idempotent on extracted content.\n"
        f"  First call:  {result!r}\n"
        f"  Second call: {result_twice!r}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_extract_output_payload


# ===========================================================================
# Unit tests: sanitize_llm_response
# ===========================================================================

# START_FUNCTION_test_sanitize_llm_response
# START_CONTRACT:
# PURPOSE: Verify the full sanitize_llm_response pipeline: strip reasoning blocks,
#          extract output payload, and strip whitespace. Also verifies idempotency
#          and correct handling of empty/whitespace-only input.
# INPUTS:
# - Inline test cases (no parametrize — 3 distinct scenarios tested in one function
#   for clarity and LDD telemetry consolidation)
# OUTPUTS: None (pytest assertions)
# KEYWORDS: [PATTERN(8): Pipeline; PATTERN(7): Idempotent;
#            CONCEPT(9): LLMOutputSanitization; CONCEPT(10): ReasoningModelNormalization]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_sanitize_llm_response():
    """
    Unit tests for sanitize_llm_response covering three cases:

    Case 1 — Full grok-style pipeline:
        Input: "<thinking>R</thinking><output>P</output>"
        Expected: "P"
        Validates: strip strips <thinking>, extract unwraps <output>, strip removes whitespace.

    Case 2 — Idempotency:
        sanitize(sanitize(x)) == sanitize(x) for a grok-style input.
        Validates: the pipeline does not over-transform on the second application.

    Case 3 — Empty and whitespace-only input:
        sanitize("") == "" and sanitize("   ") == ""
        Validates: function always returns str, never None, even on degenerate input.

    Pure sync function — no pytest-asyncio needed.
    """

    # START_BLOCK_CASE_1_FULL_PIPELINE: [grok-style <thinking><output> input -> clean payload]
    grok_input = "<thinking>R reasoning here</thinking><output>P payload text</output>"
    result_1 = sanitize_llm_response(grok_input)

    assert result_1 == "P payload text", (
        f"Case 1: full pipeline failed.\n"
        f"  Input:    {grok_input!r}\n"
        f"  Expected: 'P payload text'\n"
        f"  Got:      {result_1!r}"
    )
    print(f"\n[IMP:9][sanitize_llm_response][CASE_1] Full pipeline OK: {result_1!r}")
    # END_BLOCK_CASE_1_FULL_PIPELINE

    # START_BLOCK_CASE_2_IDEMPOTENCY: [sanitize(sanitize(x)) == sanitize(x)]
    idempotent_input = "<thinking>Reasoning</thinking><output>JSON or Markdown</output>"
    first_pass = sanitize_llm_response(idempotent_input)
    second_pass = sanitize_llm_response(first_pass)

    assert first_pass == second_pass, (
        f"Case 2: sanitize_llm_response is NOT idempotent.\n"
        f"  Input:       {idempotent_input!r}\n"
        f"  First pass:  {first_pass!r}\n"
        f"  Second pass: {second_pass!r}"
    )
    print(f"[IMP:9][sanitize_llm_response][CASE_2] Idempotency OK: first={first_pass!r} second={second_pass!r}")
    # END_BLOCK_CASE_2_IDEMPOTENCY

    # START_BLOCK_CASE_3_EMPTY_INPUT: [Empty string and whitespace-only input -> ""]
    result_empty = sanitize_llm_response("")
    result_whitespace = sanitize_llm_response("   \n\t  ")

    assert isinstance(result_empty, str), (
        f"Case 3a: sanitize_llm_response('') must return str, got {type(result_empty).__name__}"
    )
    assert result_empty == "", (
        f"Case 3a: sanitize_llm_response('') expected '', got {result_empty!r}"
    )
    assert result_whitespace == "", (
        f"Case 3b: sanitize_llm_response('   ') expected '', got {result_whitespace!r}"
    )
    print(f"[IMP:9][sanitize_llm_response][CASE_3] Empty/whitespace OK: empty={result_empty!r} ws={result_whitespace!r}")
    # END_BLOCK_CASE_3_EMPTY_INPUT
# END_FUNCTION_test_sanitize_llm_response


# ===========================================================================
# Integration regression tests — require @pytest.mark.asyncio
# ===========================================================================

# START_FUNCTION_test_weight_questioner_extracts_last_question_from_grok_style_response
# START_CONTRACT:
# PURPOSE: Regression test for weight_questioner bug: when grok-4.1-fast wraps the JSON
#          inside <thinking>...</thinking><output>...</output> tags, weight_questioner must
#          correctly extract last_question (NOT return empty string).
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# OUTPUTS: None (pytest assertions)
# SIDE_EFFECTS: Awaits weight_questioner with injected grok-style fake LLM.
# KEYWORDS: [PATTERN(8): Regression; CONCEPT(9): LLMOutputSanitization;
#            CONCEPT(10): ReasoningModelNormalization; PATTERN(9): AsyncIntegration]
# LINKS: [READS_DATA_FROM(9): src.features.decision_maker.nodes.weight_questioner]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_weight_questioner_extracts_last_question_from_grok_style_response(caplog, ldd_capture):
    """
    Integration regression test for the weight_questioner silent-fail bug.

    A real-network smoke run revealed that when grok-4.1-fast wraps its JSON inside
    <thinking>...</thinking><output>{"last_question": "..."}</output> tags,
    weight_questioner was writing last_question='' to state because:
    - safe_json_parse Strategy 0 strips <thinking> but NOT <output> tags
    - The remaining string "<output>{"last_question": ...}</output>" was parsed as a
      dict but the top-level key was absent (the output tag wrapped the content)

    The fix in nodes.py v2.1.0 applies sanitize_llm_response inside _invoke_llm_async,
    so by the time safe_json_parse sees the string it is already clean JSON.

    This test builds a fake_llm that emits a grok-style wrapped payload and asserts
    that weight_questioner returns {"last_question": "Какие критерии важнее?"}.
    """

    from langchain_core.messages import AIMessage
    from src.features.decision_maker.nodes import weight_questioner

    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_BUILD_FAKE_LLM: [Grok-style fake: <thinking>...</thinking><output>JSON</output>]
    grok_style_content = (
        "<thinking>\n"
        "Let me think about the calibration question...\n"
        "The user has a housing dilemma. I should ask about cost vs flexibility.\n"
        '{"last_question": "wrong_key_in_thinking"}\n'
        "</thinking>\n"
        "<output>"
        '{"last_question": "Какие критерии важнее?"}'
        "</output>"
    )

    class _GrokFakeLLM:
        async def ainvoke(self, messages) -> AIMessage:
            return AIMessage(content=grok_style_content)

        def invoke(self, messages) -> AIMessage:
            return AIMessage(content=grok_style_content)

    def fake_factory() -> _GrokFakeLLM:
        return _GrokFakeLLM()
    # END_BLOCK_BUILD_FAKE_LLM

    # START_BLOCK_INVOKE_NODE: [Await weight_questioner with injected fake LLM]
    state = {
        "user_input": "Купить квартиру или продолжать арендовать?",
        "dilemma": "Buy vs rent dilemma",
        "tool_facts": [],
        "last_question": "",
        "user_answer": "",
        "weights": {},
        "assumptions": "",
        "draft_analysis": "",
        "critique_feedback": "",
        "rewrite_count": 0,
        "is_data_sufficient": True,
        "search_queries": [],
        "final_answer": "",
    }

    result = await weight_questioner(state, llm_factory=fake_factory)
    # END_BLOCK_INVOKE_NODE

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 BEFORE assertions — LDD Anti-Illusion]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert last_question is correctly extracted — NOT empty]
    assert "last_question" in result, (
        f"weight_questioner must return dict with 'last_question' key. Got: {result}"
    )
    assert result["last_question"] == "Какие критерии важнее?", (
        f"weight_questioner extracted wrong last_question.\n"
        f"  Expected: 'Какие критерии важнее?'\n"
        f"  Got:      {result['last_question']!r}\n"
        f"  This is the regression bug: grok-style <output> tag was not unwrapped."
    )
    assert result["last_question"] != "", (
        "weight_questioner wrote last_question='' — regression bug not fixed."
    )

    # Anti-Illusion: _invoke_llm_async must have emitted cleaned_length log
    cleaned_length_found = any("cleaned_length=" in msg for msg in high_imp_logs)
    assert cleaned_length_found, (
        "Critical LDD Error: _invoke_llm_async did not emit 'cleaned_length=' log line.\n"
        "Sanitization may not have been applied. Check nodes.py _invoke_llm_async."
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_weight_questioner_extracts_last_question_from_grok_style_response


# START_FUNCTION_test_final_synthesizer_strips_reasoning_from_final_answer
# START_CONTRACT:
# PURPOSE: Regression test for final_synthesizer bug: raw <thinking>/<output> tags
#          must NOT appear in state.final_answer. The sanitized Markdown content must
#          be present without any XML tag wrappers.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# OUTPUTS: None (pytest assertions)
# SIDE_EFFECTS: Awaits final_synthesizer with injected grok-style fake LLM.
# KEYWORDS: [PATTERN(8): Regression; CONCEPT(9): LLMOutputSanitization;
#            CONCEPT(10): ReasoningModelNormalization; PATTERN(9): AsyncIntegration]
# LINKS: [READS_DATA_FROM(9): src.features.decision_maker.nodes.final_synthesizer]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_final_synthesizer_strips_reasoning_from_final_answer(caplog, ldd_capture):
    """
    Integration regression test for the final_synthesizer tag-leak bug.

    A real-network smoke run revealed that final_synthesizer wrote the full
    "<thinking>reasoning prose</thinking><output>### Markdown final answer</output>"
    string verbatim into state.final_answer. This node does NOT call safe_json_parse
    (it writes raw LLM text to state directly), so Strategy 0 in json_utils provided
    no protection.

    The fix in nodes.py v2.1.0 applies sanitize_llm_response inside _invoke_llm_async
    BEFORE the string is returned to final_synthesizer and written to state.

    This test asserts:
    1. final_answer contains the expected Markdown content.
    2. final_answer does NOT contain "<thinking>" substring.
    3. final_answer does NOT contain "<output>" substring.
    """

    from langchain_core.messages import AIMessage
    from src.features.decision_maker.nodes import final_synthesizer

    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_BUILD_FAKE_LLM: [Grok-style fake: thinking + Markdown in output tag]
    grok_style_content = (
        "<thinking>\n"
        "reasoning prose about how to format the final answer\n"
        "</thinking>\n"
        "<output>\n"
        "### Markdown final answer\n"
        "\n"
        "Based on the analysis, renting is recommended.\n"
        "</output>"
    )

    class _GrokFakeLLM:
        async def ainvoke(self, messages) -> AIMessage:
            return AIMessage(content=grok_style_content)

        def invoke(self, messages) -> AIMessage:
            return AIMessage(content=grok_style_content)

    def fake_factory() -> _GrokFakeLLM:
        return _GrokFakeLLM()
    # END_BLOCK_BUILD_FAKE_LLM

    # START_BLOCK_INVOKE_NODE: [Await final_synthesizer with injected fake LLM]
    state = {
        "user_input": "Купить квартиру или продолжать арендовать?",
        "dilemma": "Buy vs rent dilemma",
        "tool_facts": [],
        "last_question": "What matters more?",
        "user_answer": "Flexibility matters most",
        "weights": {"cost": 5, "flexibility": 9},
        "assumptions": "",
        "draft_analysis": "Draft analysis text here",
        "critique_feedback": "",
        "rewrite_count": 0,
        "is_data_sufficient": True,
        "search_queries": [],
        "final_answer": "",
    }

    result = await final_synthesizer(state, llm_factory=fake_factory)
    # END_BLOCK_INVOKE_NODE

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 BEFORE assertions — LDD Anti-Illusion]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert Markdown present and tags absent]
    assert "final_answer" in result, (
        f"final_synthesizer must return dict with 'final_answer' key. Got: {result}"
    )

    final_answer = result["final_answer"]

    assert "### Markdown final answer" in final_answer, (
        f"final_answer must contain Markdown content.\n"
        f"  Expected substring: '### Markdown final answer'\n"
        f"  Got final_answer: {final_answer!r}"
    )

    assert "<thinking>" not in final_answer, (
        f"final_answer must NOT contain '<thinking>' tag — regression bug detected.\n"
        f"  Got final_answer: {final_answer!r}"
    )

    assert "<output>" not in final_answer, (
        f"final_answer must NOT contain '<output>' tag — regression bug detected.\n"
        f"  Got final_answer: {final_answer!r}"
    )

    # Anti-Illusion: sanitized_delta must be positive (sanitization actually stripped something)
    sanitized_delta_found = any("sanitized_delta=" in msg for msg in high_imp_logs)
    assert sanitized_delta_found, (
        "Critical LDD Error: _invoke_llm_async did not emit 'sanitized_delta=' log line.\n"
        "Check nodes.py _invoke_llm_async BLOCK_SANITIZE."
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_final_synthesizer_strips_reasoning_from_final_answer


# START_FUNCTION_test_invoke_llm_async_emits_cleaned_length_log
# START_CONTRACT:
# PURPOSE: Verify _invoke_llm_async emits BOTH raw_length= and cleaned_length= LDD log
#          lines (IMP:8) when called with a grok-style fake LLM that returns reasoning tags.
# INPUTS:
# - caplog fixture => caplog
# - ldd_capture fixture => ldd_capture
# OUTPUTS: None (pytest assertions)
# SIDE_EFFECTS: Awaits _invoke_llm_async with injected grok-style fake LLM.
# KEYWORDS: [PATTERN(8): LDDTelemetry; CONCEPT(9): LLMOutputSanitization;
#            PATTERN(7): AtomicTest; PATTERN(9): AsyncIntegration]
# LINKS: [READS_DATA_FROM(9): src.features.decision_maker.nodes._invoke_llm_async]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
@pytest.mark.asyncio
async def test_invoke_llm_async_emits_cleaned_length_log(caplog, ldd_capture):
    """
    Integration test verifying that _invoke_llm_async emits the required diagnostic
    LDD log lines for sanitization visibility.

    Specifically tests that BOTH of the following IMP:8 log lines are emitted:
    1. "raw_length=..." — from BLOCK_LLM_CALL ResponseReceived log (existing, backward-compat)
    2. "cleaned_length=..." — from BLOCK_SANITIZE Sanitized log (new in v2.1.0)

    These lines give operations teams and debug agents quantitative visibility into how
    much content was stripped by sanitize_llm_response per LLM call.

    Uses a grok-style fake response that includes <thinking> and <output> tags so that
    sanitized_delta will be positive (confirming sanitization actually ran).
    """

    from langchain_core.messages import AIMessage
    from src.features.decision_maker.nodes import _invoke_llm_async

    caplog.set_level(logging.INFO, logger="decision_maker")

    # START_BLOCK_BUILD_FAKE_LLM: [Grok-style fake with measurable sanitization delta]
    grok_response_content = (
        "<thinking>some reasoning prose that should be stripped</thinking>"
        "<output>the actual clean response payload</output>"
    )

    class _GrokFakeLLM:
        async def ainvoke(self, messages) -> AIMessage:
            return AIMessage(content=grok_response_content)

        def invoke(self, messages) -> AIMessage:
            return AIMessage(content=grok_response_content)

    fake_llm_instance = _GrokFakeLLM()
    # END_BLOCK_BUILD_FAKE_LLM

    # START_BLOCK_INVOKE_HELPER: [Await _invoke_llm_async directly]
    returned_content = await _invoke_llm_async(
        llm=fake_llm_instance,
        system_text="system message",
        human_text="human message",
        caller_name="test_invoke",
    )
    # END_BLOCK_INVOKE_HELPER

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 BEFORE assertions — LDD Anti-Illusion]
    high_imp_logs = ldd_capture(caplog.records)
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert raw_length and cleaned_length both in logs]
    raw_length_found = any("raw_length=" in msg for msg in high_imp_logs)
    cleaned_length_found = any("cleaned_length=" in msg for msg in high_imp_logs)

    assert raw_length_found, (
        "Critical LDD Error: _invoke_llm_async did not emit 'raw_length=' log line.\n"
        "Check BLOCK_LLM_CALL [ResponseReceived] log in nodes.py _invoke_llm_async.\n"
        f"High-IMP logs captured: {high_imp_logs}"
    )

    assert cleaned_length_found, (
        "Critical LDD Error: _invoke_llm_async did not emit 'cleaned_length=' log line.\n"
        "Check BLOCK_SANITIZE [Sanitized] log in nodes.py _invoke_llm_async (v2.1.0).\n"
        f"High-IMP logs captured: {high_imp_logs}"
    )

    # Anti-Illusion: returned content must be clean (no tags)
    assert "<thinking>" not in returned_content, (
        f"_invoke_llm_async must return sanitized content without '<thinking>'.\n"
        f"Got: {returned_content!r}"
    )
    assert "<output>" not in returned_content, (
        f"_invoke_llm_async must return sanitized content without '<output>'.\n"
        f"Got: {returned_content!r}"
    )
    assert returned_content == "the actual clean response payload", (
        f"_invoke_llm_async returned unexpected sanitized content.\n"
        f"Expected: 'the actual clean response payload'\n"
        f"Got:      {returned_content!r}"
    )
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_invoke_llm_async_emits_cleaned_length_log
