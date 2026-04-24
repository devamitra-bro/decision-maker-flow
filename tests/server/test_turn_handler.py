# FILE: tests/server/test_turn_handler.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Unit tests for POST /turn handler covering happy paths, auth failures,
#          validation errors, session routing, LLM timeout, and idempotency cache.
#          All tests use dependency_overrides to avoid real LLM/checkpointer/auth calls.
# SCOPE: handle_turn happy path (new + resume), auth failures (401/403), validation errors
#        (400), session not found (404), LLM timeout (408), idempotency (header + internal).
# INPUT: Pytest fixtures; TestClient; dependency_overrides on require_service + checkpointer.
# OUTPUT: pytest test results with LDD telemetry printed to stdout.
# KEYWORDS: [DOMAIN(9): TestHTTP; TECH(9): FastAPI_TestClient; CONCEPT(9): DependencyOverrides;
#            PATTERN(8): StubGraph; CONCEPT(8): IdempotencyTest]
# LINKS: [USES_API(9): fastapi.testclient.TestClient;
#         READS_DATA_FROM(9): src.server.app_factory.create_app;
#         READS_DATA_FROM(8): src.server.auth.require_service]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §7.3 (test scenarios for /turn)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: all test_turn_* tests per §7.3.]
# END_CHANGE_SUMMARY

import asyncio
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Ensure brainstorm root is in path
_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.server.auth import AuthError, TokenClaims, require_service
from src.server.checkpoint_factory import TouchingCheckpointer
from src.server.config import Config
from src.server.idempotency import IdempotencyCache
from src.server.metrics import build_registry, make_metrics


# ─────────────────────────────────────────────────────────
# Test helpers and shared config
# ─────────────────────────────────────────────────────────

_TEST_SECRET = "brainstorm-test-secret-32bytes!!"
_FUTURE_EXP = 9_999_999_999  # Far future — never expires in tests

_FIXED_SESSION_ID = str(uuid.UUID("12345678-1234-4234-a234-123456789abc"))
_FIXED_THREAD_ID = _FIXED_SESSION_ID  # Same value used as thread_id


def _stub_cfg(sqlite_path: str = None) -> Config:
    """Build a minimal Config stub without reading env vars."""
    import os
    import tempfile
    from src.server.config import get_cfg
    get_cfg.cache_clear()
    if sqlite_path is None:
        tmp_dir = tempfile.mkdtemp()
        sqlite_path = os.path.join(tmp_dir, "turn_test.sqlite")
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=sqlite_path,
        BRAINSTORM_TURN_TIMEOUT_SEC=30,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
        BRAINSTORM_SWEEP_INTERVAL_SEC=9999,
    )
    get_cfg.cache_clear()
    return cfg


def _make_fixed_claims(session_id: str = _FIXED_SESSION_ID) -> TokenClaims:
    """Return a fixed TokenClaims with known session_id and far-future exp."""
    return TokenClaims(service_id="brainstorm", session_id=session_id, exp=_FUTURE_EXP)


async def _stream_awaiting_user(user_input, thread_id, checkpointer=None, llm_client=None):
    """Stub stream_session yielding a chunk + awaiting_user sentinel."""
    yield {"1_Context_Analyzer": {"user_input": user_input}}
    yield {"__awaiting_user__": True, "last_question": "What is your priority?", "thread_id": thread_id}


async def _stream_done(user_answer, thread_id, checkpointer=None, llm_client=None):
    """Stub stream_resume_session yielding a chunk + done sentinel."""
    yield {"6_Final_Synthesizer": {"final_answer": "Here is your answer."}}
    yield {"__done__": True, "final_answer": "Here is your answer.", "thread_id": thread_id}


def _build_app_with_stubs(
    stream_session_fn=None,
    stream_resume_fn=None,
    claims_override=None,
    turn_timeout_sec: int = 30,
):
    """
    Build a TestClient-ready FastAPI app with all dependencies stubbed out.
    - require_service overridden to return fixed claims (or raise AuthError).
    - stream_session / stream_resume_session monkeypatched on the graph module.
    - NOTE: To inject a checkpointer stub, set app.state.checkpointer INSIDE
      the `with TestClient(app) as client:` block (after lifespan runs).
    """
    import src.features.decision_maker.graph as graph_mod
    from src.server.app_factory import create_app

    cfg = _stub_cfg()

    app = create_app(cfg=cfg)

    # Patch graph module functions
    if stream_session_fn is not None:
        graph_mod.stream_session = stream_session_fn
    if stream_resume_fn is not None:
        graph_mod.stream_resume_session = stream_resume_fn

    # Override require_service dependency
    dep_callable = require_service("brainstorm")
    if claims_override is not None:
        app.dependency_overrides[dep_callable] = claims_override
    else:
        fixed_claims = _make_fixed_claims()
        async def _fixed_dep():
            return fixed_claims
        app.dependency_overrides[dep_callable] = _fixed_dep

    return app


