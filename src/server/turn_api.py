# FILE: src/server/turn_api.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: FastAPI APIRouter with all HTTP handlers for the brainstorm MCP server:
#          POST /turn (authenticated), POST /done (authenticated), GET /healthz (public),
#          GET /readyz (public), GET /metrics (public). Implements the 15-step happy path
#          from plan §4.1, /done idempotency from §4.2, and §4.3/§4.4 health probes.
# SCOPE: Pydantic request/response models; APIRouter; handler functions with LDD logging;
#        LLM stream aggregation; idempotency cache integration; metrics emission.
# INPUT: HTTP requests; authenticated claims from require_service Depends;
#        checkpointer, metrics, idempotency_cache from app.state.
# OUTPUT: JSON responses: TurnResponse, DoneResponse, healthz/readyz dicts, Prometheus text.
# KEYWORDS: [DOMAIN(10): HTTP_Handlers; TECH(10): FastAPI; CONCEPT(10): StreamAggregation;
#            PATTERN(10): DependencyInjection; CONCEPT(9): Idempotency; TECH(9): Prometheus;
#            PATTERN(9): Auth_Depends; CONCEPT(8): LDD_Telemetry; TECH(8): httpx_probe]
# LINKS: [USES_API(10): src.server.auth.require_service;
#         USES_API(9): src.features.decision_maker.graph.stream_session;
#         USES_API(9): src.features.decision_maker.graph.stream_resume_session;
#         USES_API(9): src.server.checkpoint_factory.TouchingCheckpointer;
#         USES_API(9): src.server.idempotency.IdempotencyCache;
#         USES_API(9): src.server.metrics.Metrics;
#         USES_API(8): prometheus_client.generate_latest]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §4.1–§4.4 (data flows), §9.1 (idempotency)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - /turn and /done require Depends(require_service("brainstorm")) — no exceptions.
# - /healthz, /readyz, /metrics have NO auth dependency.
# - session_fp in all log lines = sha256:<8hex> of session_id — never raw session_id.
# - token_fp in all log lines = sha256:<8hex> of raw Bearer token — never raw token.
# - Raw message body is NEVER logged at IMP >= 5.
# - asyncio.wait_for wraps the entire stream iteration; timeout -> LLMTimeoutError.
# - New session: uuid.uuid4().hex is the new session_id (not a UUID-v4 with dashes).
# - Existing session with no checkpoint -> HTTP 404.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why use stream_session / stream_resume_session (streaming) instead of the non-streaming
#    start_session / resume_session?
# A: Plan §4.1 step 11 explicitly requires the streaming API. Streaming allows asyncio.wait_for
#    to interleave with network I/O and provides finer-grained cancellation. The non-streaming
#    API blocks the event loop across the entire LLM invocation.
# Q: Why is new session_id uuid.uuid4().hex (no dashes) rather than str(uuid.uuid4())?
# A: The plan says "uuid.uuid4().hex" specifically. The hex form (32 hex chars, no dashes)
#    avoids the UUID-v4 regex check in auth.py (session_id in claims is UUID-v4 with dashes;
#    the new ID generated HERE is independent — it's used as a thread_id, not a claim field).
# Q: Why does handle_done NOT check for session existence before decrementing active_sessions?
# A: §4.2 specifies idempotent behavior: swallow NotFound, return 200 always. The gauge
#    decrement only happens IF the delete confirmed existence (via a check before delete).
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: full router with all 5 handlers,
#               Pydantic models, LDD logging, idempotency integration, metrics emission.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS  7 [Pydantic request model: session_id (optional), message (1–4000 chars)] => TurnRequest
# CLASS  7 [Pydantic response model: session_id, reply, state, metadata] => TurnResponse
# CLASS  6 [Pydantic request model: session_id (required)] => DoneRequest
# CLASS  6 [Pydantic response model: acknowledged bool] => DoneResponse
# FUNC  10 [POST /turn handler: 15-step happy path + idempotency + LDD logging] => handle_turn
# FUNC   9 [POST /done handler: idempotent session close + metrics] => handle_done
# FUNC   5 [GET /healthz handler: public liveness probe, no IO] => handle_healthz
# FUNC   8 [GET /readyz handler: public readiness probe with checkpointer + LLM pings] => handle_readyz
# FUNC   5 [GET /metrics handler: public Prometheus scrape endpoint] => handle_metrics
# END_MODULE_MAP
#
# START_USE_CASES:
# - [handle_turn]: Krab -> POST /turn -> auth -> idempotency check -> LLM stream -> cache -> 200
# - [handle_done]: Krab -> POST /done -> auth -> delete checkpoint -> 200
# - [handle_healthz]: k8s liveness -> GET /healthz -> 200 {"status":"ok"}
# - [handle_readyz]: k8s readiness -> GET /readyz -> 200 or 503
# - [handle_metrics]: Prometheus -> GET /metrics -> text/plain metrics exposition
# END_USE_CASES

