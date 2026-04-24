# FILE: tests/server/test_coverage_boost.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Targeted coverage tests for code paths not reached by the main test suite.
#          Covers: errors.py (AuthError wrong_service 403, ConfigError 500, re-raise),
#          middleware.py (valid X-Correlation-ID, malformed X-Correlation-ID, _header_fp),
#          sweeper.py (_tick direct call), checkpoint_factory.py (adelete_thread, ping),
#          app_factory.py (exception handlers via HTTP), turn_api.py (turn_n branch).
# KEYWORDS: [DOMAIN(8): TestCoverage; CONCEPT(8): TargetedCoverage; TECH(8): pytest]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.3 (coverage >= 95%)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation to boost coverage from 93.6% to >= 95%.]
# END_CHANGE_SUMMARY

import sys
from pathlib import Path

import pytest

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures/helpers
# ─────────────────────────────────────────────────────────────────────────────

_TEST_SECRET = "brainstorm-test-secret-32bytes!!"
_FUTURE_EXP = 9_999_999_999


def _make_app(tmp_path):
    """Build a minimal FastAPI app with test config."""
    import os
    from src.server.app_factory import create_app
    from src.server.config import get_cfg, Config
    get_cfg.cache_clear()
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=str(tmp_path / "boost.sqlite"),
        BRAINSTORM_TURN_TIMEOUT_SEC=30,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
        BRAINSTORM_SWEEP_INTERVAL_SEC=9999,
    )
    get_cfg.cache_clear()
    app = create_app(cfg=cfg)
    return app


# ─────────────────────────────────────────────────────────────────────────────
# errors.py coverage
# ─────────────────────────────────────────────────────────────────────────────

# START_FUNCTION_test_to_http_exception_auth_wrong_service
# START_CONTRACT:
# PURPOSE: Cover errors.py lines 127-133: AuthError.wrong_service -> 403 branch.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_to_http_exception_auth_wrong_service(server_env):
    """
    to_http_exception(AuthError('wrong_service'), cid) returns HTTPException with
    status_code=403. Covers the if exc.reason == 'wrong_service': branch.
    """
    from src.server.errors import to_http_exception
    from src.server.auth import AuthError

    exc = AuthError("wrong_service", "wrong service id")
    result = to_http_exception(exc, "cid-test-abc")
    assert result.status_code == 403
    assert result.detail["error"] == "wrong_service"
    assert result.detail["correlation_id"] == "cid-test-abc"
# END_FUNCTION_test_to_http_exception_auth_wrong_service


# START_FUNCTION_test_to_http_exception_auth_malformed
# START_CONTRACT:
# PURPOSE: Cover errors.py 129-136: AuthError with non-wrong_service reason -> 401.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_to_http_exception_auth_malformed(server_env):
    """
    to_http_exception(AuthError('malformed'), cid) returns HTTPException with
    status_code=401. Covers the else: status_code=401 branch.
    """
    from src.server.errors import to_http_exception
    from src.server.auth import AuthError

    exc = AuthError("malformed", "bad token")
    result = to_http_exception(exc, "cid-malformed")
    assert result.status_code == 401
    assert result.detail["error"] == "malformed"
# END_FUNCTION_test_to_http_exception_auth_malformed


# START_FUNCTION_test_to_http_exception_config_error
# START_CONTRACT:
# PURPOSE: Cover errors.py 152-161: ConfigError -> 500 branch.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_to_http_exception_config_error(server_env):
    """
    to_http_exception(ConfigError(...), cid) returns HTTPException with status_code=500.
    Covers the ConfigError isinstance branch with IMP:9 log.
    """
    from src.server.errors import to_http_exception
    from src.server.checkpoint_factory import ConfigError

    exc = ConfigError("database not found")
    result = to_http_exception(exc, "cid-config")
    assert result.status_code == 500
    assert result.detail["error"] == "config_error"
    assert result.detail["correlation_id"] == "cid-config"