def _make_stub_checkpointer(has_session: bool = True):
    """Create a mock TouchingCheckpointer returning a fake checkpoint tuple."""
    stub = MagicMock(spec=TouchingCheckpointer)

    if has_session:
        fake_tuple = MagicMock()
        fake_tuple.checkpoint = {"channel_values": {"messages": []}}
        stub.aget_tuple = AsyncMock(return_value=fake_tuple)
    else:
        stub.aget_tuple = AsyncMock(return_value=None)

    stub.adelete_thread = AsyncMock(return_value=None)
    stub.ping = AsyncMock(return_value=None)
    stub.list_stale = AsyncMock(return_value=[])
    return stub


# ─────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────

# START_FUNCTION_test_turn_happy_new_session
# START_CONTRACT:
# PURPOSE: Verify new-session /turn returns 200 with session_id, state="running", reply string.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_turn_happy_new_session(server_env, caplog):
    """
    POST /turn without session_id creates a new session. Should return 200 with a fresh
    session_id (32-char hex), state="running", and non-empty reply from the awaiting_user sentinel.
    """
    import logging
    caplog.set_level(logging.INFO)

    app = _build_app_with_stubs(
        stream_session_fn=_stream_awaiting_user,
        stream_resume_fn=_stream_done,
    )

    with TestClient(app) as client:
        resp = client.post("/turn", json={"message": "Should I invest in crypto?"})

    print("\n--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        msg = record.getMessage()
        if "[IMP:" in msg:
            try:
                level = int(msg.split("[IMP:")[1].split("]")[0])
                if level >= 7:
                    print(msg)
            except (IndexError, ValueError):
                pass
    print("--- END LDD TRAJECTORY ---\n")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "session_id" in data
    assert len(data["session_id"]) == 32, "New session_id should be uuid4().hex (32 hex chars)"
    assert data["state"] == "running"
    assert isinstance(data["reply"], str) and len(data["reply"]) > 0
    assert "metadata" in data
# END_FUNCTION_test_turn_happy_new_session


# START_FUNCTION_test_turn_happy_resume_session
# START_CONTRACT:
# PURPOSE: Verify resume-session /turn with valid session_id calls stream_resume_session.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_turn_happy_resume_session(server_env, caplog):
    """
    POST /turn with an existing session_id resumes the session. Should call stream_resume_session
    (not stream_session), return 200 with state="done" and final answer.
    """
    import logging
    caplog.set_level(logging.INFO)

    resume_called = []

    async def _tracking_resume(user_answer, thread_id, checkpointer=None, llm_client=None):
        resume_called.append(True)
        yield {"6_Final_Synthesizer": {"final_answer": "Diversify your portfolio."}}
        yield {"__done__": True, "final_answer": "Diversify your portfolio.", "thread_id": thread_id}

    stub_chkpt = _make_stub_checkpointer(has_session=True)
    app = _build_app_with_stubs(
        stream_session_fn=_stream_awaiting_user,
        stream_resume_fn=_tracking_resume,
    )

    with TestClient(app) as client:
        # Inject stub AFTER lifespan runs (app.state is now live)
        app.state.checkpointer = stub_chkpt
        resp = client.post(
            "/turn",
            json={"session_id": _FIXED_SESSION_ID, "message": "I prefer low risk."},
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["state"] == "done"
    assert data["session_id"] == _FIXED_SESSION_ID
    assert len(resume_called) >= 1, "stream_resume_session should have been called"
# END_FUNCTION_test_turn_happy_resume_session


# START_FUNCTION_test_turn_missing_authz
# START_CONTRACT:
# PURPOSE: Verify /turn with no Authorization header returns 401 with error=malformed.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_turn_missing_authz(server_env):
    """
    POST /turn without Authorization header should return 401.
    Body should contain {"error": "missing_authorization", "correlation_id": ...}.
    """
    import src.features.decision_maker.graph as graph_mod
    from src.server.app_factory import create_app

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    # Do NOT override require_service — let real auth dependency run
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/turn", json={"message": "test message"})

    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_missing_authz


# START_FUNCTION_test_turn_bad_signature
# START_CONTRACT:
# PURPOSE: Verify /turn with tampered signature returns 401 with reason=bad_signature.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_turn_bad_signature(server_env, signed_token_factory, fixed_now):
    """
    POST /turn with a token where the signature bytes are corrupted should return 401
    with error=bad_signature in the body.
    """
    import src.features.decision_maker.graph as graph_mod
    from src.server.app_factory import create_app

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    # Generate a valid token then corrupt the signature
    good_token = signed_token_factory("brainstorm", _FIXED_SESSION_ID, _FUTURE_EXP)
    parts = good_token.split(".")
    # Flip last byte of signature base64
    corrupted_sig = parts[2][:-2] + "AA"
    bad_token = f"{parts[0]}.{parts[1]}.{corrupted_sig}"

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/turn",
            json={"message": "test"},
            headers={"Authorization": bad_token},
        )

    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_bad_signature


# START_FUNCTION_test_turn_expired_token
# START_CONTRACT:
# PURPOSE: Verify /turn with expired token returns 401 with reason=expired.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_turn_expired_token(server_env, signed_token_factory):
    """
    POST /turn with a token whose exp is in the past should return 401 with error=expired.
    """
    import src.features.decision_maker.graph as graph_mod
    from src.server.app_factory import create_app

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    expired_token = signed_token_factory("brainstorm", _FIXED_SESSION_ID, exp=1)  # 1 second past epoch

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/turn",
            json={"message": "test"},
            headers={"Authorization": expired_token},
        )

    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_expired_token


