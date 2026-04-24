# FILE: tests/server/test_config.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Unit tests for src/server/config.py. Validates required field enforcement,
#          default values, SecretStr redaction, sweep-threshold model validator,
#          and checkpointer_kind literal constraint.
# SCOPE: Config class validation, get_cfg() caching behaviour, _secret_fp redaction.
# INPUT: Monkeypatched environment variables.
# OUTPUT: pytest PASS/FAIL with LDD telemetry output for IMP:7-10 log lines.
# KEYWORDS: [DOMAIN(8): TestConfig; CONCEPT(9): FailFast; PATTERN(8): PydanticSettings;
#            CONCEPT(8): SecretStr_Redaction; PATTERN(7): ModelValidator]
# LINKS: [READS_DATA_FROM(10): src/server/config.py]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §2.1, §9.4, §9.6
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice A: full Config test coverage.]
# END_CHANGE_SUMMARY

import logging

import pytest
from pydantic import ValidationError

# All imports use absolute paths from brainstorm root (conftest adds to sys.path)
from src.server.config import Config, _secret_fp, get_cfg


# START_FUNCTION_test_config_loads_with_all_required_fields
# START_CONTRACT:
# PURPOSE: Verify Config constructs successfully when all required fields are set.
# INPUTS: server_env fixture (sets BRAINSTORM_HMAC_SECRET, GATEWAY_LLM_PROXY_URL, GATEWAY_LLM_API_KEY)
# OUTPUTS: None — assertion on cfg fields
# KEYWORDS: [CONCEPT(8): HappyPath; PATTERN(7): FieldValidation]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_loads_with_all_required_fields(server_env, ldd_capture, caplog):
    """
    Config must construct without error when all three required fields are present.
    Verify that field values match the injected env vars and defaults are applied.
    """
    caplog.set_level(logging.DEBUG)

    cfg = Config()  # type: ignore[call-arg]

    # START_BLOCK_LDD_TELEMETRY: [Print IMP:7-10 trajectory before assertions]
    ldd_capture()
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_VERIFICATION: [Assert required fields and defaults]
    assert cfg.hmac_secret.get_secret_value() == server_env["BRAINSTORM_HMAC_SECRET"]
    assert cfg.gateway_llm_proxy_url == server_env["GATEWAY_LLM_PROXY_URL"]
    assert cfg.gateway_llm_api_key.get_secret_value() == server_env["GATEWAY_LLM_API_KEY"]

    # Verify defaults
    assert cfg.checkpointer_kind == "sqlite"
    assert cfg.sqlite_path == "/data/checkpoints.sqlite"
    assert cfg.session_ttl_sec == 1800
    assert cfg.turn_timeout_sec == 120
    assert cfg.sweep_interval_sec == 60
    assert cfg.sweep_threshold_secs == 600
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.gradio_ui is False
    assert cfg.log_level == "INFO"
    assert cfg.metrics_port == 9090
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_config_loads_with_all_required_fields


# START_FUNCTION_test_config_missing_hmac_secret_raises
# START_CONTRACT:
# PURPOSE: Verify that Config raises ValidationError when BRAINSTORM_HMAC_SECRET is absent.
# KEYWORDS: [CONCEPT(9): FailFast; PATTERN(8): RequiredField]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_missing_hmac_secret_raises(monkeypatch):
    """
    Config must raise ValidationError (pydantic) when BRAINSTORM_HMAC_SECRET is not set.
    This enforces the fail-fast contract for required secrets.
    """
    get_cfg.cache_clear()
    monkeypatch.delenv("BRAINSTORM_HMAC_SECRET", raising=False)
    monkeypatch.setenv("GATEWAY_LLM_PROXY_URL", "https://proxy.example.com/v1")
    monkeypatch.setenv("GATEWAY_LLM_API_KEY", "test-key")

    with pytest.raises((ValidationError, Exception)):
        Config()  # type: ignore[call-arg]

    get_cfg.cache_clear()
# END_FUNCTION_test_config_missing_hmac_secret_raises


