# FILE: src/core/llm_client.py
# VERSION: 1.3.0
# START_MODULE_CONTRACT:
# PURPOSE: Factory for building ChatOpenAI instances. Two factories:
#          build_llm() — env-driven (OpenRouter direct, legacy); and
#          build_llm_client(cfg) — Config-driven (gateway proxy, zero os.environ reads,
#          used by the MCP server layer for env-isolated dependency injection).
# SCOPE: LLM client instantiation with env-driven or Config-driven model selection.
# INPUT: OPENROUTER_API_KEY and OPENROUTER_MODEL environment variables for build_llm().
#        Config instance for build_llm_client() — NO os.environ reads inside.
# OUTPUT: Configured ChatOpenAI instances ready for node function invocation.
# KEYWORDS: [DOMAIN(9): LLM; CONCEPT(8): OpenRouter; TECH(9): LangChainOpenAI;
#            PATTERN(5): Factory; PATTERN(9): ConfigDrivenDI; CONCEPT(9): EnvIsolation]
# LINKS: [USES_API(9): langchain_openai.ChatOpenAI; READS_DATA_FROM(8): os.environ;
#         READS_DATA_FROM(9): src.server.config.Config]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §3.1 (llm_client_py), §5.9;
#                         DevelopmentPlan_MCP.md §9.3 (build_llm_client env-isolation)
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why fail loudly (KeyError) if OPENROUTER_MODEL is unset instead of providing a default?
# A: Hardcoding a default model name in code would violate the env-only policy (Negative
#    Constraints §6). Failing loudly surfaces misconfiguration at import time rather than
#    silently using a wrong/expensive model in production.
# Q: Why HTTP-Referer and X-Title headers?
# A: OpenRouter attribution best-practice: identifies the calling project in the OpenRouter
#    dashboard, enabling per-project rate limit tracking and request analytics.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.3.0 - Added build_llm_client(cfg: Config) -> ChatOpenAI. Reads ALL inputs
#              from cfg (gateway_llm_proxy_url, gateway_llm_api_key, llm_model). Zero os.environ
#              reads inside — cfg is the sole data source (§9.3 env-isolation enforced).
#              Existing build_llm() is UNTOUCHED. Bumped VERSION to 1.3.0.
# PREV_CHANGE_SUMMARY: v1.2.0 - Added explicit reasoning_enabled: Optional[bool] parameter to build_llm.
#              When None (default), falls back to OPENROUTER_REASONING_ENABLED env toggle for
#              backward compat. When bool, overrides env — enabling per-node Hybrid Reasoning
#              Routing (T3): mechanical nodes (weight_parser, context_analyzer, weight_questioner)
#              disable native reasoning for speed/cost; analytical nodes (draft_generator,
#              cove_critique, final_synthesizer) keep reasoning for quality. Per-node policy
#              lives in the caller (src/features/decision_maker/nodes.py) — llm_client stays
#              domain-agnostic.
# PREV_PREV_CHANGE_SUMMARY: v1.1.0 - Added OPENROUTER_REASONING_ENABLED env toggle. When set to a falsy
#              value ("0", "false", "no", "off", case-insensitive), passes OpenRouter body
#              parameter {"reasoning": {"enabled": false}} via ChatOpenAI.extra_body to
#              disable native chain-of-thought reasoning tokens for supporting models
#              (grok-4.x, DeepSeek-R1, o1-family). Default behavior unchanged (reasoning on).
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Factory function that builds ChatOpenAI client for OpenRouter (env-driven)] => build_llm
# FUNC 8 [Factory function that builds ChatOpenAI from Config (Config-driven, zero env reads)] => build_llm_client
# END_MODULE_MAP
#
# START_USE_CASES:
# - [build_llm]: NodeFunction -> BuildLLMClient -> LLMReadyForInvocation
# - [build_llm_client]: ServerLifespan -> build_llm_client(cfg) -> GatewayProxyLLMClient
# END_USE_CASES

import os
from typing import TYPE_CHECKING, Optional

from langchain_openai import ChatOpenAI

if TYPE_CHECKING:
    from src.server.config import Config

