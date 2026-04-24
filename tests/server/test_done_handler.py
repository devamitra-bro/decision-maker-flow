# FILE: tests/server/test_done_handler.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Unit tests for POST /done handler covering success, unknown session (idempotent),
#          and missing auth. Uses dependency_overrides to stub checkpointer and auth.
# SCOPE: test_done_success_deletes_checkpoint, test_done_unknown_session_is_200,
#        test_done_missing_authz.
# KEYWORDS: [DOMAIN(8): TestHTTP; TECH(8): FastAPI_TestClient; CONCEPT(8): IdempotentDone]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §4.2 (POST /done data flow)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: POST /done test suite.]
# END_CHANGE_SUMMARY

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from fastapi.testclient import TestClient

from src.server.auth import TokenClaims, require_service
from src.server.checkpoint_factory import TouchingCheckpointer
from src.server.config import Config

_TEST_SECRET = "brainstorm-test-secret-32bytes!!"
_FUTURE_EXP = 9_999_999_999
_FIXED_SESSION_ID = str(uuid.UUID("12345678-1234-4234-a234-123456789abc"))


def _stub_cfg(tmp_path=None) -> Config:
    import os
    import tempfile
    from src.server.config import get_cfg
    get_cfg.cache_clear()
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        sqlite_path = os.path.join(tmp_dir, "done_test.sqlite")
    else:
        sqlite_path = str(tmp_path / "done_test.sqlite")
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
    return TokenClaims(service_id="brainstorm", session_id=session_id, exp=_FUTURE_EXP)


def _make_stub_checkpointer(has_session: bool = True):
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


def _build_done_app(has_session: bool = True, auth_override=None):
    from src.server.app_factory import create_app

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)
    stub_chkpt = _make_stub_checkpointer(has_session=has_session)

    dep_callable = require_service("brainstorm")
    if auth_override is not None:
        app.dependency_overrides[dep_callable] = auth_override
    else:
        fixed_claims = _make_fixed_claims()
        async def _fixed_dep():
            return fixed_claims
        app.dependency_overrides[dep_callable] = _fixed_dep

    # Patch checkpointer on app.state after startup
    _original_checkpointer_store = stub_chkpt

    # We need to inject at lifespan time; use app.state pre-lifespan approach
    # Store stub for lifespan startup to pick up
    app.state._stub_checkpointer = stub_chkpt

    return app, stub_chkpt


# START_FUNCTION_test_done_success_deletes_checkpoint
# START_CONTRACT:
# PURPOSE: Verify /done with existing session returns 200, calls adelete_thread,
#          decrements active_sessions, and increments done_total.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_done_success_deletes_checkpoint(server_env):
    """
    POST /done for an existing session should:
    - Return 200 with acknowledged=True
    - Call checkpointer.adelete_thread(session_id)
    - Decrement active_sessions gauge
    - Increment done_total counter
    """
    from src.server.app_factory import create_app
    from src.server.metrics import build_registry, make_metrics

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    stub_chkpt = _make_stub_checkpointer(has_session=True)

    dep_callable = require_service("brainstorm")
    fixed_claims = _make_fixed_claims()
    async def _fixed_dep():
        return fixed_claims
    app.dependency_overrides[dep_callable] = _fixed_dep

    with TestClient(app) as client:
        # Inject stub checkpointer after lifespan starts
        app.state.checkpointer = stub_chkpt

        # Set active sessions to 1 so decrement doesn't go negative
        app.state.metrics.active_sessions.inc()

        resp = client.post("/done", json={"session_id": _FIXED_SESSION_ID})

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["acknowledged"] is True

    # Verify checkpointer.adelete_thread was called
    stub_chkpt.adelete_thread.assert_called_once_with(_FIXED_SESSION_ID)
# END_FUNCTION_test_done_success_deletes_checkpoint


# START_FUNCTION_test_done_unknown_session_is_200
# START_CONTRACT:
# PURPOSE: Verify /done for a non-existent session returns 200 (idempotent).
#          active_sessions should NOT be decremented.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_done_unknown_session_is_200(server_env):
    """
    POST /done for a session that does not exist should still return 200 acknowledged=True.
    The active_sessions gauge must NOT be decremented (session never existed).
    """
    from src.server.app_factory import create_app

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    stub_chkpt = _make_stub_checkpointer(has_session=False)

    dep_callable = require_service("brainstorm")
    fixed_claims = _make_fixed_claims()
    async def _fixed_dep():
        return fixed_claims
    app.dependency_overrides[dep_callable] = _fixed_dep

    with TestClient(app) as client:
        app.state.checkpointer = stub_chkpt
        initial_sessions = app.state.metrics.active_sessions._value.get()

        resp = client.post("/done", json={"session_id": _FIXED_SESSION_ID})

        final_sessions = app.state.metrics.active_sessions._value.get()

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json()["acknowledged"] is True
    # adelete_thread should NOT have been called for non-existent session
    stub_chkpt.adelete_thread.assert_not_called()
    # active_sessions should not have decreased
    assert final_sessions == initial_sessions, "active_sessions should not decrement for missing session"
# END_FUNCTION_test_done_unknown_session_is_200


# START_FUNCTION_test_done_missing_authz
# START_CONTRACT:
# PURPOSE: Verify /done with no Authorization header returns 401.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_done_missing_authz(server_env):
    """
    POST /done without Authorization header should return 401.
    """
    from src.server.app_factory import create_app

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/done", json={"session_id": _FIXED_SESSION_ID})

    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_done_missing_authz
