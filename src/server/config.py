# FILE: src/server/config.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Pydantic-settings driven configuration for the brainstorm HTTP server layer.
#          Single source of truth for all runtime parameters. Validates required fields
#          at startup to fail-fast on misconfiguration. Never reads os.environ directly
#          in downstream modules — Config instance is the sole env-access surface.
# SCOPE: Environment variable loading, type coercion, secret redaction, model validation.
# INPUT: Environment variables (injected via OS env or k8s Secret/ConfigMap).
# OUTPUT: Immutable Config instance consumed by auth, checkpointer factory, sweeper,
#         and FastAPI application factory via get_cfg() Depends provider.
# KEYWORDS: [DOMAIN(9): Config; TECH(10): pydantic_settings; CONCEPT(9): FailFast;
#            PATTERN(8): SecretStr_Redaction; CONCEPT(8): EnvDrivenDeployment]
# LINKS: [USES_API(9): pydantic_settings.BaseSettings; USES_API(8): pydantic.SecretStr]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.1, TASK_brainstorm_mcp_integration.md §4
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - BRAINSTORM_HMAC_SECRET is required; missing -> ValidationError at startup.
# - GATEWAY_LLM_PROXY_URL is required; missing or empty -> ValidationError at startup.
# - GATEWAY_LLM_API_KEY is required; missing -> ValidationError at startup.
# - checkpointer_kind must be "sqlite" or "postgres"; other values -> ValidationError.
# - hmac_secret repr and str() NEVER reveal the raw secret value (SecretStr guarantee).
# - sweep_threshold_secs >= 5 * turn_timeout_sec (model_validator enforces this per §9.4).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why pydantic-settings instead of plain os.getenv() calls scattered across modules?
# A: Centralised fail-fast validation prevents silent misconfiguration in prod. SecretStr
#    provides automatic redaction in logs and repr. Downstream modules receive a typed
#    dataclass-like object, not raw strings — enabling mockability in tests via
#    dependency_overrides without touching os.environ.
# Q: Why is hmac_secret a SecretStr and not bytes?
# A: pydantic-settings natively parses env strings. .get_secret_value() converts to str;
#    auth.py calls .encode() to bytes only at the verify boundary. This keeps Config
#    serialisable (JSON-safe) for diagnostics while secrets remain redacted.
# Q: Why is sweep_threshold_secs validated against turn_timeout_sec?
# A: Per §9.4: sweep must not delete sessions that are mid-turn. Invariant:
#    SWEEP_THRESHOLD >= 5 * TURN_TIMEOUT ensures a turn running at its full timeout limit
#    still finishes before the sweeper can delete its session.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice A: Config module with all env vars
#               from TASK §4 and plan §9.4 sweep threshold validator.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 10 [Pydantic-settings Config: all runtime parameters, validation, secret redaction] => Config
# FUNC  8  [FastAPI Depends provider: returns cached Config singleton] => get_cfg
# FUNC  4  [Log-safe SHA-256 fingerprint of a secret string] => _secret_fp
# END_MODULE_MAP
#
# START_USE_CASES:
# - [Config]: Application startup -> load env -> validate required fields ->
#   fail-fast on missing -> provide typed config to all server submodules
# - [get_cfg]: FastAPI Depends -> cached Config singleton injected into routes/handlers
# END_USE_CASES

