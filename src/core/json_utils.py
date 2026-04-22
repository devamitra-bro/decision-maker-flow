# FILE: src/core/json_utils.py
# VERSION: 1.2.0
# START_MODULE_CONTRACT:
# PURPOSE: Markdown-fence-aware JSON parser for LLM raw outputs, hardened for reasoning models.
# SCOPE: Parses JSON embedded in plain text, ```json fences, bare ``` fences, or prose;
#        pre-normalises reasoning-model prefixes (<thinking>, <think>, <reasoning>, <reflection>)
#        before attempting extraction. Strategy 0 normalisation now delegates to
#        src.core.llm_utils.strip_reasoning_blocks (centralised sanitization).
# INPUT: Raw string from LLM response (str).
# OUTPUT: Parsed Python dict; or raises JsonParseError with the raw snippet on failure.
# KEYWORDS: [DOMAIN(8): Parsing; CONCEPT(9): LLMOutputSanitization; TECH(7): re; PATTERN(6): SafeParser;
#            CONCEPT(10): ReasoningModelNormalization]
# LINKS: [USES_API(5): json; USES_API(6): re;
#         USES_API(9): src.core.llm_utils.strip_reasoning_blocks]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §3.1 (json_utils_py), §5.7, AC8
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - safe_json_parse ALWAYS returns a dict on success.
# - safe_json_parse ALWAYS raises JsonParseError (not json.JSONDecodeError) on failure.
# - JsonParseError.raw_snippet is ALWAYS set to the first 200 chars of the ORIGINAL input (not normalised).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why use regex-based extraction instead of a simple str.strip()?
# A: LLM outputs can embed JSON inside ```json blocks, bare ``` blocks, or even prose
#    sentences like "Here is the analysis: {...} — end." Simple strip would fail all
#    three cases. Regex extracts the first {...} balanced block correctly.
# Q: Why try multiple extraction strategies in order before falling to regex?
# A: Order of strategies follows descending specificity — most constrained (```json fenced)
#    first, then less constrained (bare fenced), then raw extraction. This minimises
#    false positive matches in edge cases.
# Q: Why raise JsonParseError rather than return None?
# A: Callers (node functions) must log the raw snippet at IMP:10 for LDD trace. A None
#    return would require all callers to add None checks; a typed exception with raw_snippet
#    standardises the error-handling contract across all nodes.
# Q: Why does Strategy 0 (normalisation) exist as a pre-processing step?
# A: Reasoning models (grok-4.1-fast, deepseek-r1, o1-family) emit a <thinking>...</thinking>
#    block BEFORE their structured JSON output. The greedy regex in Strategy 4 would match
#    braces embedded in the reasoning prose (e.g. example dicts, quoted JSON fragments)
#    rather than the real output object, causing JSONDecodeError or silently returning wrong
#    data. Stripping reasoning tags before any strategy runs eliminates this failure class
#    entirely without changing how any strategy works for non-reasoning models.
# Q: Why does _strip_reasoning_blocks now delegate to llm_utils.strip_reasoning_blocks?
# A: v1.2.0 centralised all LLM response sanitization in src.core.llm_utils. The private
#    function is kept as a thin wrapper for backward compatibility (safe_json_parse calls it
#    in Strategy 0; internal tests may reference the local symbol). The implementation lives
#    once in llm_utils, eliminating duplicate regex logic. Defense-in-depth is preserved:
#    Strategy 0 still strips reasoning blocks even if the caller already sanitized upstream
#    via sanitize_llm_response (double application is idempotent).
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.2.0 - Centralised sanitization: _strip_reasoning_blocks now delegates to
#              src.core.llm_utils.strip_reasoning_blocks (thin wrapper for backward compat).
#              Updated LINKS, RATIONALE, MODULE_MAP. VERSION bumped. No behaviour change.
# PREV_CHANGE_SUMMARY: v1.1.0 - Added Strategy 0 (reasoning-block normalization) + Strategy 4 raw_decode fallback;
#              hardened against grok-4.1-fast / deepseek-r1 / o1 outputs with <thinking> prefixes.
# PREV_PREV_CHANGE_SUMMARY: v1.0.0 - Initial implementation; 4-mode parser with JsonParseError.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 7 [Custom exception carrying raw LLM snippet for LDD trace] => JsonParseError
# FUNC 10 [5-mode JSON extractor for LLM raw output strings; Strategy 0 normalises reasoning blocks] => safe_json_parse
# FUNC 8 [Private thin wrapper — delegates to llm_utils.strip_reasoning_blocks for backward compat] => _strip_reasoning_blocks
# END_MODULE_MAP
#
# START_USE_CASES:
# - [safe_json_parse]: NodeFunction -> ParseLLMOutput -> StructuredDictExtracted
# - [JsonParseError]: NodeFunction -> HandleParseFailure -> LDDTrace_IMP10_Emitted
# - [_strip_reasoning_blocks]: safe_json_parse -> NormalizeReasoningPrefix -> CleanStringReadyForExtraction
# END_USE_CASES