# START_FUNCTION_test_config_missing_gateway_url_raises
# START_CONTRACT:
# PURPOSE: Verify that Config raises when GATEWAY_LLM_PROXY_URL is absent or empty.
# KEYWORDS: [CONCEPT(9): FailFast; PATTERN(8): RequiredField]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_missing_gateway_url_raises(monkeypatch):
    """
    Config must raise when GATEWAY_LLM_PROXY_URL is not set.
    Empty URL would silently break all LLM calls; fail-fast at startup is required.
    """
    get_cfg.cache_clear()
    monkeypatch.setenv("BRAINSTORM_HMAC_SECRET", "any-secret")
    monkeypatch.delenv("GATEWAY_LLM_PROXY_URL", raising=False)
    monkeypatch.setenv("GATEWAY_LLM_API_KEY", "test-key")

    with pytest.raises((ValidationError, Exception)):
        Config()  # type: ignore[call-arg]

    get_cfg.cache_clear()
# END_FUNCTION_test_config_missing_gateway_url_raises


# START_FUNCTION_test_config_missing_api_key_raises
# START_CONTRACT:
# PURPOSE: Verify that Config raises when GATEWAY_LLM_API_KEY is absent.
# KEYWORDS: [CONCEPT(9): FailFast; PATTERN(8): RequiredField]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_missing_api_key_raises(monkeypatch):
    """
    Config must raise when GATEWAY_LLM_API_KEY is not set.
    """
    get_cfg.cache_clear()
    monkeypatch.setenv("BRAINSTORM_HMAC_SECRET", "any-secret")
    monkeypatch.setenv("GATEWAY_LLM_PROXY_URL", "https://proxy.example.com/v1")
    monkeypatch.delenv("GATEWAY_LLM_API_KEY", raising=False)

    with pytest.raises((ValidationError, Exception)):
        Config()  # type: ignore[call-arg]

    get_cfg.cache_clear()
# END_FUNCTION_test_config_missing_api_key_raises


# START_FUNCTION_test_config_secret_str_repr_hides_value
# START_CONTRACT:
# PURPOSE: Verify that hmac_secret and gateway_llm_api_key repr() and str() never
#          expose the raw secret value — SecretStr redaction invariant.
# KEYWORDS: [CONCEPT(10): SecretStr_Redaction; CONCEPT(9): LogSafety]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_secret_str_repr_hides_value(server_env):
    """
    SecretStr fields must never expose raw secret values in repr() or str().
    This is the primary defence against secrets leaking into logs.
    """
    cfg = Config()  # type: ignore[call-arg]

    hmac_repr = repr(cfg.hmac_secret)
    api_key_repr = repr(cfg.gateway_llm_api_key)
    hmac_str = str(cfg.hmac_secret)
    api_key_str = str(cfg.gateway_llm_api_key)

    raw_hmac = server_env["BRAINSTORM_HMAC_SECRET"]
    raw_api_key = server_env["GATEWAY_LLM_API_KEY"]

    # START_BLOCK_VERIFICATION: [Assert secrets are redacted]
    assert raw_hmac not in hmac_repr, f"CRITICAL: raw HMAC secret exposed in repr: {hmac_repr}"
    assert raw_hmac not in hmac_str, f"CRITICAL: raw HMAC secret exposed in str: {hmac_str}"
    assert raw_api_key not in api_key_repr, f"CRITICAL: raw API key exposed in repr: {api_key_repr}"
    assert raw_api_key not in api_key_str, f"CRITICAL: raw API key exposed in str: {api_key_str}"
    # get_secret_value() MUST reveal the raw value (that's the intentional access path)
    assert cfg.hmac_secret.get_secret_value() == raw_hmac
    assert cfg.gateway_llm_api_key.get_secret_value() == raw_api_key
    # END_BLOCK_VERIFICATION
# END_FUNCTION_test_config_secret_str_repr_hides_value