# START_FUNCTION_build_llm
# START_CONTRACT:
# PURPOSE: Construct and return a ChatOpenAI instance configured for the OpenRouter API.
# INPUTS:
# - Temperature for generation sampling => temperature: float (default 0.2)
# - Explicit per-call reasoning override; None = env default => reasoning_enabled: Optional[bool]
# OUTPUTS:
# - ChatOpenAI — ready-to-invoke LangChain LLM client
# SIDE_EFFECTS: Reads OPENROUTER_MODEL and OPENROUTER_API_KEY from environment.
#               Reads OPENROUTER_REASONING_ENABLED from environment ONLY when
#               reasoning_enabled argument is None (backward compat).
#               Raises KeyError if OPENROUTER_MODEL or OPENROUTER_API_KEY unset.
# KEYWORDS: [PATTERN(5): Factory; CONCEPT(8): OpenRouter; TECH(9): ChatOpenAI;
#            PATTERN(7): HybridReasoningRouting]
# LINKS: [USES_API(9): ChatOpenAI; READS_DATA_FROM(8): os.environ]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def build_llm(
    temperature: float = 0.2,
    reasoning_enabled: Optional[bool] = None,
) -> ChatOpenAI:
    """
    Build a ChatOpenAI instance backed by the OpenRouter API endpoint.

    Reads OPENROUTER_MODEL and OPENROUTER_API_KEY from the process environment.
    If either variable is absent, a KeyError is raised immediately — this is
    intentional loud-fail behavior (no default model baked into code).

    OpenRouter is addressed via its OpenAI-compatible endpoint at
    https://openrouter.ai/api/v1. Attribution headers HTTP-Referer and X-Title
    are attached per OpenRouter best-practices for dashboard tracking.

    Temperature defaults to 0.2 (deterministic enough for structured JSON output
    while retaining slight creativity for analysis drafts).

    The reasoning_enabled argument enables Hybrid Reasoning Routing (T3):
    - None (default): resolve from OPENROUTER_REASONING_ENABLED env var (falsy values
      "0"/"false"/"no"/"off" disable reasoning; anything else enables it).
    - True: force-enable native reasoning tokens (do not emit reasoning-off body flag).
    - False: force-disable native reasoning via OpenRouter body parameter
      {"reasoning": {"enabled": false}} regardless of env.

    Per-node policy MUST live in the caller (feature module) — this factory stays
    domain-agnostic so tests and non-decision_maker features can reuse it cleanly.
    """

    # START_BLOCK_READ_ENV: [Resolve required env vars; fail loudly if absent]
    model = os.environ["OPENROUTER_MODEL"]
    api_key = os.environ["OPENROUTER_API_KEY"]
    # END_BLOCK_READ_ENV

    # START_BLOCK_RESOLVE_REASONING_FLAG: [Resolve reasoning flag: arg override > env > default=True]
    # BUG_FIX_CONTEXT: Reasoning-heavy models (grok-4.1-fast) burn 60-80% of raw output
    # tokens on <thinking> blocks. For tasks where native CoT doesn't measurably improve
    # quality, disabling it via OpenRouter's {"reasoning":{"enabled":false}} body param
    # cuts latency and cost. Per-call override via reasoning_enabled= parameter enables
    # Hybrid Reasoning Routing (T3): each node decides its own policy based on whether it
    # is "mechanical" (routing/parsing/short NL) or "analytical" (multi-step math/audit).
    # When reasoning_enabled is None (the default), fall back to the env var for backward
    # compat and A/B-test ergonomics.
    if reasoning_enabled is None:
        _reasoning_raw = os.environ.get("OPENROUTER_REASONING_ENABLED", "true").strip().lower()
        _reasoning_enabled = _reasoning_raw not in ("0", "false", "no", "off")
    else:
        _reasoning_enabled = reasoning_enabled
    # END_BLOCK_RESOLVE_REASONING_FLAG

    # START_BLOCK_BUILD_CLIENT: [Instantiate ChatOpenAI with OpenRouter config]
    _client_kwargs = {
        "model": model,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": api_key,
        "temperature": temperature,
        "default_headers": {
            "HTTP-Referer": "https://github.com/crablink",
            "X-Title": "decision_maker_scenario_1",
        },
    }
    if not _reasoning_enabled:
        # OpenRouter body parameter — forwarded to provider (xAI / DeepSeek / OpenAI)
        # and silently ignored by models that don't support native reasoning toggles.
        _client_kwargs["extra_body"] = {"reasoning": {"enabled": False}}

    llm = ChatOpenAI(**_client_kwargs)
    # END_BLOCK_BUILD_CLIENT

    return llm
# END_FUNCTION_build_llm


# START_FUNCTION_build_llm_client
# START_CONTRACT:
# PURPOSE: Config-driven factory for the MCP server layer. Reads ALL inputs exclusively
#          from the cfg: Config instance — NO os.environ reads inside this function.
#          This is the §9.3 env-isolation contract: even if OPENROUTER_API_KEY is set
#          in the environment, this function MUST NOT read it. The Config object is the
#          only data source.
# INPUTS:
#   - Validated Config instance with gateway_llm_proxy_url, gateway_llm_api_key, llm_model
#     => cfg: Config
# OUTPUTS:
#   - ChatOpenAI — configured for cfg.gateway_llm_proxy_url with cfg.gateway_llm_api_key
#     and cfg.llm_model.
# SIDE_EFFECTS: None (pure factory; no I/O, no env reads).
# KEYWORDS: [PATTERN(9): ConfigDrivenFactory; CONCEPT(9): EnvIsolation; TECH(9): ChatOpenAI;
#            CONCEPT(8): GatewayProxy]
# LINKS: [READS_DATA_FROM(9): src.server.config.Config; USES_API(9): ChatOpenAI]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def build_llm_client(cfg: "Config") -> ChatOpenAI:
    """
    Config-driven LLM client factory for the MCP server layer.

    Unlike build_llm() which reads OPENROUTER_API_KEY and OPENROUTER_MODEL from
    os.environ, this function reads ALL inputs exclusively from the cfg argument.
    This provides full env-isolation: the server layer never accidentally picks up
    env vars that belong to other processes or test environments.

    The client is configured to point at cfg.gateway_llm_proxy_url (the brainstorm
    gateway LLM proxy endpoint) rather than directly at OpenRouter. This allows the
    gateway to intercept, rate-limit, and route LLM calls transparently.

    Per §9.3 test contract: even if OPENROUTER_API_KEY is set in the process
    environment, this function MUST NOT expose it. Only cfg.gateway_llm_api_key
    reaches the ChatOpenAI constructor.
    """

    # START_BLOCK_EXTRACT_FROM_CONFIG: [Read all parameters from cfg — no os.environ]
    base_url: str = cfg.gateway_llm_proxy_url
    api_key: str = cfg.gateway_llm_api_key.get_secret_value()
    model: str = cfg.llm_model
    # END_BLOCK_EXTRACT_FROM_CONFIG

    # START_BLOCK_BUILD_GATEWAY_CLIENT: [Instantiate ChatOpenAI pointing at gateway proxy]
    llm = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.2,
        default_headers={
            "HTTP-Referer": "https://github.com/crablink",
            "X-Title": "brainstorm_mcp_server",
        },
    )
    # END_BLOCK_BUILD_GATEWAY_CLIENT

    return llm
# END_FUNCTION_build_llm_client