import json
import re

# BUG_FIX_CONTEXT: v1.2.0 — LLM response sanitization centralised in llm_utils.
# strip_reasoning_blocks is imported here so _strip_reasoning_blocks (below) can
# delegate to the canonical implementation rather than maintaining a duplicate regex.
# This import also allows safe_json_parse (Strategy 0) to benefit from any future
# improvements to the central sanitization logic automatically.
from src.core.llm_utils import strip_reasoning_blocks


class JsonParseError(Exception):
    """
    Custom exception raised when safe_json_parse cannot extract valid JSON from the
    raw LLM output string. Carries the raw_snippet (first 200 chars) for LDD telemetry.
    """

    # START_CONTRACT:
    # PURPOSE: Signal a JSON parse failure with contextual raw snippet for LDD logging.
    # INPUTS:
    # - First 200 chars of the raw LLM output => raw_snippet: str
    # KEYWORDS: [CONCEPT(8): LDDEnrichment; PATTERN(5): CustomException]
    # COMPLEXITY_SCORE: 2
    # END_CONTRACT

    def __init__(self, raw_snippet: str) -> None:
        self.raw_snippet = raw_snippet
        super().__init__(f"Failed to parse JSON from LLM output. Snippet: {raw_snippet!r}")


# START_FUNCTION__strip_reasoning_blocks
# START_CONTRACT:
# PURPOSE: Thin backward-compatibility wrapper around llm_utils.strip_reasoning_blocks.
#          Kept as a private symbol so safe_json_parse (Strategy 0) and any external
#          callers that import this symbol continue to work without modification.
#          Implementation is delegated entirely to the canonical llm_utils function.
# INPUTS:
# - Raw LLM response string potentially prefixed with a reasoning block => text: str
# OUTPUTS:
# - str — input with all matched reasoning blocks removed; whitespace-stripped
# SIDE_EFFECTS: None. Pure function.
# KEYWORDS: [CONCEPT(10): ReasoningModelNormalization; PATTERN(6): ThinWrapper;
#            PATTERN(7): BackwardCompatibility]
# LINKS: [CALLS_FUNCTION(9): src.core.llm_utils.strip_reasoning_blocks]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def _strip_reasoning_blocks(text: str) -> str:
    """
    Thin backward-compatibility wrapper around llm_utils.strip_reasoning_blocks.

    Prior to v1.2.0, this function contained its own compiled regex. In v1.2.0
    the implementation was centralised in src.core.llm_utils to serve both the
    json_utils path (Strategy 0 in safe_json_parse) and the upstream
    _invoke_llm_async sanitization path (nodes.py v2.1.0). This wrapper retains
    the local symbol so callers need not be updated.

    BUG_FIX_CONTEXT: Prior to v1.1.0, reasoning-model outputs (grok-4.1-fast, deepseek-r1, o1-family)
    with leading <thinking>...</thinking> prefixes broke Strategy 4 — the greedy regex grabbed
    braces embedded in reasoning text. Fix introduced in v1.1.0: pre-normalize by stripping
    reasoning tags, then add a raw_decode() fallback to Strategy 4. In v1.2.0 the implementation
    was lifted to llm_utils for full-pipeline coverage (not just the JSON parse path).
    """

    # START_BLOCK_DELEGATE: [Delegate to canonical llm_utils implementation]
    return strip_reasoning_blocks(text)
    # END_BLOCK_DELEGATE
# END_FUNCTION__strip_reasoning_blocks


