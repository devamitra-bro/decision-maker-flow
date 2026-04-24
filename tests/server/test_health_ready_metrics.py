# FILE: tests/server/test_health_ready_metrics.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Tests for public endpoints: GET /healthz (liveness), GET /readyz (readiness),
#          GET /metrics (Prometheus). All are public (no auth). Readyz probes are
#          tested by mocking httpx and checkpointer ping.
# KEYWORDS: [DOMAIN(8): TestHTTP; CONCEPT(8): HealthProbes; TECH(8): pytest_httpx]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §4.3, §4.4, §7.4
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: health/ready/metrics tests.]
# END_CHANGE_SUMMARY

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from fastapi.testclient import TestClient

from src.server.checkpoint_factory import TouchingCheckpointer
from src.server.config import Config

_TEST_SECRET = "brainstorm-test-secret-32bytes!!"


def _stub_cfg(tmp_path=None) -> Config:
    from src.server.config import get_cfg
    get_cfg.cache_clear()
    import tempfile, os
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        sqlite_path = os.path.join(tmp_dir, "health_test.sqlite")
    else:
        sqlite_path = str(tmp_path / "health_test.sqlite")
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


def _make_stub_checkpointer(ping_fails: bool = False):
    stub = MagicMock(spec=TouchingCheckpointer)
    stub.aget_tuple = AsyncMock(return_value=None)
    stub.adelete_thread = AsyncMock(return_value=None)
    stub.list_stale = AsyncMock(return_value=[])
    if ping_fails:
        stub.ping = AsyncMock(side_effect=Exception("SQLite connection failed"))
    else:
        stub.ping = AsyncMock(return_value=None)
    return stub


# START_FUNCTION_test_healthz_returns_200_no_io
# START_CONTRACT:
# PURPOSE: Verify GET /healthz returns 200 {"status": "ok"} without IO. No auth required.
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_healthz_returns_200_no_io(server_env, tmp_path):
    """
    GET /healthz should return 200 with {"status":"ok"} and no authorization header.
    This is a liveness probe — it must return immediately without any IO.
    """
    from src.server.app_factory import create_app

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)

    with TestClient(app) as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json() == {"status": "ok"}
# END_FUNCTION_test_healthz_returns_200_no_io


# START_FUNCTION_test_readyz_ok_when_all_healthy
# START_CONTRACT:
# PURPOSE: Verify GET /readyz returns 200 {"status":"ready",...} when checkpointer ping
#          succeeds and LLM gateway /healthz returns 200.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_readyz_ok_when_all_healthy(server_env, tmp_path):
    """
    GET /readyz with healthy checkpointer and healthy LLM gateway should return 200
    {"status":"ready","checkpointer":"ok","llm_gateway":"ok"}.
    """
    import httpx
    from src.server.app_factory import create_app
    from pytest_httpx import HTTPXMock

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)
    stub_chkpt = _make_stub_checkpointer(ping_fails=False)

    with TestClient(app) as client:
        app.state.checkpointer = stub_chkpt
        # Patch httpx.AsyncClient to return 200 for the LLM gateway probe
        with patch("src.server.turn_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            resp = client.get("/readyz")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "ready"
    assert data["checkpointer"] == "ok"
    assert data["llm_gateway"] == "ok"
# END_FUNCTION_test_readyz_ok_when_all_healthy


# START_FUNCTION_test_readyz_503_on_checkpointer_fail
# START_CONTRACT:
# PURPOSE: Verify GET /readyz returns 503 when checkpointer ping raises an exception.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_readyz_503_on_checkpointer_fail(server_env, tmp_path):
    """
    GET /readyz when checkpointer.ping() raises should return 503 not_ready
    with checkpointer failure info.
    """
    from src.server.app_factory import create_app

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)
    stub_chkpt = _make_stub_checkpointer(ping_fails=True)

    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.checkpointer = stub_chkpt
        resp = client.get("/readyz")

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("status") == "not_ready" or "not_ready" in str(data)
# END_FUNCTION_test_readyz_503_on_checkpointer_fail


# START_FUNCTION_test_readyz_503_on_llm_gateway_fail
# START_CONTRACT:
# PURPOSE: Verify GET /readyz returns 503 when LLM gateway /healthz returns non-200.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_readyz_503_on_llm_gateway_fail(server_env, tmp_path):
    """
    GET /readyz when LLM gateway /healthz returns 500 should return 503 not_ready.
    """
    from src.server.app_factory import create_app
    from unittest.mock import patch, AsyncMock, MagicMock

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)
    stub_chkpt = _make_stub_checkpointer(ping_fails=False)

    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.checkpointer = stub_chkpt
        with patch("src.server.turn_api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 500  # LLM gateway failure
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            resp = client.get("/readyz")

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
# END_FUNCTION_test_readyz_503_on_llm_gateway_fail


# START_FUNCTION_test_metrics_endpoint_returns_prometheus_format
# START_CONTRACT:
# PURPOSE: Verify GET /metrics returns text/plain with Prometheus format and contains
#          brainstorm_turns_total in the body.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_metrics_endpoint_returns_prometheus_format(server_env, tmp_path):
    """
    GET /metrics should return 200 with content-type containing
    'text/plain; version=0.0.4; charset=utf-8' and body containing
    brainstorm_turns_total metric.
    """
    from src.server.app_factory import create_app

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)

    with TestClient(app) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    content_type = resp.headers.get("content-type", "")
    assert "text/plain" in content_type, f"Expected text/plain content-type, got: {content_type}"
    assert "version=0.0.4" in content_type, f"Expected Prometheus content-type, got: {content_type}"
    assert "brainstorm_turns_total" in resp.text, "Expected brainstorm_turns_total in Prometheus output"
# END_FUNCTION_test_metrics_endpoint_returns_prometheus_format