import asyncio
import hashlib
import logging
import time
import uuid
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from prometheus_client import CollectorRegistry, generate_latest
from pydantic import BaseModel, Field

from src.server.auth import AuthError, TokenClaims, require_service
from src.server.errors import LLMTimeoutError

logger = logging.getLogger(__name__)

router = APIRouter()


# ───────────────────────────────────────────────
# Helper utilities
# ───────────────────────────────────────────────

def _session_fp(session_id: str) -> str:
    """Return sha256:<8hex> fingerprint of session_id for safe log inclusion (AC6)."""
    return "sha256:" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]


def _token_fp(raw_bearer: Optional[str]) -> str:
    """Return sha256:<8hex> fingerprint of the raw Bearer token (AC6)."""
    if raw_bearer is None:
        return "sha256:00000000"
    return "sha256:" + hashlib.sha256(raw_bearer.encode("utf-8")).hexdigest()[:8]


# ───────────────────────────────────────────────
# Pydantic models
# ───────────────────────────────────────────────

# START_FUNCTION_TurnRequest
# START_CONTRACT:
# PURPOSE: Validated Pydantic model for POST /turn request body.
# KEYWORDS: [PATTERN(8): PydanticModel; CONCEPT(7): InputValidation]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
class TurnRequest(BaseModel):
    """
    POST /turn request body. session_id is optional — omit to start a new session.
    message is required and bounded to 1–4000 characters.
    """
    session_id: Optional[str] = Field(default=None, description="Existing session thread_id or null for new session")
    message: str = Field(min_length=1, max_length=4000, description="User message (1–4000 chars)")
# END_FUNCTION_TurnRequest


# START_FUNCTION_TurnResponse
# START_CONTRACT:
# PURPOSE: Validated Pydantic response model for POST /turn. state is "running" while
#          the graph is awaiting user input, "done" when the session completes.
# KEYWORDS: [PATTERN(8): PydanticModel]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
class TurnResponse(BaseModel):
    """
    POST /turn response. reply contains the latest graph output (question or final answer).
    state indicates whether the session expects another /turn or is complete.
    metadata carries optional supplementary fields (turn_n, reply_chars, etc.).
    """
    session_id: str
    reply: str
    state: Literal["running", "done"]
    metadata: dict
# END_FUNCTION_TurnResponse


# START_FUNCTION_DoneRequest
# START_CONTRACT:
# PURPOSE: Validated Pydantic model for POST /done request body.
# KEYWORDS: [PATTERN(8): PydanticModel]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
class DoneRequest(BaseModel):
    """POST /done request body. session_id identifies the session to close."""
    session_id: str
# END_FUNCTION_DoneRequest


# START_FUNCTION_DoneResponse
# START_CONTRACT:
# PURPOSE: Validated Pydantic response model for POST /done.
# KEYWORDS: [PATTERN(8): PydanticModel]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
class DoneResponse(BaseModel):
    """POST /done response. acknowledged is always True (idempotent)."""
    acknowledged: bool
# END_FUNCTION_DoneResponse


# ───────────────────────────────────────────────
# Handlers
# ───────────────────────────────────────────────

