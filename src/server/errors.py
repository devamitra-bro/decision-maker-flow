# FILE: src/server/errors.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Canonical domain exception definitions and HTTP translation for the brainstorm
#          server layer. Defines LLMTimeoutError (new in Slice C). Re-exports AuthError
#          from src.server.auth and ConfigError from src.server.checkpoint_factory for a
#          single canonical import path via src.server.errors.*. Provides to_http_exception()
#          which translates domain errors to FastAPI HTTPException with correlation_id.
# SCOPE: Exception class definitions (LLMTimeoutError); re-exports (AuthError, ConfigError);
#        HTTP translation function to_http_exception().
# INPUT: Domain exceptions + correlation_id string.
# OUTPUT: HTTPException with structured JSON body {"error": reason, "correlation_id": cid}.
# KEYWORDS: [DOMAIN(9): ErrorHandling; TECH(9): FastAPI_HTTPException; CONCEPT(9): CorrelationID;
#            PATTERN(8): ExceptionTranslation; CONCEPT(8): DomainExceptions]
# LINKS: [READS_DATA_FROM(9): src.server.auth.AuthError;
#         READS_DATA_FROM(9): src.server.checkpoint_factory.ConfigError;
#         USES_API(9): fastapi.HTTPException]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.3, §9.2
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why re-export AuthError and ConfigError here rather than define them again?
# A: Lower-risk option: AuthError lives in auth.py (Slice A, tested, stable); ConfigError
#    lives in checkpoint_factory.py (Slice B, tested, stable). Re-exporting avoids
#    circular imports and does not break existing callers. src.server.errors becomes the
#    single canonical import path for Slice C onward while preserving backward compat.
# Q: Why does to_http_exception raise for unknown exceptions instead of returning 500?
# A: Unknown exceptions may carry sensitive data in their message. Re-raising lets the
#    global exception handler (which has the correlation_id in scope) decide how to log
#    and translate them — keeping the translation function focused and auditable.
# Q: Why is LLMTimeoutError defined here instead of turn_api.py?
# A: It is a cross-cutting domain exception: the sweeper, health checks, and metrics
#    module may all reference it. Defining it here prevents circular imports.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: LLMTimeoutError; re-exports of
#               AuthError and ConfigError; to_http_exception translator function.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 8  [NEW — raised when asyncio.wait_for times out around LLM graph stream] => LLMTimeoutError
# FUNC  9  [Translates domain exceptions to FastAPI HTTPException with correlation_id] => to_http_exception
# IMPORT 7 [Re-export: AuthError from src.server.auth] => AuthError
# IMPORT 7 [Re-export: ConfigError from src.server.checkpoint_factory] => ConfigError
# END_MODULE_MAP
#
# START_USE_CASES:
# - [LLMTimeoutError]: handle_turn -> asyncio.wait_for timeout -> LLMTimeoutError ->
#   to_http_exception -> HTTPException(408)
# - [to_http_exception]: global exception handler -> to_http_exception(exc, cid) ->
#   HTTPException with correlation_id embedded in body
# - [AuthError re-export]: src.server.errors.AuthError == src.server.auth.AuthError (identity)
# - [ConfigError re-export]: src.server.errors.ConfigError == src.server.checkpoint_factory.ConfigError
# END_USE_CASES

import logging

# Re-exports — lower-risk than moving canonical definition (plan §2.3 rationale above)
from src.server.auth import AuthError  # noqa: F401
from src.server.checkpoint_factory import ConfigError  # noqa: F401

logger = logging.getLogger(__name__)


# START_FUNCTION_LLMTimeoutError
# START_CONTRACT:
# PURPOSE: Domain exception raised when asyncio.wait_for() times out while awaiting
#          the LangGraph stream iteration in handle_turn. Carries no sensitive data.
#          Translated to HTTP 408 by to_http_exception.
# INPUTS: None (standard Exception constructor)
# OUTPUTS: LLMTimeoutError instance
# KEYWORDS: [CONCEPT(9): LLMTimeout; PATTERN(7): DomainException]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
class LLMTimeoutError(Exception):
    """
    Raised when the LLM graph stream iteration does not complete within the configured
    turn_timeout_sec window (cfg.turn_timeout_sec). The asyncio.wait_for() call in
    handle_turn catches asyncio.TimeoutError and re-raises as LLMTimeoutError.

    This exception carries no user data or secrets — it is safe to log its str() at
    any IMP level. HTTP translation: to_http_exception -> HTTPException(408).
    """