# START_FUNCTION_test_config_sweep_threshold_validator_pass
# START_CONTRACT:
# PURPOSE: Verify model_validator allows sweep_threshold_secs >= 5 * turn_timeout_sec.
# KEYWORDS: [PATTERN(8): ModelValidator; CONCEPT(7): InvariantEnforcement]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_sweep_threshold_validator_pass(monkeypatch):
    """
    sweep_threshold_secs = 5 * turn_timeout_sec should pass validation.
    sweep_threshold_secs > 5 * turn_timeout_sec should also pass.
    """
    get_cfg.cache_clear()
    monkeypatch.setenv("BRAINSTORM_HMAC_SECRET", "any-secret-value")
    monkeypatch.setenv("GATEWAY_LLM_PROXY_URL", "https://proxy.example.com/v1")
    monkeypatch.setenv("GATEWAY_LLM_API_KEY", "test-key")
    monkeypatch.setenv("BRAINSTORM_TURN_TIMEOUT_SEC", "60")
    monkeypatch.setenv("BRAINSTORM_SWEEP_THRESHOLD_SECS", "300")  # exactly 5 * 60

    cfg = Config()  # type: ignore[call-arg]
    assert cfg.sweep_threshold_secs == 300
    assert cfg.turn_timeout_sec == 60

    get_cfg.cache_clear()
# END_FUNCTION_test_config_sweep_threshold_validator_pass


# START_FUNCTION_test_config_sweep_threshold_validator_fail
# START_CONTRACT:
# PURPOSE: Verify model_validator raises when sweep_threshold_secs < 5 * turn_timeout_sec.
#          This enforces the §9.4 race-safety invariant.
# KEYWORDS: [CONCEPT(9): FailFast; PATTERN(8): ModelValidator; CONCEPT(8): SweepSafety]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_sweep_threshold_validator_fail(monkeypatch):
    """
    sweep_threshold_secs < 5 * turn_timeout_sec must raise ValidationError.
    E.g., turn_timeout=120, sweep_threshold=400 (< 600) must be rejected.
    """
    get_cfg.cache_clear()
    monkeypatch.setenv("BRAINSTORM_HMAC_SECRET", "any-secret-value")
    monkeypatch.setenv("GATEWAY_LLM_PROXY_URL", "https://proxy.example.com/v1")
    monkeypatch.setenv("GATEWAY_LLM_API_KEY", "test-key")
    monkeypatch.setenv("BRAINSTORM_TURN_TIMEOUT_SEC", "120")
    monkeypatch.setenv("BRAINSTORM_SWEEP_THRESHOLD_SECS", "400")  # less than 5*120=600

    with pytest.raises((ValidationError, ValueError)):
        Config()  # type: ignore[call-arg]

    get_cfg.cache_clear()
# END_FUNCTION_test_config_sweep_threshold_validator_fail


# START_FUNCTION_test_config_invalid_checkpointer_kind_raises
# START_CONTRACT:
# PURPOSE: Verify that checkpointer_kind only accepts "sqlite" or "postgres";
#          any other value raises ValidationError.
# KEYWORDS: [CONCEPT(8): LiteralConstraint; PATTERN(7): FieldValidation]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_invalid_checkpointer_kind_raises(monkeypatch):
    """
    checkpointer_kind is a Literal["sqlite", "postgres"]. Setting it to any other
    value (e.g., "mysql", "redis") must raise a ValidationError.
    """
    get_cfg.cache_clear()
    monkeypatch.setenv("BRAINSTORM_HMAC_SECRET", "any-secret-value")
    monkeypatch.setenv("GATEWAY_LLM_PROXY_URL", "https://proxy.example.com/v1")
    monkeypatch.setenv("GATEWAY_LLM_API_KEY", "test-key")
    monkeypatch.setenv("BRAINSTORM_CHECKPOINTER", "redis")

    with pytest.raises((ValidationError, Exception)):
        Config()  # type: ignore[call-arg]

    get_cfg.cache_clear()
# END_FUNCTION_test_config_invalid_checkpointer_kind_raises


# START_FUNCTION_test_config_postgres_kind_accepted
# START_CONTRACT:
# PURPOSE: Verify "postgres" is accepted as checkpointer_kind.
# KEYWORDS: [CONCEPT(7): LiteralConstraint; PATTERN(6): HappyPath]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def test_config_postgres_kind_accepted(monkeypatch):
    """postgres checkpointer kind should be accepted without error."""
    get_cfg.cache_clear()
    monkeypatch.setenv("BRAINSTORM_HMAC_SECRET", "any-secret-value")
    monkeypatch.setenv("GATEWAY_LLM_PROXY_URL", "https://proxy.example.com/v1")
    monkeypatch.setenv("GATEWAY_LLM_API_KEY", "test-key")
    monkeypatch.setenv("BRAINSTORM_CHECKPOINTER", "postgres")
    monkeypatch.setenv("BRAINSTORM_CHECKPOINT_DSN", "postgresql://user:pass@localhost:5432/db")

    cfg = Config()  # type: ignore[call-arg]
    assert cfg.checkpointer_kind == "postgres"
    assert "postgresql" in cfg.checkpoint_dsn

    get_cfg.cache_clear()