# END_FUNCTION_test_to_http_exception_config_error


# START_FUNCTION_test_to_http_exception_unknown_re_raises
# START_CONTRACT:
# PURPOSE: Cover errors.py 164-165: unknown exception -> re-raise branch.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_to_http_exception_unknown_re_raises(server_env):
    """
    to_http_exception(ValueError(...), cid) re-raises the original exception unchanged.
    Covers the final 'raise exc' branch for unknown exception types.
    """
    from src.server.errors import to_http_exception

    original_exc = ValueError("unexpected error")
    with pytest.raises(ValueError, match="unexpected error"):
        to_http_exception(original_exc, "cid-unknown")
# END_FUNCTION_test_to_http_exception_unknown_re_raises


# ─────────────────────────────────────────────────────────────────────────────
# middleware.py coverage
# ─────────────────────────────────────────────────────────────────────────────

# START_FUNCTION_test_middleware_valid_correlation_id
# START_CONTRACT:
# PURPOSE: Cover middleware.py 73, 124-126: valid X-Correlation-ID passthrough.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_middleware_valid_correlation_id(server_env, tmp_path):
    """
    GET /healthz with a valid X-Correlation-ID header should echo the same ID in the
    response header. Covers lines 73 (_header_fp) and 124-126 (valid header branch).
    """
    from fastapi.testclient import TestClient

    app = _make_app(tmp_path)
    valid_cid = "valid-corr-id-abc123"

    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Correlation-ID": valid_cid})

    assert resp.status_code == 200
    assert resp.headers.get("X-Correlation-ID") == valid_cid, (
        f"Expected same correlation-id in response, got {resp.headers.get('X-Correlation-ID')!r}"
    )
# END_FUNCTION_test_middleware_valid_correlation_id


# START_FUNCTION_test_middleware_malformed_correlation_id
# START_CONTRACT:
# PURPOSE: Cover middleware.py 127-134: malformed X-Correlation-ID gets replaced with uuid4.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_middleware_malformed_correlation_id(server_env, tmp_path):
    """
    GET /healthz with a malformed X-Correlation-ID (fails regex) should replace it with
    a generated uuid4. Response header should contain a valid 32-char hex value.
    Covers lines 127-134 in middleware.py.
    """
    from fastapi.testclient import TestClient

    app = _make_app(tmp_path)
    malformed_cid = "!!"  # fails ^[a-zA-Z0-9_-]{8,64}$ regex

    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Correlation-ID": malformed_cid})

    assert resp.status_code == 200
    response_cid = resp.headers.get("X-Correlation-ID", "")
    assert response_cid != malformed_cid, "Malformed cid should have been replaced"
    assert len(response_cid) == 32, f"Expected uuid4.hex (32 chars), got {response_cid!r}"
# END_FUNCTION_test_middleware_malformed_correlation_id


# ─────────────────────────────────────────────────────────────────────────────
# sweeper.py coverage
# ─────────────────────────────────────────────────────────────────────────────

# START_FUNCTION_test_sweeper_tick_direct
# START_CONTRACT:
# PURPOSE: Cover sweeper.py line 168: _tick() called directly with stale sessions.
#          Also covers the delete loop, exception path, and metrics update.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.asyncio
async def test_sweeper_tick_direct(server_env):
    """
    Call Sweeper._tick() directly with a mocked checkpointer returning 1 stale session.
    Covers sweeper.py line 168 (await self._tick()) and the full delete loop.
    """
    from unittest.mock import AsyncMock, MagicMock
    from src.server.sweeper import Sweeper
    from src.server.metrics import build_registry, make_metrics
    from src.server.checkpoint_factory import TouchingCheckpointer

    registry = build_registry()
    metrics = make_metrics(registry)

    stub_chkpt = MagicMock(spec=TouchingCheckpointer)
    stub_chkpt.list_stale = AsyncMock(return_value=["thread-stale-001"])
    stub_chkpt.adelete_thread = AsyncMock(return_value=None)

    sweeper = Sweeper(
        checkpointer=stub_chkpt,
        threshold_sec=600,
        interval_sec=9999,
        metrics=metrics,
        clock=lambda: 1_700_000_000,
    )

    # Set active_sessions to 1 so dec() doesn't go negative
    metrics.active_sessions.inc()

    await sweeper._tick()

    stub_chkpt.adelete_thread.assert_called_once_with("thread-stale-001")
    print("[test_sweeper_tick_direct] PASS: adelete_thread called for stale session")
