# FILE: src/core/llm_utils.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Centralised LLM response sanitization utilities for reasoning-model outputs.
#          Provides three public functions to strip reasoning blocks, unwrap <output> tags,
#          and run the full sanitization pipeline — applied once in _invoke_llm_async so
#          ALL nodes receive a clean payload regardless of the underlying LLM family.
# SCOPE: strip_reasoning_blocks, extract_output_payload, sanitize_llm_response.
#        This module is stdlib-only (re); no third-party dependencies.
# INPUT: Raw string from LLM API response (may contain <thinking>, <output> wrappers).
# OUTPUT: Sanitized string with reasoning blocks and <output> wrapper removed.
# KEYWORDS: [DOMAIN(9): LLMOutputSanitization; CONCEPT(10): ReasoningModelNormalization;
#            TECH(8): regex; PATTERN(8): Pipeline; PATTERN(7): Idempotent]
# LINKS: [USES_API(5): re]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §3.1 (nodes.py, json_utils.py); Architect prompt — Feature slice §1
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - strip_reasoning_blocks is idempotent: calling twice on the same string yields the same result.
# - extract_output_payload is idempotent: calling twice on the same string yields the same result.
# - sanitize_llm_response is idempotent: sanitize(sanitize(x)) == sanitize(x) for all x.
# - sanitize_llm_response ALWAYS returns a str (never None, even on empty input).
# - extract_output_payload on absent/malformed <output> returns the input UNCHANGED (no data loss).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why centralise sanitization in llm_utils instead of keeping it in json_utils?
# A: json_utils.safe_json_parse only protects the JSON parsing path (nodes that call
#    safe_json_parse). Nodes like final_synthesizer write the raw LLM string directly
#    to state.final_answer without JSON parsing — there is no sanitization checkpoint.
#    A real-network smoke run showed <thinking>...</thinking><output>...</output> leaking
#    verbatim into user-facing final_answer. The fix must live UPSTREAM at the single
#    _invoke_llm_async call site, so ALL nodes receive a pre-sanitized string.
# Q: Why extract_output_payload prefers the LAST <output> block when multiple are present?
# A: Reasoning models (grok-4.1-fast, etc.) occasionally echo the prompt example in their
#    internal reasoning and then emit their own <output> block. The LAST complete <output>
#    block is the model's actual structured answer; earlier occurrences are artefacts.
# Q: Why keep strip_reasoning_blocks in json_utils as well (as a thin wrapper)?
# A: Backward compatibility and defense-in-depth. json_utils.safe_json_parse already
#    calls _strip_reasoning_blocks in Strategy 0. Making that a thin wrapper over
#    llm_utils.strip_reasoning_blocks avoids duplicated regex logic while preserving
#    the safe_json_parse call chain for any callers that bypass _invoke_llm_async.
# Q: Why is this module stdlib-only (just re)?
# A: The Architect's constraint prohibits adding new entries to requirements.txt for this
#    feature. The re module is always available in Python 3.12 and is sufficient for the
#    regex operations required.
# Q: Why use re.DOTALL | re.IGNORECASE on all patterns?
# A: DOTALL makes '.' match '\n' so multiline reasoning blocks (the common case) are
#    fully consumed. IGNORECASE handles mixed-capitalisation tags like <Thinking> or
#    <THINKING> that some models emit.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation. Extracted strip_reasoning_blocks from
#              json_utils._strip_reasoning_blocks; added extract_output_payload and
#              sanitize_llm_response. Applied by _invoke_llm_async in nodes.py (v2.1.0).
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Strips <thinking>, <think>, <reasoning>, <reflection> blocks; case-insensitive DOTALL] => strip_reasoning_blocks
# FUNC 9 [Extracts inner content from <output>...</output> wrapper; prefers LAST block if multiple] => extract_output_payload
# FUNC 10 [Full sanitization pipeline: strip_reasoning_blocks -> extract_output_payload -> .strip()] => sanitize_llm_response
# END_MODULE_MAP
#
# START_USE_CASES:
# - [sanitize_llm_response]: _invoke_llm_async -> SanitizeLLMPayload -> CleanStringDeliveredToAllNodes
# - [strip_reasoning_blocks]: json_utils._strip_reasoning_blocks (thin wrapper) -> NormalizePrefix -> StrategiesRunOnCleanText
# - [extract_output_payload]: sanitize_llm_response -> UnwrapOutputTag -> InnerContentExtracted
# END_USE_CASES