# END_FUNCTION_LLMTimeoutError


# START_FUNCTION_to_http_exception
# START_CONTRACT:
# PURPOSE: Translate a domain exception into a FastAPI HTTPException with structured
#          JSON body containing {"error": reason, "correlation_id": correlation_id}.
#          Determines HTTP status code from exception type and AuthError.reason.
#          For unknown exceptions: re-raises without wrapping to preserve the
#          original traceback for the outer handler.
# INPUTS:
#   - Domain exception to translate => exc: Exception
#   - Correlation-ID for this request (embedded in response body) => correlation_id: str
# OUTPUTS:
#   - HTTPException: with status_code and detail dict ready for FastAPI response
# SIDE_EFFECTS: Logs at IMP:9 for LLMTimeoutError (AI Belief State). Re-raises on unknown.
# KEYWORDS: [PATTERN(9): ExceptionTranslation; CONCEPT(9): CorrelationID;
#            TECH(8): FastAPI_HTTPException; CONCEPT(8): HTTPStatusMapping]
# LINKS: [USES_API(9): fastapi.HTTPException; READS_DATA_FROM(9): AuthError.reason]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def to_http_exception(exc: Exception, correlation_id: str):
    """
    Translate a domain exception to a FastAPI HTTPException with a structured body.

    Mapping rules (per plan §2.3 and §9.2):
    - AuthError("missing_authorization"|"malformed"|"bad_signature"|"bad_version"|"expired")
      → HTTP 401 with error=reason
    - AuthError("wrong_service") → HTTP 403 with error=wrong_service
    - AuthError("missing_session") → HTTP 401 with error=missing_session
    - LLMTimeoutError → HTTP 408 with error=llm_timeout
    - ConfigError → HTTP 500 with error=config_error
    - Any other exception: re-raise unchanged (caller's outer handler decides)

    correlation_id is always embedded in the response body so the client can correlate
    error responses with distributed traces without raw session data.
    """
    from fastapi import HTTPException  # noqa: PLC0415 — lazy import avoids circular at module load

    # START_BLOCK_AUTH_ERROR_TRANSLATION: [Map AuthError reasons to HTTP 401/403]
    if isinstance(exc, AuthError):
        if exc.reason == "wrong_service":
            status_code = 403
        else:
            # All other AuthError reasons -> 401
            # Covers: malformed, bad_version, bad_signature, expired, missing_session
            status_code = 401
        return HTTPException(
            status_code=status_code,
            detail={"error": exc.reason, "correlation_id": correlation_id},
        )
    # END_BLOCK_AUTH_ERROR_TRANSLATION

    # START_BLOCK_LLM_TIMEOUT_TRANSLATION: [Map LLMTimeoutError -> HTTP 408]
    if isinstance(exc, LLMTimeoutError):
        logger.warning(
            f"[BRAINSTORM][IMP:9][to_http_exception][LLM][Timeout][BELIEF] "
            f"LLM call timed out. correlation_id={correlation_id} [FAIL]"
        )
        return HTTPException(
            status_code=408,
            detail={"error": "llm_timeout", "correlation_id": correlation_id},
        )
    # END_BLOCK_LLM_TIMEOUT_TRANSLATION

    # START_BLOCK_CONFIG_ERROR_TRANSLATION: [Map ConfigError -> HTTP 500]
    if isinstance(exc, ConfigError):
        logger.error(
            f"[BRAINSTORM][IMP:9][to_http_exception][Config][Fatal][BELIEF] "
            f"Config error reached HTTP layer. correlation_id={correlation_id} "
            f"exc={exc!r} [FAIL]"
        )
        return HTTPException(
            status_code=500,
            detail={"error": "config_error", "correlation_id": correlation_id},
        )
    # END_BLOCK_CONFIG_ERROR_TRANSLATION

    # START_BLOCK_UNKNOWN_RE_RAISE: [Unknown exceptions are re-raised unchanged]
    raise exc
    # END_BLOCK_UNKNOWN_RE_RAISE

# END_FUNCTION_to_http_exception