# END_FUNCTION_test_sweeper_tick_direct


# START_FUNCTION_test_sweeper_tick_delete_exception_continues
# START_CONTRACT:
# PURPOSE: Cover sweeper.py 202-204: exception during delete is swallowed; sweeper continues.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
@pytest.mark.asyncio
async def test_sweeper_tick_delete_exception_continues(server_env):
    """
    If adelete_thread raises, the sweeper logs and continues (doesn't crash).
    Covers the except Exception as exc: branch at lines 202-204.
    """
    from unittest.mock import AsyncMock, MagicMock
    from src.server.sweeper import Sweeper
    from src.server.metrics import build_registry, make_metrics
    from src.server.checkpoint_factory import TouchingCheckpointer

    registry = build_registry()
    metrics = make_metrics(registry)

    stub_chkpt = MagicMock(spec=TouchingCheckpointer)
    stub_chkpt.list_stale = AsyncMock(return_value=["bad-thread-001", "good-thread-002"])
    # First delete raises, second succeeds
    stub_chkpt.adelete_thread = AsyncMock(
        side_effect=[RuntimeError("delete failed"), None]
    )

    sweeper = Sweeper(
        checkpointer=stub_chkpt,
        threshold_sec=600,
        interval_sec=9999,
        metrics=metrics,
        clock=lambda: 1_700_000_000,
    )
    metrics.active_sessions.inc()
    metrics.active_sessions.inc()

    # Should NOT raise even if first delete fails
    await sweeper._tick()

    assert stub_chkpt.adelete_thread.call_count == 2, "Should have attempted both deletes"
    print("[test_sweeper_tick_delete_exception_continues] PASS: exception swallowed, sweeper continued")
# END_FUNCTION_test_sweeper_tick_delete_exception_continues


# ─────────────────────────────────────────────────────────────────────────────
# checkpoint_factory.py coverage
# ─────────────────────────────────────────────────────────────────────────────

# START_FUNCTION_test_touching_checkpointer_adelete_thread
# START_CONTRACT:
# PURPOSE: Cover checkpoint_factory.py 348-375: adelete_thread with real SQLite.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
@pytest.mark.asyncio
async def test_touching_checkpointer_adelete_thread(server_env, tmp_path):
    """
    Call TouchingCheckpointer.adelete_thread() with a real SQLite db at tmp_path.
    Verifies the method runs without error and logs success (covers lines 348-375).
    Uses build_checkpointer async context manager for proper setup.
    """
    from src.server.checkpoint_factory import build_checkpointer
    from src.server.config import get_cfg, Config
    get_cfg.cache_clear()
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=str(tmp_path / "adt_test.sqlite"),
        BRAINSTORM_TURN_TIMEOUT_SEC=30,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
    )
    get_cfg.cache_clear()

    async with build_checkpointer(cfg) as chkpt:
        await chkpt.setup()
        # Delete a thread_id that doesn't exist — should be idempotent (no rows affected)
        await chkpt.adelete_thread("nonexistent-thread-id-for-coverage")

    print("[test_touching_checkpointer_adelete_thread] PASS: adelete_thread ran without error")
# END_FUNCTION_test_touching_checkpointer_adelete_thread