# END_FUNCTION_test_config_postgres_kind_accepted


# START_FUNCTION_test_secret_fp_produces_fingerprint
# START_CONTRACT:
# PURPOSE: Verify _secret_fp produces "sha256:<8hex>" format and does not include raw value.
# KEYWORDS: [CONCEPT(9): LogRedaction; PATTERN(8): TokenFingerprint]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_secret_fp_produces_fingerprint():
    """
    _secret_fp must return exactly "sha256:" + 8 hex chars.
    The raw input value must never appear in the fingerprint output.
    """
    raw = "my-super-secret-value"
    fp = _secret_fp(raw)

    assert fp.startswith("sha256:"), f"Fingerprint must start with 'sha256:': {fp}"
    hex_part = fp[len("sha256:"):]
    assert len(hex_part) == 8, f"Fingerprint hex part must be 8 chars, got {len(hex_part)}: {fp}"
    assert all(c in "0123456789abcdef" for c in hex_part), f"Fingerprint must be hex: {fp}"
    assert raw not in fp, f"Raw value must not appear in fingerprint: {fp}"
# END_FUNCTION_test_secret_fp_produces_fingerprint


# START_FUNCTION_test_get_cfg_is_cached
# START_CONTRACT:
# PURPOSE: Verify get_cfg() returns the same object on multiple calls (lru_cache).
#          Also verify cache_clear() resets it.
# KEYWORDS: [PATTERN(8): Singleton; CONCEPT(7): lru_cache]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_get_cfg_is_cached(server_env):
    """
    get_cfg() must return the same Config instance on repeated calls (lru_cache).
    After cache_clear(), a new instance is constructed.
    """
    cfg_a = get_cfg()
    cfg_b = get_cfg()

    assert cfg_a is cfg_b, "get_cfg() must return the same cached instance"

    get_cfg.cache_clear()
    cfg_c = get_cfg()
    # After clear, a new instance is created; values must still match env
    assert cfg_c.gateway_llm_proxy_url == server_env["GATEWAY_LLM_PROXY_URL"]
# END_FUNCTION_test_get_cfg_is_cached


# START_FUNCTION_test_config_custom_values
# START_CONTRACT:
# PURPOSE: Verify that optional fields accept custom values from environment.
# KEYWORDS: [CONCEPT(7): OptionalField; PATTERN(6): EnvOverride]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def test_config_custom_values(monkeypatch):
    """
    Optional config fields must reflect custom env var values, not just defaults.
    Tests that env-var parsing works for all overridable fields.
    """
    get_cfg.cache_clear()
    monkeypatch.setenv("BRAINSTORM_HMAC_SECRET", "custom-secret")
    monkeypatch.setenv("GATEWAY_LLM_PROXY_URL", "https://custom.proxy.com/v1")
    monkeypatch.setenv("GATEWAY_LLM_API_KEY", "custom-api-key")
    monkeypatch.setenv("BRAINSTORM_SESSION_TTL_SEC", "3600")
    monkeypatch.setenv("BRAINSTORM_TURN_TIMEOUT_SEC", "60")
    monkeypatch.setenv("BRAINSTORM_SWEEP_THRESHOLD_SECS", "600")  # 10 * 60 = ok
    monkeypatch.setenv("BRAINSTORM_LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("GRADIO_UI", "true")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("METRICS_PORT", "8080")

    cfg = Config()  # type: ignore[call-arg]

    assert cfg.session_ttl_sec == 3600
    assert cfg.turn_timeout_sec == 60
    assert cfg.llm_model == "gpt-4o"
    assert cfg.gradio_ui is True
    assert cfg.log_level == "DEBUG"
    assert cfg.metrics_port == 8080

    get_cfg.cache_clear()
# END_FUNCTION_test_config_custom_values
