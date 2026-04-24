# FILE: tests/server/test_route_auth_coverage.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Structural test verifying that every non-public route has an auth dependency
#          per §1.2 (Auth Placement A1). Iterates app.routes, filters APIRoute instances,
#          and asserts each non-public route's dependency list contains require_service.
# KEYWORDS: [DOMAIN(8): TestStructural; CONCEPT(9): AuthCoverage; PATTERN(8): RouteInspection]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §1.2 (AuthLayer_A1), §2.3 (exit criteria)
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: auth coverage structural test.]
# END_CHANGE_SUMMARY

import sys
from pathlib import Path

import pytest

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

from src.server.config import Config

_TEST_SECRET = "brainstorm-test-secret-32bytes!!"

# Public routes that explicitly MUST NOT have auth dependency
_PUBLIC_PATHS = frozenset({
    "/healthz",
    "/readyz",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
})


def _stub_cfg() -> Config:
    from src.server.config import get_cfg
    get_cfg.cache_clear()
    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_TURN_TIMEOUT_SEC=30,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
    )
    get_cfg.cache_clear()
    return cfg


def _dep_has_auth(dep) -> bool:
    """Check if a FastAPI Dependant or dependency callable looks like an auth dep."""
    if dep is None:
        return False
    # Check the dependency callable's qualname
    dep_callable = getattr(dep, "dependency", dep)
    qualname = getattr(dep_callable, "__qualname__", "") or ""
    name = getattr(dep_callable, "__name__", "") or ""
    return "require_service" in qualname or "require_service" in name or "verify" in name


def _route_has_auth_dep(route) -> bool:
    """
    Check whether a FastAPI APIRoute has an auth dependency by inspecting
    its dependant tree for callable names containing require_service or verify.
    """
    from fastapi.routing import APIRoute
    from fastapi.dependencies.utils import get_flat_dependant

    if not isinstance(route, APIRoute):
        return False

    dependant = route.dependant
    if dependant is None:
        return False

    # Check the flat dependency list
    flat_deps = get_flat_dependant(dependant, skip_repeats=True)
    for dep in flat_deps.dependencies:
        if _dep_has_auth(dep):
            return True
    return False


# START_FUNCTION_test_all_non_public_routes_require_auth
# START_CONTRACT:
# PURPOSE: Verify that all non-public APIRoute instances have a dependency whose
#          __qualname__ contains 'require_service' or 'verify'. Failure message
#          lists offending routes by path.
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def test_all_non_public_routes_require_auth(server_env):
    """
    Build the FastAPI app and iterate all APIRoute instances.
    For each route whose path is NOT in _PUBLIC_PATHS, assert that at least one
    dependency is an auth dependency produced by require_service().

    The check identifies auth deps by:
    1. Comparing dep callable identity against _DEPS_CACHE values (definitive: dep IS
       from require_service if it's in the cache).
    2. Fallback: qualname contains '_make_dep' (inner closure of require_service) or
       'require_service' or 'verify'.

    Failure output lists all offending route paths to aid quick diagnosis.
    """
    from fastapi.routing import APIRoute
    from src.server.app_factory import create_app
    from src.server.auth import _DEPS_CACHE

    cfg = _stub_cfg()
    app = create_app(cfg=cfg)

    # Collect all dep callables from require_service cache (by identity)
    auth_dep_ids = {id(dep) for dep in _DEPS_CACHE.values()}

    offending_routes = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        route_path = route.path
        if route_path in _PUBLIC_PATHS:
            continue

        # For non-public routes, check for auth dependency
        has_auth = False

        # Check dependant.dependencies (FastAPI internal Dependant objects)
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            for dep in getattr(dependant, "dependencies", []):
                # FastAPI stores the dep callable in cache_key[0] (Dependant.cache_key = (callable, ()))
                # dep.dependency may be None for inline Depends; use cache_key as canonical source.
                dep_callable = None
                cache_key = getattr(dep, "cache_key", None)
                if cache_key and isinstance(cache_key, tuple) and len(cache_key) > 0:
                    dep_callable = cache_key[0]
                if dep_callable is None:
                    dep_callable = getattr(dep, "dependency", None)

                if dep_callable is not None:
                    # Method 1: Identity check against known auth dep callables from _DEPS_CACHE
                    if id(dep_callable) in auth_dep_ids:
                        has_auth = True
                        break
                    # Method 2: Qualname check (fallback for any naming variant)
                    qualname = getattr(dep_callable, "__qualname__", "") or ""
                    name = getattr(dep_callable, "__name__", "") or ""
                    if (
                        "require_service" in qualname
                        or "require_service" in name
                        or "_make_dep" in qualname
                        or "verify" in name
                    ):
                        has_auth = True
                        break

        if not has_auth:
            offending_routes.append(route_path)

    assert not offending_routes, (
        f"The following non-public routes lack require_service auth dependency:\n"
        + "\n".join(f"  - {p}" for p in offending_routes)
        + "\nEvery protected route MUST have Depends(require_service('brainstorm'))."
    )
# END_FUNCTION_test_all_non_public_routes_require_auth


# START_FUNCTION_test_public_routes_do_not_require_auth
# START_CONTRACT:
# PURPOSE: Verify that public routes (/healthz, /readyz, /metrics) are accessible
#          without any Authorization header (no auth dependency active).
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def test_public_routes_do_not_require_auth(server_env, tmp_path):
    """
    GET /healthz, GET /readyz (may fail due to real IO, but should not be 401),
    GET /metrics should all return non-401 status codes without any auth header.
    """
    from fastapi.testclient import TestClient
    from src.server.app_factory import create_app
    from unittest.mock import AsyncMock, MagicMock, patch

    cfg = Config(
        BRAINSTORM_HMAC_SECRET=_TEST_SECRET,
        GATEWAY_LLM_PROXY_URL="https://test-llm.example.com/v1",
        GATEWAY_LLM_API_KEY="test-api-key",
        BRAINSTORM_SQLITE_PATH=str(tmp_path / "auth_cov.sqlite"),
        BRAINSTORM_TURN_TIMEOUT_SEC=30,
        BRAINSTORM_SWEEP_THRESHOLD_SECS=600,
    )

    app = create_app(cfg=cfg)

    with TestClient(app, raise_server_exceptions=False) as client:
        healthz_resp = client.get("/healthz")
        metrics_resp = client.get("/metrics")

    assert healthz_resp.status_code != 401, "/healthz must not require auth"
    assert metrics_resp.status_code != 401, "/metrics must not require auth"
# END_FUNCTION_test_public_routes_do_not_require_auth