# START_FUNCTION_test_touching_checkpointer_ping
# START_CONTRACT:
# PURPOSE: Cover checkpoint_factory.py line 384: ping() with real SQLite.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
@pytest.mark.asyncio
async def test_touching_checkpointer_ping(server_env, tmp_path):
    """
    Call TouchingCheckpointer.ping() with a real SQLite db at tmp_path.
    Verifies SELECT 1 completes without error (covers line 384).
    """
    from src.server.checkpoint_factory import build_checkpointer
    from src.server.config import get_cfg, Config
    get_cfg.cache_clear()
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=str(tmp_path / "ping_test.sqlite"),
        BRAINSTORM_TURN_TIMEOUT_SEC=30,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
    )
    get_cfg.cache_clear()

    async with build_checkpointer(cfg) as chkpt:
        await chkpt.setup()
        await chkpt.ping()

    print("[test_touching_checkpointer_ping] PASS: ping() returned without error")
# END_FUNCTION_test_touching_checkpointer_ping


# ─────────────────────────────────────────────────────────────────────────────
# app_factory.py coverage — exception handlers
# ─────────────────────────────────────────────────────────────────────────────

# START_FUNCTION_test_app_factory_auth_error_handler
# START_CONTRACT:
# PURPOSE: Cover app_factory.py 267-269: _auth_error_handler body via HTTP.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_app_factory_auth_error_handler(server_env, tmp_path):
    """
    POST /turn without Authorization triggers AuthError -> _auth_error_handler.
    Verifies the handler returns 401 with X-Correlation-ID header.
    Covers app_factory.py lines 267-269 (auth error handler body).
    """
    from fastapi.testclient import TestClient

    app = _make_app(tmp_path)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/turn", json={"message": "test"})

    assert resp.status_code == 401
    assert "X-Correlation-ID" in resp.headers
# END_FUNCTION_test_app_factory_auth_error_handler


# START_FUNCTION_test_app_factory_llm_timeout_handler
# START_CONTRACT:
# PURPOSE: Cover app_factory.py 277-284: _llm_timeout_handler body via HTTP.
#          Also covers errors.py 140-148 (LLMTimeoutError branch).
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_app_factory_llm_timeout_handler(server_env, tmp_path):
    """
    POST /turn with a slow stream triggers LLMTimeoutError -> _llm_timeout_handler.
    Uses a 1s timeout with a 30s sleep to ensure the timeout fires.
    Covers app_factory.py lines 277-284 and errors.py 140-148.
    """
    import asyncio
    import os
    import tempfile
    from fastapi.testclient import TestClient
    from src.server.app_factory import create_app
    from src.server.auth import TokenClaims, require_service
    from src.server.config import get_cfg, Config
    import src.features.decision_maker.graph as graph_mod

    get_cfg.cache_clear()
    tmp_dir = tempfile.mkdtemp()
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=os.path.join(tmp_dir, "timeout_test2.sqlite"),
        BRAINSTORM_TURN_TIMEOUT_SEC=1,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
        BRAINSTORM_SWEEP_INTERVAL_SEC=9999,
    )
    get_cfg.cache_clear()

    async def _slow_stream(user_input, thread_id, checkpointer=None, llm_client=None):
        await asyncio.sleep(30)
        yield {"__awaiting_user__": True, "last_question": "never", "thread_id": thread_id}

    graph_mod.stream_session = _slow_stream

    app = create_app(cfg=cfg)
    dep_callable = require_service("brainstorm")
    fixed_claims = TokenClaims(service_id="brainstorm", session_id="12345678-1234-4234-a234-123456789abc", exp=_FUTURE_EXP)

    async def _fixed_dep():
        return fixed_claims

    app.dependency_overrides[dep_callable] = _fixed_dep

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/turn", json={"message": "test timeout via handler"})

    assert resp.status_code == 408, f"Expected 408, got {resp.status_code}: {resp.text}"
    assert "X-Correlation-ID" in resp.headers
# END_FUNCTION_test_app_factory_llm_timeout_handler