import re


# ---------------------------------------------------------------------------
# Compiled regex constants (module-level for performance)
# ---------------------------------------------------------------------------

# Pattern for known reasoning-model block tags.
# Non-greedy .*? inside DOTALL ensures each block is stripped individually,
# preventing one greedy match from consuming content between the first <thinking>
# and the last </reflection> if both are present in the same response.
_REASONING_TAG_RE = re.compile(
    r"<(thinking|think|reasoning|reflection)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Pattern for <output>...</output> wrapper.
# Non-greedy .*? prevents over-consumption when multiple <output> blocks exist.
# findall is used instead of search to support "prefer LAST block" semantics.
_OUTPUT_TAG_RE = re.compile(
    r"<output\b[^>]*>(.*?)</output\s*>",
    re.DOTALL | re.IGNORECASE,
)


# START_FUNCTION_strip_reasoning_blocks
# START_CONTRACT:
# PURPOSE: Remove all <thinking>, <think>, <reasoning>, <reflection> blocks from a raw
#          LLM string. Case-insensitive, DOTALL-aware, idempotent.
# INPUTS:
# - Raw LLM response string potentially containing reasoning-model prefix blocks => raw: str
# OUTPUTS:
# - str — input with all matched reasoning tag blocks removed and outer whitespace stripped.
#         Returns input unchanged if no matching tags are present.
# SIDE_EFFECTS: None. Pure function.
# KEYWORDS: [CONCEPT(10): ReasoningModelNormalization; TECH(8): regex;
#            PATTERN(7): Idempotent; PATTERN(6): Preprocessing]
# LINKS: [USES_API(5): re]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def strip_reasoning_blocks(raw: str) -> str:
    """
    Strip all reasoning-model tag blocks from a raw LLM response string.

    Handles the full family of reasoning-model prefixes used across major LLM providers:
    - <thinking>...</thinking>   — xAI Grok family (grok-4.1-fast, grok-3, etc.)
    - <think>...</think>         — DeepSeek-R1 and its fine-tuned derivatives
    - <reasoning>...</reasoning> — certain o1-style variants and custom models
    - <reflection>...</reflection> — reflection-tuned models

    The compiled regex uses DOTALL so multiline blocks are consumed in a single pass,
    and IGNORECASE so mixed-capitalisation tags (e.g. <Thinking>) are also matched.
    The non-greedy quantifier (.*?) ensures that multiple consecutive blocks of
    different types are each removed individually rather than consuming everything
    between the first opening tag and the last closing tag of a different type.

    This function is idempotent: if no matching tags are present the input is returned
    stripped but otherwise unchanged. Calling it twice on the same string yields the
    same result as calling it once.
    """

    # START_BLOCK_STRIP_BLOCKS: [Single-pass removal of all known reasoning tag blocks]
    return _REASONING_TAG_RE.sub("", raw).strip()
    # END_BLOCK_STRIP_BLOCKS
# END_FUNCTION_strip_reasoning_blocks


# START_FUNCTION_extract_output_payload
# START_CONTRACT:
# PURPOSE: Extract the inner text from the LAST complete <output>...</output> wrapper in
#          a raw LLM string. If no wrapper is present, returns the input unchanged.
#          Prefers the LAST block to handle models that echo prompt examples before
#          emitting their own structured response.
# INPUTS:
# - Raw LLM response string potentially wrapped in <output>...</output> => raw: str
# OUTPUTS:
# - str — trimmed inner content of the last <output> block, OR the original raw string
#         if no <output> wrapper is present or the match is malformed.
# SIDE_EFFECTS: None. Pure function.
# KEYWORDS: [CONCEPT(10): ReasoningModelNormalization; TECH(8): regex;
#            PATTERN(7): Idempotent; PATTERN(8): LastBlockPreference]
# LINKS: [USES_API(5): re]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def extract_output_payload(raw: str) -> str:
    """
    Extract the inner content of the LAST <output>...</output> wrapper from a raw LLM string.

    The xAI Grok family and similar reasoning models are prompted (via GLOBAL_PRIMING) to
    wrap their final structured answer in an <output> tag. This function unwraps that tag
    to expose only the actual payload — which may be a JSON string or Markdown text
    depending on the node.

    LAST block semantics: when multiple <output> blocks appear (e.g. because the model
    echoed the prompt example inside its <thinking> block and then emitted its own
    <output>), the LAST complete block is taken as the model's actual answer. Earlier
    occurrences are artefacts from prompt echo or reasoning prose.

    If no <output> tag is found the function returns the input unchanged (no-op), so it
    is safe to apply unconditionally to any LLM response regardless of model family.

    The function is idempotent: applying it to a string that contains no <output> tags
    returns the same string; applying it to an already-extracted payload (which has no
    <output> tags) also returns the same string.
    """

    # START_BLOCK_FIND_OUTPUT_BLOCKS: [Find all complete <output> blocks; prefer the last one]
    matches = _OUTPUT_TAG_RE.findall(raw)

    if not matches:
        # No <output> wrapper present — return input unchanged (idempotent no-op)
        return raw

    # Take the LAST match: reasoning models may echo prompt examples before their real answer
    last_payload = matches[-1].strip()
    return last_payload
    # END_BLOCK_FIND_OUTPUT_BLOCKS
# END_FUNCTION_extract_output_payload


# START_FUNCTION_sanitize_llm_response
# START_CONTRACT:
# PURPOSE: Full sanitization pipeline for raw LLM responses. Applies in order:
#          1. strip_reasoning_blocks — removes <thinking>, <think>, <reasoning>, <reflection> tags.
#          2. extract_output_payload — unwraps <output>...</output> if present.
#          3. .strip() — removes leading/trailing whitespace.
#          Idempotent: applying twice yields the same result as applying once.
# INPUTS:
# - Raw LLM response string (any model, any format) => raw: str
# OUTPUTS:
# - str — sanitized response with reasoning blocks stripped and <output> wrapper extracted.
#         ALWAYS returns a str (empty string on empty/whitespace-only input).
# SIDE_EFFECTS: None. Pure function.
# KEYWORDS: [DOMAIN(9): LLMOutputSanitization; CONCEPT(10): ReasoningModelNormalization;
#            PATTERN(8): Pipeline; PATTERN(7): Idempotent; TECH(8): regex]
# LINKS: [CALLS_FUNCTION(9): strip_reasoning_blocks; CALLS_FUNCTION(9): extract_output_payload]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def sanitize_llm_response(raw: str) -> str:
    """
    Full sanitization pipeline for raw LLM API responses.

    Applies three transformations in order, each of which is a pure function:

    Step 1 — strip_reasoning_blocks(raw):
        Removes all <thinking>/<think>/<reasoning>/<reflection> tag blocks. This prevents
        reasoning prose (which can contain example dicts, partial JSON, etc.) from leaking
        into the payload consumed by node functions.

    Step 2 — extract_output_payload(stripped):
        If the remaining text contains an <output>...</output> wrapper, extracts the inner
        content (preferring the LAST complete block). If no wrapper is present, this is a
        no-op and the stripped text passes through unchanged.

    Step 3 — .strip():
        Removes any residual leading/trailing whitespace introduced by the above steps.

    The pipeline is idempotent: for any input x,
        sanitize_llm_response(sanitize_llm_response(x)) == sanitize_llm_response(x).
    This property ensures it is safe to call multiple times on the same string without
    unexpected transformation.

    Applied by _invoke_llm_async in nodes.py so ALL six LLM node functions receive a
    sanitized payload without needing per-node sanitization logic.

    BUG_FIX_CONTEXT: Two production failures observed in real-network smoke run:
    1. weight_questioner wrote last_question='' because the JSON {"last_question": "..."}
       was inside <output> tags and safe_json_parse received the full raw string including
       the wrapper. The <output> tag is not a recognized reasoning block so Strategy 0
       in json_utils did not remove it, causing the JSON-inside-output pattern to be
       misidentified or returned as None key.
    2. final_synthesizer wrote <thinking>...</thinking><output>### Answer</output> verbatim
       into final_answer because that node does not call safe_json_parse at all — it writes
       raw_response directly to state. Sanitizing upstream at _invoke_llm_async eliminates
       both failure classes in one place.
    """

    # START_BLOCK_PIPELINE: [Apply strip -> extract -> strip in sequence]
    after_strip = strip_reasoning_blocks(raw)
    after_extract = extract_output_payload(after_strip)
    return after_extract.strip()
    # END_BLOCK_PIPELINE
# END_FUNCTION_sanitize_llm_response
