# FILE: src/server/app_factory.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: FastAPI application factory for the brainstorm MCP server. Provides
#          create_app(cfg) -> FastAPI and the lifespan async context manager implementing
#          startup (checkpointer + sweeper + LLM client) and shutdown (cancel sweeper,
#          exit checkpointer CM). This file is the --factory entry point for uvicorn:
#          `uvicorn src.server.app_factory:create_app --factory`.
# SCOPE: create_app() factory; lifespan() async context manager; global exception
#        handler translating domain exceptions to HTTP; middleware installation;
#        router inclusion.
# INPUT: Optional Config instance (if None, calls get_cfg()).
# OUTPUT: Configured FastAPI application instance with lifespan, middleware, routes.
# KEYWORDS: [DOMAIN(10): AppFactory; TECH(10): FastAPI_Lifespan; CONCEPT(9): DependencyInjection;
#            PATTERN(9): FactoryPattern; CONCEPT(9): AsyncContextManager; TECH(8): uvicorn_factory]
# LINKS: [USES_API(10): fastapi.FastAPI;
#         USES_API(9): src.server.turn_api.router;
#         USES_API(9): src.server.middleware.CorrelationIdMiddleware;
#         USES_API(9): src.server.checkpoint_factory.build_checkpointer;
#         USES_API(9): src.server.sweeper.Sweeper;
#         USES_API(9): src.server.metrics.build_registry;
#         USES_API(8): src.core.llm_client.build_llm_client;
#         USES_API(8): src.server.idempotency.IdempotencyCache]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §4.6 (Lifespan), §2.3 (Slice C scope), §9.5
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - lifespan is the ONLY place that calls build_checkpointer and constructs Sweeper.
# - app.state.checkpointer is set exactly once per lifespan entry.
# - Sweeper task is cancelled and awaited (with exception-swallow) on shutdown.
# - Checkpointer CM is exited after sweeper cancellation (reverse order of init).
# - [IMP:7][Lifespan][Startup][OK] log emitted after all startup is complete.
# - [IMP:7][Lifespan][Shutdown][OK] log emitted after all shutdown is complete.
# - AuthError, LLMTimeoutError, ConfigError all trigger the global exception handler
#   which calls to_http_exception() and injects correlation_id from request.state.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why does create_app accept an optional cfg parameter?
# A: Tests can pass a pre-built Config stub via create_app(cfg=stub_cfg) without env vars.
#    The factory pattern (uvicorn --factory) requires create_app() to be callable with no
#    args, so cfg defaults to None and falls back to get_cfg() for production.
# Q: Why does the lifespan use a nested async with for the checkpointer CM?
# A: build_checkpointer returns an asynccontextmanager — async with is required to open
#    the DB connection and yield the saver. Using try/finally directly would bypass the
#    CM's cleanup logic. The nested async with is the correct pattern for asynccontextmanager.
# Q: Why store registry on app.state rather than a module global?
# A: Tests need isolated registries per create_app() call. Module globals persist across
#    tests and cause "Duplicated timeseries" errors. app.state.registry is scoped to the
#    app instance lifetime.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: create_app factory, lifespan,
#               global exception handler, middleware, router inclusion per §4.6.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC  10 [AsyncCM: startup (checkpointer + sweeper + LLM client) + shutdown] => lifespan
# FUNC   9 [Factory: builds FastAPI app with lifespan, middleware, routes, exception handler] => create_app
# END_MODULE_MAP
#
# START_USE_CASES:
# - [create_app]: uvicorn --factory -> create_app() -> FastAPI app -> HTTP server
# - [create_app(cfg)]: test -> create_app(cfg=stub) -> TestClient -> isolated test
# - [lifespan]: app startup -> setup checkpointer + sweeper -> yield -> shutdown cleanup
# END_USE_CASES

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.server.auth import AuthError
from src.server.checkpoint_factory import build_checkpointer
from src.server.config import Config, get_cfg
from src.server.errors import ConfigError, LLMTimeoutError, to_http_exception
from src.server.idempotency import IdempotencyCache
from src.server.metrics import build_registry, make_metrics
from src.server.middleware import CorrelationIdMiddleware
from src.server.sweeper import Sweeper
from src.server.turn_api import router

logger = logging.getLogger(__name__)


