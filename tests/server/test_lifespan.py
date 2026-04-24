# FILE: tests/server/test_lifespan.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Tests for FastAPI lifespan: startup creates checkpointer and sweeper task,
#          shutdown cancels sweeper and closes checkpointer, LDD startup log is emitted.
# KEYWORDS: [DOMAIN(8): TestLifespan; TECH(8): FastAPI_Lifespan; CONCEPT(8): AsyncTask]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §4.6 (Lifespan)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: lifespan tests.]
# END_CHANGE_SUMMARY

import asyncio
import logging
import sys
from pathlib import Path

import pytest

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from fastapi.testclient import TestClient

from src.server.config import Config

_TEST_SECRET = "brainstorm-test-secret-32bytes!!"


def _stub_cfg(tmp_path) -> Config:
    from src.server.config import get_cfg
    get_cfg.cache_clear()
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=str(tmp_path / "test_lifespan.sqlite"),
        BRAINSTORM_TURN_TIMEOUT_SEC=30,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
        BRAINSTORM_SWEEP_INTERVAL_SEC=999,  # Very long interval so sweeper doesn't run
    )
    get_cfg.cache_clear()
    return cfg


# START_FUNCTION_test_lifespan_creates_checkpointer_and_sweeper
# START_CONTRACT:
# PURPOSE: Verify that after TestClient enters lifespan, app.state.checkpointer is set
#          and app.state.sweeper_task is a running asyncio.Task.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_lifespan_creates_checkpointer_and_sweeper(server_env, tmp_path):
    """
    After entering TestClient (which triggers lifespan startup), app.state.checkpointer
    should be set to a TouchingCheckpointer instance and app.state.sweeper_task should
    be an asyncio.Task that is not yet done.
    """
    from src.server.app_factory import create_app
    from src.server.checkpoint_factory import TouchingCheckpointer

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)

    with TestClient(app) as client:
        # Verify checkpointer is on app.state
        assert hasattr(app.state, "checkpointer"), "app.state.checkpointer should be set"
        assert isinstance(app.state.checkpointer, TouchingCheckpointer), (
            f"Expected TouchingCheckpointer, got {type(app.state.checkpointer)}"
        )

        # Verify sweeper task is running
        assert hasattr(app.state, "sweeper_task"), "app.state.sweeper_task should be set"
        sweeper_task = app.state.sweeper_task
        assert isinstance(sweeper_task, asyncio.Task), (
            f"Expected asyncio.Task, got {type(sweeper_task)}"
        )
        assert not sweeper_task.done(), "Sweeper task should still be running"
# END_FUNCTION_test_lifespan_creates_checkpointer_and_sweeper


# START_FUNCTION_test_lifespan_shutdown_cancels_sweeper_and_closes_checkpointer
# START_CONTRACT:
# PURPOSE: Verify that after TestClient exits (lifespan shutdown), the sweeper task is
#          cancelled/done and the checkpointer CM is properly exited.
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def test_lifespan_shutdown_cancels_sweeper_and_closes_checkpointer(server_env, tmp_path):
    """
    After TestClient exits the context (triggering shutdown), the sweeper task should be
    cancelled (done=True). The checkpointer should have had its async CM exited cleanly.
    """
    from src.server.app_factory import create_app

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)

    sweeper_task_ref = []

    with TestClient(app) as client:
        sweeper_task_ref.append(app.state.sweeper_task)

    # After context exit, sweeper task should be done (cancelled)
    task = sweeper_task_ref[0]
    assert task.done(), "Sweeper task should be done (cancelled) after lifespan shutdown"
# END_FUNCTION_test_lifespan_shutdown_cancels_sweeper_and_closes_checkpointer


# START_FUNCTION_test_lifespan_ldd_startup_ok_logged
# START_CONTRACT:
# PURPOSE: Verify that [IMP:7][Lifespan][Startup][OK] log line is emitted during startup.
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_lifespan_ldd_startup_ok_logged(server_env, tmp_path, caplog):
    """
    During TestClient startup, the lifespan should emit:
    [BRAINSTORM][IMP:7][Lifespan][Startup][OK] in the log.
    """
    import logging
    caplog.set_level(logging.INFO)

    from src.server.app_factory import create_app

    cfg = _stub_cfg(tmp_path)
    app = create_app(cfg=cfg)

    with TestClient(app) as client:
        pass  # Just enter and exit

    # Find the startup log
    print("\n--- LDD TRAJECTORY (IMP:7-10) ---")
    startup_found = False
    shutdown_found = False
    for record in caplog.records:
        msg = record.getMessage()
        if "[IMP:7]" in msg or "[IMP:9]" in msg:
            print(msg)
        if "[Lifespan][Startup][OK]" in msg:
            startup_found = True
        if "[Lifespan][Shutdown][OK]" in msg:
            shutdown_found = True
    print("--- END LDD TRAJECTORY ---\n")

    assert startup_found, "Expected [BRAINSTORM][IMP:7][Lifespan][Startup][OK] log line"
    assert shutdown_found, "Expected [BRAINSTORM][IMP:7][Lifespan][Shutdown][OK] log line"
# END_FUNCTION_test_lifespan_ldd_startup_ok_logged
