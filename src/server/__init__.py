# FILE: src/server/__init__.py
# VERSION: 1.1.0
# START_MODULE_CONTRACT:
# PURPOSE: Package marker for the brainstorm HTTP server layer. Exposes the public
#          surface of src/server/ for import by the FastAPI application factory and
#          external test suites. This layer is the ONLY entry point for all HTTP
#          concerns (auth, config, routing, metrics, sweeper) — decision_maker domain
#          logic is accessed only via injected dependencies.
# SCOPE: Package initialisation; no business logic. Provides __all__ for linting.
#        Re-exports ConfigError from checkpoint_factory per plan §1.3 (Slice B addition).
# INPUT: None (imported at module load time by FastAPI app factory).
# OUTPUT: Namespace src.server.* available for consumer imports, including ConfigError.
# KEYWORDS: [DOMAIN(8): MCP_Integration; TECH(7): PackageMarker; CONCEPT(7): LayeredArch]
# LINKS: [READS_DATA_FROM(8): src/server/config.py; READS_DATA_FROM(8): src/server/auth.py;
#         READS_DATA_FROM(8): src/server/checkpoint_factory.py]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.1 (Slice A scope), §1.3 (Slice B)
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why is this file non-empty (just __all__) instead of a truly empty marker?
# A: Semantic Exoskeleton protocol requires MODULE_CONTRACT on every new .py file.
#    __all__ enables static analysis and prevents wildcard-import noise from creeping
#    into test discovery. The module itself carries zero runtime cost.
# Q: Why is ConfigError re-exported here?
# A: Plan §1.3 explicitly states: define ConfigError locally in checkpoint_factory.py
#    and re-export from src/server/__init__.py. Slice C errors.py will expose it via
#    a broader exceptions hierarchy. For now, src.server.ConfigError is the canonical path.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.1.0 - Slice B: re-export ConfigError from checkpoint_factory.]
# PREV_CHANGE_SUMMARY: [v1.0.0 - Initial creation as part of Slice A: Config + Auth foundation.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# (no functions; package marker only)
# END_MODULE_MAP
#
# START_USE_CASES:
# - [src.server package]: FastAPI factory -> import Config, verify_session_token ->
#   wire HTTP server with HMAC auth
# - [ConfigError]: Lifespan -> build_checkpointer -> ConfigError on bad kind
# END_USE_CASES

from src.server.checkpoint_factory import ConfigError  # noqa: F401

__all__ = [
    "config",
    "auth",
    "checkpoint_factory",
    "ConfigError",
]