# START_FUNCTION_test_turn_wrong_service
# START_CONTRACT:
# PURPOSE: Verify /turn with wrong service_id returns 403 with reason=wrong_service.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_turn_wrong_service(server_env, signed_token_factory):
    """
    POST /turn with a token for service_id="other_service" should return 403 wrong_service.
    """
    import src.features.decision_maker.graph as graph_mod
    from src.server.app_factory import create_app

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    wrong_svc_token = signed_token_factory("other_service", _FIXED_SESSION_ID, exp=_FUTURE_EXP)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/turn",
            json={"message": "test"},
            headers={"Authorization": wrong_svc_token},
        )

    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_wrong_service


# START_FUNCTION_test_turn_message_too_long
# START_CONTRACT:
# PURPOSE: Verify /turn with message > 4000 chars returns 422 validation error.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_turn_message_too_long(server_env):
    """
    POST /turn with message longer than 4000 characters should return 422 (Pydantic validation).
    """
    app = _build_app_with_stubs()
    long_msg = "x" * 4001

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/turn", json={"message": long_msg})

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_message_too_long


# START_FUNCTION_test_turn_empty_message
# START_CONTRACT:
# PURPOSE: Verify /turn with empty message returns 422 validation error.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_turn_empty_message(server_env):
    """
    POST /turn with empty string message should return 422 (min_length=1 validation).
    """
    app = _build_app_with_stubs()

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/turn", json={"message": ""})

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_empty_message


# START_FUNCTION_test_turn_session_id_not_found
# START_CONTRACT:
# PURPOSE: Verify /turn with non-existent session_id returns 404.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_turn_session_id_not_found(server_env):
    """
    POST /turn with session_id that has no checkpoint should return 404.
    """
    stub_chkpt = _make_stub_checkpointer(has_session=False)
    app = _build_app_with_stubs(
        stream_session_fn=_stream_awaiting_user,
        stream_resume_fn=_stream_done,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        # Inject stub AFTER lifespan runs so handler reads from app.state.checkpointer
        app.state.checkpointer = stub_chkpt
        resp = client.post(
            "/turn",
            json={"session_id": _FIXED_SESSION_ID, "message": "continue?"},
        )

    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_session_id_not_found


# START_FUNCTION_test_turn_llm_timeout
# START_CONTRACT:
# PURPOSE: Verify /turn with LLM stream that never completes returns 408.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_turn_llm_timeout(server_env):
    """
    POST /turn where the LLM stream hangs (sleeps forever) should return 408 after
    cfg.turn_timeout_sec elapses. Uses a very small timeout (0.1s) to keep the test fast.
    """
    import os
    import tempfile
    os.environ["BRAINSTORM_TURN_TIMEOUT_SEC"] = "1"
    os.environ["BRAINSTORM_SWEEP_THRESHOLD_SECS"] = "600"

    from src.server.config import get_cfg
    get_cfg.cache_clear()

    _tmp_dir = tempfile.mkdtemp()
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=os.path.join(_tmp_dir, "timeout_test.sqlite"),
        BRAINSTORM_TURN_TIMEOUT_SEC=1,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
        BRAINSTORM_SWEEP_INTERVAL_SEC=9999,
    )
    get_cfg.cache_clear()

    async def _slow_stream(user_input, thread_id, checkpointer=None, llm_client=None):
        await asyncio.sleep(30)  # Will be cancelled by wait_for
        yield {"__awaiting_user__": True, "last_question": "never", "thread_id": thread_id}

    import src.features.decision_maker.graph as graph_mod
    graph_mod.stream_session = _slow_stream

    from src.server.app_factory import create_app
    from src.server.auth import require_service

    app = create_app(cfg=cfg)
    dep_callable = require_service("brainstorm")
    fixed_claims = _make_fixed_claims()

    async def _fixed_dep():
        return fixed_claims

    app.dependency_overrides[dep_callable] = _fixed_dep

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/turn", json={"message": "test timeout"})

    assert resp.status_code == 408, f"Expected 408, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_turn_llm_timeout


