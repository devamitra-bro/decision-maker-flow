# FILE: src/server/middleware.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: HTTP middleware for the brainstorm MCP server. Provides CorrelationIdMiddleware
#          which reads, validates, and propagates X-Correlation-ID headers per §9.2.
#          Ensures every request has a correlation_id attached to request.state and every
#          response (including errors) carries the X-Correlation-ID response header.
# SCOPE: CorrelationIdMiddleware class implementing BaseHTTPMiddleware; header validation
#        regex; log-safe fingerprinting for malformed values.
# INPUT: Incoming HTTP requests (X-Correlation-ID header, optional).
# OUTPUT: request.state.correlation_id set; X-Correlation-ID response header added.
# KEYWORDS: [DOMAIN(8): HTTP_Middleware; TECH(9): Starlette_BaseHTTPMiddleware;
#            CONCEPT(9): CorrelationID; PATTERN(8): RequestStateInjection;
#            CONCEPT(8): LogSafeFingerprint; TECH(8): uuid4_fallback]
# LINKS: [USES_API(9): starlette.middleware.base.BaseHTTPMiddleware;
#         USES_API(8): uuid.uuid4]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §9.2 (Correlation-ID middleware)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Every response (2xx, 4xx, 5xx) carries X-Correlation-ID header.
# - request.state.correlation_id is ALWAYS set before downstream handlers run.
# - Raw malformed header value is NEVER logged — only sha256:<8hex> fingerprint.
# - Regex: ^[a-zA-Z0-9_-]{8,64}$ — match -> use as-is; no-match -> uuid4 + log IMP:5.
# - Missing header -> uuid4, no log (not an error, just a missing header).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why BaseHTTPMiddleware and not a pure ASGI middleware?
# A: BaseHTTPMiddleware is idiomatic Starlette and integrates cleanly with FastAPI.
#    Pure ASGI middleware would require managing request/response streaming manually
#    and would not benefit from Starlette's request/response abstractions.
# Q: Why validate the X-Correlation-ID regex rather than accepting any non-empty string?
# A: Unrestricted headers could carry shell metacharacters or binary data that corrupts
#    log pipelines. The regex mirrors a safe alphanumeric+hyphen+underscore character set
#    with a minimum length (8 chars) sufficient for entropy and maximum (64) for sanity.
# Q: Why log sha256:<8hex> of the malformed value instead of the raw value?
# A: The raw header could contain HMAC tokens, session IDs, or other sensitive data
#    if a misconfigured client accidentally sends them in this header. Fingerprinting
#    provides enough entropy to identify the exact request in logs without exposure.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: CorrelationIdMiddleware per §9.2.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 9 [Starlette middleware: reads/validates X-Correlation-ID, propagates in state + response] => CorrelationIdMiddleware
# END_MODULE_MAP
#
# START_USE_CASES:
# - [CorrelationIdMiddleware]: incoming request -> validate header -> attach to state ->
#   call downstream -> copy correlation_id into response header -> return
# END_USE_CASES

import hashlib
import logging
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Compiled regex for valid X-Correlation-ID values per §9.2
_CORRELATION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


def _header_fp(raw: str) -> str:
    """Return sha256:<8hex> fingerprint of a header value for safe log inclusion."""
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


# START_FUNCTION_CorrelationIdMiddleware
# START_CONTRACT:
# PURPOSE: Starlette middleware that validates X-Correlation-ID header and attaches
#          a correlation_id to request.state. After the downstream handler returns,
#          copies correlation_id into the response X-Correlation-ID header.
#          Malformed header: generates uuid4 + logs IMP:5 with header fingerprint.
#          Missing header: generates uuid4 silently (no log).
# INPUTS:
#   - HTTP request with optional X-Correlation-ID header => request: Request
# OUTPUTS:
#   - Mutated request.state.correlation_id (str); X-Correlation-ID in response headers.
# SIDE_EFFECTS: Logs at IMP:5 when header is malformed (never logs raw header value).
# KEYWORDS: [PATTERN(9): Middleware; CONCEPT(9): CorrelationID; TECH(8): BaseHTTPMiddleware]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware implementing §9.2 Correlation-ID propagation.

    Processing flow per request:
    1. Read X-Correlation-ID header from the incoming request.
    2. If present and matches ^[a-zA-Z0-9_-]{8,64}$: use the header value as-is.
    3. If present but does NOT match: log IMP:5 with sha256:<8hex> fingerprint of
       the malformed value; generate uuid4.hex as replacement.
    4. If absent: generate uuid4.hex silently.
    5. Attach the resolved correlation_id to request.state.correlation_id.
    6. Call the downstream handler (next_call).
    7. Set response.headers["X-Correlation-ID"] = correlation_id on the returned response.
       This step runs even on exception-derived responses (FastAPI exception handlers
       ensure errors are wrapped in responses before reaching middleware dispatch).
    """

    # START_BLOCK_DISPATCH: [Core middleware dispatch logic]
    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Middleware dispatch: validate + inject correlation_id, propagate to response.

        Implements the full §9.2 flow. The correlation_id is set on request.state
        before calling call_next so handlers can access it synchronously without
        needing to parse headers themselves.
        """

        # START_BLOCK_HEADER_READ: [Read and validate X-Correlation-ID]
        raw_cid = request.headers.get("X-Correlation-ID")

        if raw_cid is None:
            # Missing header: generate silently
            correlation_id = uuid.uuid4().hex
        elif _CORRELATION_ID_RE.match(raw_cid):
            # Valid header: use as-is
            correlation_id = raw_cid
        else:
            # Malformed header: fingerprint + log + replace with uuid4
            fp = _header_fp(raw_cid)
            logger.warning(
                f"[BRAINSTORM][IMP:5][Trace][MalformedCorrelationID] "
                f"got_fp={fp} replaced_with=uuid4 [WARN]"
            )
            correlation_id = uuid.uuid4().hex
        # END_BLOCK_HEADER_READ

        # START_BLOCK_STATE_INJECTION: [Attach correlation_id to request.state]
        request.state.correlation_id = correlation_id
        # END_BLOCK_STATE_INJECTION

        # START_BLOCK_DOWNSTREAM_CALL: [Call downstream handler and get response]
        response: Response = await call_next(request)
        # END_BLOCK_DOWNSTREAM_CALL

        # START_BLOCK_RESPONSE_HEADER: [Copy correlation_id into response header]
        response.headers["X-Correlation-ID"] = correlation_id
        # END_BLOCK_RESPONSE_HEADER

        return response

    # END_BLOCK_DISPATCH

# END_FUNCTION_CorrelationIdMiddleware