import hashlib
import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# START_FUNCTION_Config
# START_CONTRACT:
# PURPOSE: Immutable configuration snapshot built from environment variables at startup.
#          Required fields (hmac_secret, gateway_llm_proxy_url, gateway_llm_api_key)
#          raise ValidationError if absent. All secrets use SecretStr to prevent
#          accidental logging. The model_validator enforces the sweep/turn ratio invariant.
# INPUTS:
#   - Environment variables matching field aliases (e.g. BRAINSTORM_HMAC_SECRET)
# OUTPUTS:
#   - Config instance (constructed and validated by pydantic-settings)
# SIDE_EFFECTS: Reads os.environ at construction time; logs at get_cfg() call only.
# KEYWORDS: [PATTERN(9): PydanticSettings; CONCEPT(10): SecretStr; PATTERN(8): FailFast]
# LINKS: [USES_API(9): pydantic_settings.BaseSettings]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
class Config(BaseSettings):
    """
    Centralised pydantic-settings configuration for the brainstorm server layer.

    All environment variables are read once at application startup. Required fields
    (hmac_secret, gateway_llm_proxy_url, gateway_llm_api_key) cause immediate
    ValidationError if absent, giving an unambiguous fail-fast signal before any
    server socket is opened. SecretStr fields are never exposed in repr(), logs,
    or serialisation — only .get_secret_value() at the exact consumption point
    (auth.py HMAC verify, LLM client constructor) returns the raw bytes/string.

    Field naming convention: Python snake_case field names map to SCREAMING_SNAKE_CASE
    env vars via the aliases declared below (e.g. hmac_secret <- BRAINSTORM_HMAC_SECRET).
    """

    model_config = SettingsConfigDict(
        case_sensitive=True,
        populate_by_name=True,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore unknown env vars from .env and OS env (e.g. OPENROUTER_API_KEY)
    )

    # START_BLOCK_REQUIRED_SECRETS: [Required secret fields — ValidationError if absent]
    hmac_secret: SecretStr = Field(
        alias="BRAINSTORM_HMAC_SECRET",
        description=(
            "Per-service HMAC secret for verifying incoming session tokens. "
            "MUST differ from GATEWAY_HMAC_SECRET (uber-key). Required."
        ),
    )
    gateway_llm_api_key: SecretStr = Field(
        alias="GATEWAY_LLM_API_KEY",
        description="API key for the gateway LLM proxy endpoint. Required.",
    )
    # END_BLOCK_REQUIRED_SECRETS

    # START_BLOCK_REQUIRED_URLS: [Required URL — ValidationError if empty string]
    gateway_llm_proxy_url: str = Field(
        alias="GATEWAY_LLM_PROXY_URL",
        description=(
            "Base URL of the gateway LLM proxy "
            "(e.g. https://openrouter.ai/api/v1). Required."
        ),
    )
    # END_BLOCK_REQUIRED_URLS

    # START_BLOCK_OPTIONAL_FIELDS: [Optional fields with safe production defaults]
    checkpointer_kind: Literal["sqlite", "postgres"] = Field(
        default="sqlite",
        alias="BRAINSTORM_CHECKPOINTER",
        description="Checkpointer backend: 'sqlite' (default, MVP) or 'postgres' (experimental).",
    )
    sqlite_path: str = Field(
        default="/data/checkpoints.sqlite",
        alias="BRAINSTORM_SQLITE_PATH",
        description="Filesystem path for SQLite checkpoint database.",
    )
    checkpoint_dsn: str = Field(
        default="",
        alias="BRAINSTORM_CHECKPOINT_DSN",
        description="Postgres DSN; only used when checkpointer_kind='postgres'.",
    )
    session_ttl_sec: int = Field(
        default=1800,
        alias="BRAINSTORM_SESSION_TTL_SEC",
        description="Idle session TTL in seconds before sweeper deletes checkpoint.",
    )
    turn_timeout_sec: int = Field(
        default=120,
        alias="BRAINSTORM_TURN_TIMEOUT_SEC",
        description="Timeout in seconds for a single /turn LLM call.",
    )
    sweep_interval_sec: int = Field(
        default=60,
        alias="BRAINSTORM_SWEEP_INTERVAL_SEC",
        description="Interval in seconds between sweeper scan cycles.",
    )
    sweep_threshold_secs: int = Field(
        default=600,
        alias="BRAINSTORM_SWEEP_THRESHOLD_SECS",
        description=(
            "Session inactivity threshold for sweeper deletion. "
            "Must be >= 5 * turn_timeout_sec."
        ),
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        alias="BRAINSTORM_LLM_MODEL",
        description="LLM model identifier passed to the gateway proxy.",
    )
    gradio_ui: bool = Field(
        default=False,
        alias="GRADIO_UI",
        description=(
            "Set true to enable legacy Gradio UI for development. "
            "Never set in prod container."
        ),
    )
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Python logging level name (DEBUG/INFO/WARNING/ERROR).",
    )
    metrics_port: int = Field(
        default=9090,
        alias="METRICS_PORT",
        description="Port for /metrics endpoint (or 0 to serve on main port).",
    )
    # END_BLOCK_OPTIONAL_FIELDS

    @field_validator("gateway_llm_proxy_url")
    @classmethod
    def _validate_llm_proxy_url_non_empty(cls, v: str) -> str:
        """
        Ensure the LLM proxy URL is not an empty string. An empty URL would silently
        cause all LLM calls to fail at runtime rather than at startup. Fail-fast here.
        """
        if not v:
            raise ValueError(
                "GATEWAY_LLM_PROXY_URL must be a non-empty URL string. "
                "Set it to the gateway LLM proxy base URL "
                "(e.g. https://openrouter.ai/api/v1)."
            )
        return v

    @model_validator(mode="after")
    def _validate_sweep_threshold(self) -> "Config":
        """
        Enforce the sweep safety invariant from plan §9.4:
        sweep_threshold_secs must be at least 5 * turn_timeout_sec.
        This guarantees that an in-flight turn running at its maximum allowed timeout
        completes before the sweeper can evict its session checkpoint.
        """
        min_threshold = 5 * self.turn_timeout_sec
        if self.sweep_threshold_secs < min_threshold:
            raise ValueError(
                f"BRAINSTORM_SWEEP_THRESHOLD_SECS ({self.sweep_threshold_secs}) must be >= "
                f"5 * BRAINSTORM_TURN_TIMEOUT_SEC ({self.turn_timeout_sec}) = {min_threshold}. "
                f"Either decrease BRAINSTORM_TURN_TIMEOUT_SEC or increase "
                f"BRAINSTORM_SWEEP_THRESHOLD_SECS to at least {min_threshold}."
            )
        return self