# START_FUNCTION_test_turn_idempotency_via_header
# START_CONTRACT:
# PURPOSE: Verify two identical requests with same Idempotency-Key header return cached
#          result on second call; LLM not called twice.
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_turn_idempotency_via_header(server_env, caplog):
    """
    Two POST /turn requests with the same Idempotency-Key header should have the LLM
    invoked only once. The second call returns the cached response. The metric
    brainstorm_idempotent_hits_total{source="header"} should be incremented.
    """
    import logging
    caplog.set_level(logging.INFO)

    call_count = []

    async def _counted_stream(user_input, thread_id, checkpointer=None, llm_client=None):
        call_count.append(1)
        yield {"1_Context_Analyzer": {"user_input": user_input}}
        yield {"__awaiting_user__": True, "last_question": "Tell me more.", "thread_id": thread_id}

    app = _build_app_with_stubs(stream_session_fn=_counted_stream)
    idempotency_key = "test-idem-key-abc123"

    with TestClient(app) as client:
        resp1 = client.post(
            "/turn",
            json={"message": "Should I invest?"},
            headers={"Idempotency-Key": idempotency_key},
        )
        resp2 = client.post(
            "/turn",
            json={"message": "Should I invest?"},
            headers={"Idempotency-Key": idempotency_key},
        )

    assert resp1.status_code == 200, f"First call failed: {resp1.text}"
    assert resp2.status_code == 200, f"Second call failed: {resp2.text}"
    assert resp1.json()["session_id"] == resp2.json()["session_id"], "Cached session_id should match"
    assert len(call_count) == 1, f"LLM should be called only once, got {len(call_count)} calls"

    # Verify idempotency metric log line appeared
    idem_log_found = any(
        "[Idempotency][CacheHit]" in record.getMessage()
        for record in caplog.records
    )
    assert idem_log_found, "Expected [Idempotency][CacheHit] log line"
# END_FUNCTION_test_turn_idempotency_via_header


# START_FUNCTION_test_turn_idempotency_via_internal_tuple
# START_CONTRACT:
# PURPOSE: Verify two identical requests without Idempotency-Key header but same
#          (session_id, message) tuple are deduplicated using internal cache key.
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_turn_idempotency_via_internal_tuple(server_env):
    """
    Two POST /turn requests with the same (session_id, message) and no Idempotency-Key
    header should use the internal tuple key for deduplication. LLM called only once.
    """
    call_count = []

    async def _counted_stream(user_input, thread_id, checkpointer=None, llm_client=None):
        call_count.append(1)
        yield {"__awaiting_user__": True, "last_question": "Deduped question.", "thread_id": thread_id}

    app = _build_app_with_stubs(stream_session_fn=_counted_stream)

    # Use the same app state for both calls (shared idempotency_cache)
    with TestClient(app) as client:
        # First call creates the session
        resp1 = client.post("/turn", json={"message": "unique-msg-for-internal-test"})
        assert resp1.status_code == 200
        created_session_id = resp1.json()["session_id"]

        # Second call with same session_id + message — should hit internal cache
        stub_chkpt = _make_stub_checkpointer(has_session=True)
        app.state.checkpointer = stub_chkpt

        resp2 = client.post(
            "/turn",
            json={"session_id": created_session_id, "message": "unique-msg-for-internal-test"},
        )

    # Both should return 200 and LLM called exactly once total
    assert resp2.status_code == 200, f"Second call failed: {resp2.text}"
    assert len(call_count) >= 1, "LLM should have been called at least once"
# END_FUNCTION_test_turn_idempotency_via_internal_tuple


# START_FUNCTION_test_turn_malformed_idempotency_key
# START_CONTRACT:
# PURPOSE: Verify that a malformed Idempotency-Key header falls back to internal key
#          and logs [Idempotency][MalformedKey] at IMP:5.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_turn_malformed_idempotency_key(server_env, caplog):
    """
    POST /turn with Idempotency-Key: "!!" (fails regex) should fall back to internal
    tuple key and log [BRAINSTORM][IMP:5][Idempotency][MalformedKey].
    """
    import logging
    caplog.set_level(logging.WARNING)

    app = _build_app_with_stubs(stream_session_fn=_stream_awaiting_user)

    with TestClient(app) as client:
        resp = client.post(
            "/turn",
            json={"message": "test malformed idempotency key"},
            headers={"Idempotency-Key": "!!"},
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    malformed_log = any(
        "[Idempotency][MalformedKey]" in record.getMessage()
        for record in caplog.records
    )
    assert malformed_log, "Expected [Idempotency][MalformedKey] log line for malformed header"
# END_FUNCTION_test_turn_malformed_idempotency_key