# START_FUNCTION_handle_turn
# START_CONTRACT:
# PURPOSE: Authenticated POST /turn handler implementing the 15-step happy path (§4.1).
#          Validates auth via require_service Depends; reads body; routes to new-session
#          or resume-session LangGraph stream; wraps in asyncio.wait_for; aggregates reply;
#          updates metrics; stores idempotency cache entry; returns TurnResponse.
# INPUTS:
#   - HTTP POST body => body: TurnRequest
#   - FastAPI Request (for state access: checkpointer, metrics, idempotency_cache) => request: Request
#   - Authentication claims from Depends => claims: TokenClaims
#   - Raw Authorization header (for LDD fingerprint logging) => authorization: Optional[str]
# OUTPUTS:
#   - TurnResponse: 200 JSON response with session_id, reply, state, metadata
# SIDE_EFFECTS:
#   - Emits LDD logs at IMP:4, IMP:7, IMP:9.
#   - Updates Prometheus metrics: turns_total, turn_duration_seconds, llm_roundtrip_seconds,
#     active_sessions (if new session), idempotent_hits_total (if cache hit).
#   - Advances checkpointer state via stream_session / stream_resume_session.
#   - Inserts into IdempotencyCache on first call.
# KEYWORDS: [PATTERN(10): HttpHandler; CONCEPT(10): StreamAggregation; CONCEPT(9): Idempotency;
#            PATTERN(9): LDD_Telemetry; CONCEPT(8): NewVsResumeSession]
# COMPLEXITY_SCORE: 9
# END_CONTRACT
@router.post("/turn", response_model=TurnResponse)
async def handle_turn(
    body: TurnRequest,
    request: Request,
    claims: TokenClaims = Depends(require_service("brainstorm")),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> TurnResponse:
    """
    POST /turn — authenticated LangGraph session handler.

    Implements the 15-step plan §4.1 happy path:
    1. Log Request-In at IMP:4.
    2. Auth is resolved by Depends(require_service) — already validated in claims.
    3-6. Token verification already done by require_service Depends.
    7. Log Verify-OK at IMP:9 (BELIEF state).
    8. Parse and validate body (done by Pydantic TurnRequest model).
    9. Route: new session (no session_id) or resume (session_id in body).
    10. Check idempotency cache (header key or internal key).
    11. Stream graph execution wrapped in asyncio.wait_for.
    12. Aggregate reply from stream sentinel.
    13. Update metrics.
    14. Log Turn-End at IMP:7.
    15. Return TurnResponse.
    """
    start_time = time.monotonic()
    cid = getattr(request.state, "correlation_id", "unknown")

    # START_BLOCK_REQUEST_IN: [IMP:4 Request-In log]
    logger.info(
        f"[BRAINSTORM][IMP:4][handle_turn][HTTP][Request-In] "
        f"path=/turn correlation_id={cid} [OK]"
    )
    # END_BLOCK_REQUEST_IN

    # START_BLOCK_AUTH_LOG: [IMP:9 AI Belief State after successful auth]
    token_fingerprint = _token_fp(authorization)
    session_fp_claim = _session_fp(claims.session_id)
    now_unix = int(time.time())
    exp_delta = claims.exp - now_unix
    logger.info(
        f"[BRAINSTORM][IMP:9][handle_turn][Auth][Verify-OK][BELIEF] "
        f"token_fp={token_fingerprint} session_fp={session_fp_claim} "
        f"service_id={claims.service_id} exp_delta_s={exp_delta} [OK]"
    )
    # END_BLOCK_AUTH_LOG

    # START_BLOCK_STATE_ACCESS: [Retrieve app.state dependencies]
    checkpointer = request.app.state.checkpointer
    metrics = request.app.state.metrics
    idempotency_cache = request.app.state.idempotency_cache
    llm_client = request.app.state.llm_client
    registry: CollectorRegistry = request.app.state.registry
    # END_BLOCK_STATE_ACCESS

    # START_BLOCK_SESSION_ROUTING: [Determine new vs existing session; validate existing]
    is_new_session = body.session_id is None
    if is_new_session:
        thread_id = uuid.uuid4().hex
    else:
        thread_id = body.session_id
        # Validate checkpoint exists for provided session_id
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "session_not_found",
                    "correlation_id": cid,
                    "session_fp": _session_fp(thread_id),
                },
            )
    # END_BLOCK_SESSION_ROUTING

    # START_BLOCK_TURN_N: [Determine current turn number for idempotency key]
    if is_new_session:
        turn_n = 0
    else:
        # Read checkpoint state to get message count as turn_n proxy
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is not None and checkpoint_tuple.checkpoint:
            channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
            messages = channel_values.get("messages", [])
            turn_n = len(messages)
        else:
            turn_n = 0
    # END_BLOCK_TURN_N

    # START_BLOCK_IDEMPOTENCY_CHECK: [Check cache before running LLM]
    idempotency_key_header = request.headers.get("Idempotency-Key")
    idem_key_from_header = idempotency_cache.make_key_from_header(idempotency_key_header)
    if idem_key_from_header is not None:
        cache_key = idem_key_from_header
        cache_source = "header"
    else:
        cache_key = idempotency_cache.make_key_from_tuple(thread_id, body.message, turn_n)
        cache_source = "internal"

    cached_result = await idempotency_cache.get(cache_key)
    if cached_result is not None:
        metrics.idempotent_hits_total.labels(source=cache_source).inc()
        logger.info(
            f"[BRAINSTORM][IMP:5][handle_turn][Idempotency][CacheHit] "
            f"source={cache_source} session_fp={_session_fp(thread_id)} [OK]"
        )
        elapsed = time.monotonic() - start_time
        metrics.turn_duration_seconds.observe(elapsed)
        logger.info(
            f"[BRAINSTORM][IMP:4][handle_turn][HTTP][Response-Out] "
            f"path=/turn status=200 correlation_id={cid} cached=true [OK]"
        )
        return TurnResponse(**cached_result)
    # END_BLOCK_IDEMPOTENCY_CHECK

    # START_BLOCK_TURN_START_LOG: [IMP:7 Turn-Start boundary]
    session_fp_thread = _session_fp(thread_id)
    logger.info(
        f"[BRAINSTORM][IMP:7][handle_turn][Turn][Start] "
        f"session_fp={session_fp_thread} new={is_new_session} [OK]"
    )
    # END_BLOCK_TURN_START_LOG

    # START_BLOCK_LLM_STREAM: [Stream graph; wrap in asyncio.wait_for for timeout]
    from src.features.decision_maker.graph import stream_resume_session, stream_session  # noqa: PLC0415

    llm_start = time.monotonic()
    reply = ""
    final_state: Literal["running", "done"] = "running"

    cfg = request.app.state.cfg
    timeout_sec = cfg.turn_timeout_sec

    async def _collect_stream() -> tuple:
        """Collect all stream chunks and return (reply_text, state_str)."""
        nonlocal reply, final_state
        collected_reply = ""
        detected_state: Literal["running", "done"] = "running"

        if is_new_session:
            gen = stream_session(
                user_input=body.message,
                thread_id=thread_id,
                checkpointer=checkpointer,
                llm_client=llm_client,
            )
        else:
            gen = stream_resume_session(
                user_answer=body.message,
                thread_id=thread_id,
                checkpointer=checkpointer,
                llm_client=llm_client,
            )

        async for chunk in gen:
            if isinstance(chunk, dict):
                if chunk.get("__awaiting_user__") is True:
                    last_q = chunk.get("last_question", "")
                    collected_reply = last_q
                    detected_state = "running"
                elif chunk.get("__done__") is True:
                    final_ans = chunk.get("final_answer", "")
                    collected_reply = final_ans
                    detected_state = "done"

        return collected_reply, detected_state

    try:
        reply, final_state = await asyncio.wait_for(
            _collect_stream(),
            timeout=float(timeout_sec),
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[BRAINSTORM][IMP:9][handle_turn][LLM][Timeout][BELIEF] "
            f"session_fp={session_fp_thread} timeout_sec={timeout_sec} [FAIL]"
        )
        raise LLMTimeoutError(f"LLM stream timed out after {timeout_sec}s")

    llm_elapsed = time.monotonic() - llm_start
    metrics.llm_roundtrip_seconds.observe(llm_elapsed)
    # END_BLOCK_LLM_STREAM

    # START_BLOCK_METRICS_UPDATE: [Update Prometheus metrics for this turn]
    total_elapsed = time.monotonic() - start_time
    metrics.turns_total.labels(state=final_state).inc()
    metrics.turn_duration_seconds.observe(total_elapsed)
    if is_new_session:
        metrics.active_sessions.inc()
    # END_BLOCK_METRICS_UPDATE

    # START_BLOCK_TURN_END_LOG: [IMP:7 Turn-End boundary with full context]
    reply_chars = len(reply)
    logger.info(
        f"[BRAINSTORM][IMP:7][handle_turn][Turn][End][OK] "
        f"session_fp={session_fp_thread} turn_n={turn_n} "
        f"duration_ms={int(total_elapsed * 1000)} reply_chars={reply_chars} "
        f"token_fp={token_fingerprint} [OK]"
    )
    # END_BLOCK_TURN_END_LOG

    # START_BLOCK_RESPONSE_BUILD: [Construct response and cache it]
    response_data = TurnResponse(
        session_id=thread_id,
        reply=reply,
        state=final_state,
        metadata={
            "turn_n": turn_n,
            "reply_chars": reply_chars,
            "duration_ms": int(total_elapsed * 1000),
        },
    )

    await idempotency_cache.set(cache_key, response_data.model_dump())

    logger.info(
        f"[BRAINSTORM][IMP:4][handle_turn][HTTP][Response-Out] "
        f"path=/turn status=200 correlation_id={cid} state={final_state} [OK]"
    )
    # END_BLOCK_RESPONSE_BUILD

    return response_data