# START_FUNCTION_safe_json_parse
# START_CONTRACT:
# PURPOSE: Extract and parse the first JSON object from a raw LLM response string.
#          Handles 5 input modes: Strategy 0 (reasoning-block normalisation as pre-step),
#          then plain JSON, ```json fenced, bare ``` fenced, prose-embedded (with raw_decode fallback).
# INPUTS:
# - Raw LLM response string potentially wrapping JSON => raw: str
# OUTPUTS:
# - dict — parsed JSON object
# SIDE_EFFECTS: None. Does not log; callers are responsible for IMP:10 log on exception.
# KEYWORDS: [PATTERN(6): SafeParser; CONCEPT(9): LLMOutputSanitization; TECH(7): regex;
#            CONCEPT(10): ReasoningModelNormalization]
# LINKS: [USES_API(5): json; USES_API(6): re]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
def safe_json_parse(raw: str) -> dict:
    """
    Parse the first JSON object from a raw LLM output string using five strategies
    applied in order:

    0. Normalise: strip <thinking>/<think>/<reasoning>/<reflection> tag blocks from the
       input using _strip_reasoning_blocks(). All subsequent strategies run on the
       NORMALISED string (reasoning models from xAI, DeepSeek, o1 family).

    1. Plain JSON: the entire stripped normalised string is valid JSON.
    2. Fenced with 'json' language tag: content between ```json ... ``` markers.
    3. Fenced without language: content between bare ``` ... ``` markers.
    4. Prose-embedded — two passes:
       Pass A: greedy regex \\{[\\s\\S]*\\} (fast; handles simple cases).
       Pass B (fallback): locate first '{' index; run json.JSONDecoder().raw_decode() from
       that offset — naturally consumes the first balanced JSON object and ignores trailing
       content, which correctly handles nested braces appearing in reasoning prose.

    If none of the five strategies yield a valid dict, raises JsonParseError with the
    first 200 characters of the ORIGINAL (non-normalised) input so callers can emit
    IMP:10 LDD trace with the authentic raw LLM output.

    The function never mutates the input and has no side effects.
    """

    # START_BLOCK_STRATEGY_0_NORMALIZE: [Pre-processing — strip reasoning-model tag blocks]
    # Run _strip_reasoning_blocks on raw to produce the string all strategies will operate on.
    # raw_snippet is captured from the ORIGINAL raw BEFORE normalisation so operators see
    # the real LLM output in error traces, not the scrubbed version.
    raw_snippet = raw[:200]
    normalized = _strip_reasoning_blocks(raw)
    # END_BLOCK_STRATEGY_0_NORMALIZE

    # START_BLOCK_STRATEGY_PLAIN: [Attempt 1 — direct parse of stripped normalised string]
    candidate = normalized.strip()
    try:
        result = json.loads(candidate)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # END_BLOCK_STRATEGY_PLAIN

    # START_BLOCK_STRATEGY_FENCED_JSON: [Attempt 2 — ```json ... ``` fence stripping]
    fenced_json_match = re.search(r"```json\s*([\s\S]*?)```", normalized, re.IGNORECASE)
    if fenced_json_match:
        inner = fenced_json_match.group(1).strip()
        try:
            result = json.loads(inner)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    # END_BLOCK_STRATEGY_FENCED_JSON

    # START_BLOCK_STRATEGY_FENCED_BARE: [Attempt 3 — bare ``` ... ``` fence stripping]
    fenced_bare_match = re.search(r"```\s*([\s\S]*?)```", normalized)
    if fenced_bare_match:
        inner = fenced_bare_match.group(1).strip()
        try:
            result = json.loads(inner)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    # END_BLOCK_STRATEGY_FENCED_BARE

    # START_BLOCK_STRATEGY_PROSE_EMBEDDED: [Attempt 4 — extract first {...} block; two-pass]
    # Pass A: greedy regex (fast path — handles simple prose-embedded cases)
    brace_match = re.search(r"\{[\s\S]*\}", normalized)
    if brace_match:
        inner = brace_match.group(0)
        try:
            result = json.loads(inner)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Pass B: raw_decode fallback — finds the first '{' and lets the decoder consume
    # exactly one balanced JSON object, ignoring any trailing text.
    # This correctly handles cases where Pass A's greedy match captures too much text
    # (e.g. reasoning prose containing example dicts followed by the real output object).
    first_brace_idx = normalized.find("{")
    if first_brace_idx != -1:
        try:
            decoder = json.JSONDecoder()
            result, _ = decoder.raw_decode(normalized, first_brace_idx)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    # END_BLOCK_STRATEGY_PROSE_EMBEDDED

    # START_BLOCK_RAISE_ERROR: [All strategies exhausted — raise JsonParseError with ORIGINAL snippet]
    # raw_snippet is set to first 200 chars of the ORIGINAL raw argument (captured in Strategy 0 block)
    # so that callers logging at IMP:10 see the authentic LLM output, not the normalised version.
    raise JsonParseError(raw_snippet)
    # END_BLOCK_RAISE_ERROR
# END_FUNCTION_safe_json_parse