# START_FUNCTION_lifespan
# START_CONTRACT:
# PURPOSE: FastAPI lifespan async context manager implementing §4.6 startup + shutdown.
#          Startup: build checkpointer CM, enter it, call setup(), build LLM client,
#          build Prometheus registry + metrics, build IdempotencyCache, create Sweeper task.
#          Store all on app.state. Yield to serve requests.
#          Shutdown: cancel sweeper task, await with exception-swallow, exit checkpointer CM.
# INPUTS:
#   - FastAPI application instance (injected by FastAPI lifespan protocol) => app: FastAPI
# OUTPUTS: None (yields to serve requests between startup and shutdown).
# SIDE_EFFECTS:
#   - Creates SQLite file at cfg.sqlite_path on startup.
#   - Starts asyncio.Task(Sweeper.run) on startup; cancels on shutdown.
#   - Emits [IMP:7][Lifespan][Startup][OK] and [Lifespan][Shutdown][OK] LDD logs.
# KEYWORDS: [PATTERN(9): AsyncContextManager; CONCEPT(9): DependencyLifecycle;
#            TECH(8): asyncio_create_task; CONCEPT(8): GracefulShutdown]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager implementing the §4.6 startup/shutdown sequence.

    Startup sequence:
    1. cfg = get or use pre-injected Config.
    2. Build checkpointer CM via build_checkpointer(cfg) and async-enter it.
    3. await checkpointer.setup() — creates LangGraph schema + _brainstorm_meta table.
    4. Store checkpointer on app.state.checkpointer.
    5. Build LLM client via build_llm_client(cfg), store on app.state.llm_client.
    6. Build fresh Prometheus registry + Metrics dataclass, store on app.state.
    7. Build IdempotencyCache, store on app.state.idempotency_cache.
    8. Create asyncio.Task(Sweeper(...).run()), store on app.state.sweeper_task.
    9. Log [IMP:7][Lifespan][Startup][OK].

    Shutdown sequence (in finally block):
    1. Cancel sweeper_task; await with exception-swallow (CancelledError OK).
    2. Exit checkpointer CM via __aexit__.
    3. Log [IMP:7][Lifespan][Shutdown][OK].
    """
    cfg: Config = getattr(app.state, "_injected_cfg", None) or get_cfg()
    checkpointer_cm = None
    checkpointer = None
    sweeper_task = None

    # START_BLOCK_STARTUP: [Build all dependencies and start background tasks]
    try:
        # Build checkpointer CM and enter it
        checkpointer_cm = build_checkpointer(cfg)
        checkpointer = await checkpointer_cm.__aenter__()
        await checkpointer.setup()
        app.state.checkpointer = checkpointer

        # Build LLM client
        from src.core.llm_client import build_llm_client  # noqa: PLC0415
        llm_client = build_llm_client(cfg)
        app.state.llm_client = llm_client

        # Build fresh Prometheus registry + Metrics (isolated per app instance)
        registry = build_registry()
        metrics = make_metrics(registry)
        app.state.registry = registry
        app.state.metrics = metrics

        # Build IdempotencyCache
        idempotency_cache = IdempotencyCache()
        app.state.idempotency_cache = idempotency_cache

        # Store cfg on state for handler access
        app.state.cfg = cfg

        # Create sweeper task
        sweeper = Sweeper(
            checkpointer=checkpointer,
            threshold_sec=cfg.sweep_threshold_secs,
            interval_sec=cfg.sweep_interval_sec,
            metrics=metrics,
        )
        sweeper_task = asyncio.create_task(sweeper.run())
        app.state.sweeper_task = sweeper_task

        logger.info(
            f"[BRAINSTORM][IMP:7][Lifespan][Startup][OK] "
            f"checkpointer={cfg.checkpointer_kind} "
            f"llm_model={cfg.llm_model} "
            f"sweep_interval_sec={cfg.sweep_interval_sec} [OK]"
        )
        # END_BLOCK_STARTUP

        yield  # Serve requests

    finally:
        # START_BLOCK_SHUTDOWN: [Cancel sweeper, exit checkpointer CM]
        if sweeper_task is not None:
            sweeper_task.cancel()
            try:
                await sweeper_task
            except (asyncio.CancelledError, Exception):
                pass  # CancelledError is expected on cancel; other exceptions logged below

        if checkpointer_cm is not None and checkpointer is not None:
            try:
                await checkpointer_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning(
                    f"[BRAINSTORM][IMP:6][Lifespan][Shutdown][CheckpointerCloseError] "
                    f"err={exc!r} [WARN]"
                )

        logger.info(
            "[BRAINSTORM][IMP:7][Lifespan][Shutdown][OK] "
            "All resources released [OK]"
        )
        # END_BLOCK_SHUTDOWN

# END_FUNCTION_lifespan


# START_FUNCTION_create_app
# START_CONTRACT:
# PURPOSE: Factory function building and returning a configured FastAPI application.
#          Installs CorrelationIdMiddleware, global exception handler for domain errors,
#          and includes the turn_api router. Uses the lifespan async CM for startup/shutdown.
#          Supports both production (cfg=None -> get_cfg()) and test (cfg=stub) modes.
# INPUTS:
#   - Optional pre-built Config for test injection => cfg: Optional[Config]
# OUTPUTS:
#   - FastAPI: fully configured application instance ready for uvicorn or TestClient.
# SIDE_EFFECTS: None at construction time (lifespan handles I/O at startup).
# KEYWORDS: [PATTERN(9): FactoryPattern; CONCEPT(8): TestableApp; TECH(8): uvicorn_factory]
# LINKS: [CALLS_FUNCTION(9): lifespan; USES_API(8): CorrelationIdMiddleware;
#         USES_API(8): turn_api.router]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def create_app(cfg: Optional[Config] = None) -> FastAPI:
    """
    FastAPI application factory. Builds and returns a fully configured FastAPI instance.

    Production usage via uvicorn --factory:
        uvicorn src.server.app_factory:create_app --factory --workers 1

    Test usage:
        app = create_app(cfg=stub_cfg)
        client = TestClient(app)

    When cfg is not None, it is injected into the lifespan via app.state._injected_cfg
    so that get_cfg() is not called (avoids env var requirements in tests).

    Components installed:
    - lifespan: startup + shutdown sequence (checkpointer, sweeper, LLM client, metrics)
    - CorrelationIdMiddleware: X-Correlation-ID validation + propagation
    - Global exception handler: translates AuthError, LLMTimeoutError, ConfigError to HTTP
    - turn_api.router: all routes (/turn, /done, /healthz, /readyz, /metrics)
    """
    # START_BLOCK_BUILD_APP: [Construct FastAPI with lifespan and metadata]
    app = FastAPI(
        title="brainstorm-mcp",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    # END_BLOCK_BUILD_APP

    # START_BLOCK_INJECT_CFG: [Store pre-built cfg on state for lifespan to pick up]
    if cfg is not None:
        app.state._injected_cfg = cfg
    # END_BLOCK_INJECT_CFG

    # START_BLOCK_MIDDLEWARE: [Install CorrelationIdMiddleware]
    app.add_middleware(CorrelationIdMiddleware)
    # END_BLOCK_MIDDLEWARE

    # START_BLOCK_EXCEPTION_HANDLER: [Global handler for domain exceptions -> HTTP]
    @app.exception_handler(AuthError)
    async def _auth_error_handler(request: Request, exc: AuthError):
        """Translate AuthError to 401/403 with correlation_id in body."""
        cid = getattr(request.state, "correlation_id", "unknown")
        http_exc = to_http_exception(exc, cid)
        return JSONResponse(
            status_code=http_exc.status_code,
            content=http_exc.detail,
            headers={"X-Correlation-ID": cid},
        )

    @app.exception_handler(LLMTimeoutError)
    async def _llm_timeout_handler(request: Request, exc: LLMTimeoutError):
        """Translate LLMTimeoutError to 408 with correlation_id in body."""
        cid = getattr(request.state, "correlation_id", "unknown")
        http_exc = to_http_exception(exc, cid)
        return JSONResponse(
            status_code=http_exc.status_code,
            content=http_exc.detail,
            headers={"X-Correlation-ID": cid},
        )

    @app.exception_handler(ConfigError)
    async def _config_error_handler(request: Request, exc: ConfigError):
        """Translate ConfigError to 500 with correlation_id in body."""
        cid = getattr(request.state, "correlation_id", "unknown")
        http_exc = to_http_exception(exc, cid)
        return JSONResponse(
            status_code=http_exc.status_code,
            content=http_exc.detail,
            headers={"X-Correlation-ID": cid},
        )
    # END_BLOCK_EXCEPTION_HANDLER

    # START_BLOCK_ROUTER: [Include turn_api router with all routes]
    app.include_router(router)
    # END_BLOCK_ROUTER

    return app

# END_FUNCTION_create_app