# END_FUNCTION_handle_turn


# START_FUNCTION_handle_done
# START_CONTRACT:
# PURPOSE: Authenticated POST /done handler — idempotent session close per §4.2.
#          Swallows NotFound (session already deleted or never existed).
#          Decrements active_sessions gauge ONLY if delete confirmed the session existed.
#          Always returns DoneResponse(acknowledged=True).
# INPUTS:
#   - POST body with session_id => body: DoneRequest
#   - Request for state access => request: Request
#   - Authentication claims => claims: TokenClaims
# OUTPUTS:
#   - DoneResponse: 200 with acknowledged=True
# SIDE_EFFECTS: Logs at IMP:4. Updates metrics.done_total; conditionally active_sessions.dec().
# KEYWORDS: [PATTERN(8): IdempotentHandler; CONCEPT(8): SessionClose]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@router.post("/done", response_model=DoneResponse)
async def handle_done(
    body: DoneRequest,
    request: Request,
    claims: TokenClaims = Depends(require_service("brainstorm")),
) -> DoneResponse:
    """
    POST /done — idempotent session termination endpoint per §4.2.

    Checks if the session checkpoint exists, then deletes it (if found).
    Always returns 200 acknowledged=True — even if the session was not found
    (already deleted, never existed). Decrements active_sessions gauge only
    on confirmed deletion.
    """
    cid = getattr(request.state, "correlation_id", "unknown")
    logger.info(
        f"[BRAINSTORM][IMP:4][handle_done][HTTP][Request-In] "
        f"path=/done correlation_id={cid} [OK]"
    )

    checkpointer = request.app.state.checkpointer
    metrics = request.app.state.metrics
    session_fp = _session_fp(body.session_id)

    # START_BLOCK_CHECK_EXISTS: [Check whether checkpoint exists before delete]
    config = {"configurable": {"thread_id": body.session_id}}
    checkpoint_tuple = await checkpointer.aget_tuple(config)
    session_existed = checkpoint_tuple is not None
    # END_BLOCK_CHECK_EXISTS

    # START_BLOCK_DELETE_SESSION: [Delete checkpoint; swallow NotFound]
    if session_existed:
        try:
            await checkpointer.adelete_thread(body.session_id)
            metrics.active_sessions.dec()
            logger.info(
                f"[BRAINSTORM][IMP:5][handle_done][Session][Deleted] "
                f"session_fp={session_fp} [OK]"
            )
        except Exception as exc:
            # Log but do not surface — /done must remain idempotent
            logger.warning(
                f"[BRAINSTORM][IMP:6][handle_done][Session][DeleteFailed] "
                f"session_fp={session_fp} err={exc!r} [WARN]"
            )
    else:
        logger.info(
            f"[BRAINSTORM][IMP:5][handle_done][Session][NotFound] "
            f"session_fp={session_fp} already_deleted=true [OK]"
        )
    # END_BLOCK_DELETE_SESSION

    # START_BLOCK_DONE_METRICS: [Increment done_total regardless of existence]
    metrics.done_total.inc()
    # END_BLOCK_DONE_METRICS

    logger.info(
        f"[BRAINSTORM][IMP:4][handle_done][HTTP][Response-Out] "
        f"path=/done status=200 correlation_id={cid} [OK]"
    )
    return DoneResponse(acknowledged=True)