# END_FUNCTION_Config


# START_FUNCTION_get_cfg
# START_CONTRACT:
# PURPOSE: FastAPI Depends-compatible provider that returns a cached Config singleton.
#          Uses functools.lru_cache so that pydantic-settings env parsing runs exactly
#          once per process lifetime. Test suites override via app.dependency_overrides
#          or call get_cfg.cache_clear() between tests.
# INPUTS: None (reads from os.environ at first call).
# OUTPUTS:
#   - Config: The validated, immutable configuration instance.
# SIDE_EFFECTS: Logs an IMP:9 belief-state line on first call to confirm config loaded.
# KEYWORDS: [PATTERN(8): Singleton; CONCEPT(7): DependencyInjection; TECH(8): lru_cache]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
@lru_cache(maxsize=1)
def get_cfg() -> Config:
    """
    Return the global Config singleton, constructing it from environment variables on
    first call. The lru_cache ensures the pydantic-settings validation runs exactly once
    per process, preventing repeated env reads and providing a stable identity for
    FastAPI's dependency_overrides mechanism (which requires the same callable object).
    """
    cfg = Config()  # type: ignore[call-arg]
    logger.info(
        f"[BRAINSTORM][IMP:9][get_cfg][Config][Init][BELIEF] "
        f"Config loaded: checkpointer={cfg.checkpointer_kind} "
        f"llm_model={cfg.llm_model} "
        f"session_ttl_sec={cfg.session_ttl_sec} "
        f"turn_timeout_sec={cfg.turn_timeout_sec} "
        f"hmac_secret_fp={_secret_fp(cfg.hmac_secret.get_secret_value())} "
        f"[OK]"
    )
    return cfg
# END_FUNCTION_get_cfg


# START_FUNCTION__secret_fp
# START_CONTRACT:
# PURPOSE: Produce a safe fingerprint of a secret string: sha256 hex digest, first 8 chars.
#          Used exclusively in log lines to provide traceability without leaking secrets.
# INPUTS:
#   - Raw secret string => value: str
# OUTPUTS:
#   - str: first 8 hex chars of SHA-256 digest (never the full hash, never the raw value)
# KEYWORDS: [CONCEPT(9): LogRedaction; PATTERN(8): TokenFingerprint]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def _secret_fp(value: str) -> str:
    """
    Compute a log-safe fingerprint of a secret: first 8 hex chars of its SHA-256 digest.
    This provides enough entropy to correlate log lines with a known secret without
    revealing the secret itself. Never use the full hash (reduces brute-force surface).
    """
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:8]
# END_FUNCTION__secret_fp