# END_FUNCTION_handle_done


# START_FUNCTION_handle_healthz
# START_CONTRACT:
# PURPOSE: Public GET /healthz liveness probe — no IO, always 200 per §4.3.
#          No auth dependency. No metric emission (noisy for liveness probes).
# INPUTS: None
# OUTPUTS: dict {"status": "ok"} with HTTP 200
# KEYWORDS: [PATTERN(5): HealthProbe; CONCEPT(7): Liveness]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
@router.get("/healthz", include_in_schema=True)
async def handle_healthz(request: Request) -> dict:
    """
    Public liveness probe. Returns 200 {"status":"ok"} immediately without any IO.
    No authentication required. Does not emit metrics (liveness probes are high-frequency
    and would add noise to the turn/session metrics).
    """
    return {"status": "ok"}

# END_FUNCTION_handle_healthz


# START_FUNCTION_handle_readyz
# START_CONTRACT:
# PURPOSE: Public GET /readyz readiness probe per §4.4. Performs two checks:
#          1. checkpointer.ping() — SQLite SELECT 1 (2s budget shared with LLM probe).
#          2. httpx.get(cfg.gateway_llm_proxy_url/healthz, timeout=2.0).
#          Returns 200 {"status":"ready",...} if both pass; 503 on any failure.
#          Updates readyz_checks_total metric.
# INPUTS: Request for state access (cfg, checkpointer, metrics, registry)
# OUTPUTS: dict with status + component diagnostics; HTTP 200 or 503
# KEYWORDS: [PATTERN(7): ReadinessProbe; CONCEPT(8): HealthCheck; TECH(7): httpx_probe]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
@router.get("/readyz", include_in_schema=True)
async def handle_readyz(request: Request) -> dict:
    """
    Public readiness probe. Performs synchronous I/O checks on both the SQLite
    checkpointer (ping = SELECT 1) and the LLM gateway (httpx GET /healthz with 2s timeout).
    Returns 200 {"status":"ready"} only when both pass. Returns 503 on any failure,
    with a diagnostics dict indicating which component failed.
    """
    checkpointer = request.app.state.checkpointer
    cfg = request.app.state.cfg
    metrics = request.app.state.metrics

    # START_BLOCK_CHECKPOINTER_PING: [Ping SQLite checkpointer]
    try:
        await checkpointer.ping()
        checkpointer_status = "ok"
    except Exception as exc:
        logger.warning(
            f"[BRAINSTORM][IMP:6][handle_readyz][Readyz][CheckpointerFail] "
            f"err={exc!r} [FAIL]"
        )
        checkpointer_status = f"fail:{exc!r}"
        metrics.readyz_checks_total.labels(result="checkpointer_fail").inc()
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "checkpointer": checkpointer_status,
                "llm_gateway": "unknown",
            },
        )
    # END_BLOCK_CHECKPOINTER_PING

    # START_BLOCK_LLM_GATEWAY_PING: [Probe LLM gateway with 2s timeout]
    llm_gateway_url = cfg.gateway_llm_proxy_url.rstrip("/") + "/healthz"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(llm_gateway_url)
        if resp.status_code != 200:
            raise ValueError(f"LLM gateway returned HTTP {resp.status_code}")
        llm_gateway_status = "ok"
    except Exception as exc:
        logger.warning(
            f"[BRAINSTORM][IMP:6][handle_readyz][Readyz][LLMGatewayFail] "
            f"err={exc!r} [FAIL]"
        )
        llm_gateway_status = f"fail:{exc!r}"
        metrics.readyz_checks_total.labels(result="llm_gateway_fail").inc()
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "checkpointer": checkpointer_status,
                "llm_gateway": llm_gateway_status,
            },
        )
    # END_BLOCK_LLM_GATEWAY_PING

    metrics.readyz_checks_total.labels(result="ok").inc()
    return {
        "status": "ready",
        "checkpointer": checkpointer_status,
        "llm_gateway": llm_gateway_status,
    }

# END_FUNCTION_handle_readyz


# START_FUNCTION_handle_metrics
# START_CONTRACT:
# PURPOSE: Public GET /metrics endpoint — returns Prometheus exposition format text.
#          No authentication required. Content-Type: text/plain; version=0.0.4; charset=utf-8.
# INPUTS: Request for registry access (request.app.state.registry)
# OUTPUTS: PlainTextResponse with Prometheus metrics text body.
# KEYWORDS: [PATTERN(5): MetricsEndpoint; TECH(8): prometheus_client_generate_latest]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
@router.get("/metrics", include_in_schema=True)
async def handle_metrics(request: Request):
    """
    Public Prometheus scrape endpoint. Calls generate_latest(registry) on the app's
    CollectorRegistry and returns the Prometheus text format output with the correct
    content-type header for scrape compatibility.
    """
    from fastapi.responses import Response  # noqa: PLC0415

    registry: CollectorRegistry = request.app.state.registry
    metrics_bytes = generate_latest(registry)
    return Response(
        content=metrics_bytes,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

# END_FUNCTION_handle_metrics
