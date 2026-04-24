# FILE: src/server/metrics.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Prometheus metrics namespace for the brainstorm MCP server. Provides a factory
#          build_registry() that creates a fresh CollectorRegistry with all brainstorm_*
#          metric collectors. Exposes a Metrics dataclass bundling collectors as named
#          attributes for type-safe handler access. Tests construct a fresh registry per
#          test to prevent global state leakage.
# SCOPE: CollectorRegistry construction; Counter, Histogram, Gauge declarations;
#        Metrics dataclass as typed bundle; make_metrics() factory.
# INPUT: None (build_registry creates fresh registry; make_metrics accepts registry).
# OUTPUT: Metrics dataclass instance with all collectors as attributes.
# KEYWORDS: [DOMAIN(9): Observability; TECH(10): prometheus_client; CONCEPT(9): MetricsRegistry;
#            PATTERN(9): DataclassBundle; CONCEPT(8): NoGlobalState; TECH(8): Counter_Histogram_Gauge]
# LINKS: [USES_API(10): prometheus_client.CollectorRegistry;
#         USES_API(9): prometheus_client.Counter;
#         USES_API(9): prometheus_client.Histogram;
#         USES_API(9): prometheus_client.Gauge]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §7.5 (metrics list), §9.1 (idempotency counter),
#   §2.3 (Slice C scope), §4.1 step 13 (metrics in turn handler)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - build_registry() always returns a FRESH CollectorRegistry — never the prometheus_client
#   REGISTRY global. This prevents test pollution when multiple tests create metrics.
# - All metric names start with "brainstorm_" prefix.
# - brainstorm_token_verify_failures_total ONLY uses labels: malformed, bad_version,
#   bad_signature, expired, wrong_service, missing_session. Forbidden: insufficient_scope,
#   bad_user_id, iat_skew (per §1.1).
# - brainstorm_idempotent_hits_total uses labels: header, internal only.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why use a fresh CollectorRegistry per call rather than module-level globals?
# A: Module-level prometheus_client globals conflict across tests — registering the same
#    metric name twice raises ValueError. A per-call registry is passed to generate_latest()
#    and isolated entirely. The app stores one Metrics instance on app.state; tests create
#    their own. This matches prometheus_client best practice for testing.
# Q: Why a Metrics dataclass rather than a plain dict or tuple?
# A: Dataclass provides dot-notation access (metrics.turns_total) with no runtime overhead.
#    It enables type-checking and IDE auto-completion. Named attributes prevent off-by-one
#    errors when referencing metrics in handlers vs. tests.
# Q: Why are histogram buckets explicitly specified?
# A: Default prometheus_client buckets (0.005..10) are tuned for sub-10s latencies.
#    LLM calls can take up to cfg.turn_timeout_sec (120s default). Extended buckets
#    [0.1..120] cover the full turn timeout range for accurate SLO tracking.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: full metrics registry with all
#               brainstorm_* collectors per plan §7.5 and §9.1.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 8  [Typed dataclass bundle of all Prometheus collectors] => Metrics
# FUNC  9  [Factory: builds fresh CollectorRegistry with all brainstorm_* metrics] => build_registry
# FUNC  8  [Factory: instantiates Metrics dataclass from a given registry] => make_metrics
# END_MODULE_MAP
#
# START_USE_CASES:
# - [build_registry + make_metrics]: Lifespan -> build_registry() -> make_metrics(registry)
#   -> app.state.metrics; handlers call metrics.turns_total.labels(...).inc()
# - [make_metrics in tests]: test -> build_registry() -> make_metrics(reg) -> isolated metrics
# END_USE_CASES

import logging
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# Histogram buckets covering the full range from fast (100ms) to slow (120s) LLM turns.
# Extended beyond prometheus_client defaults to track turn_timeout_sec boundary accurately.
_TURN_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)


# START_FUNCTION_Metrics
# START_CONTRACT:
# PURPOSE: Typed dataclass bundling all Prometheus collectors as named attributes.
#          Constructed by make_metrics(registry) and stored on app.state.metrics.
#          Handlers access collectors by attribute name for type-safe, autocomplete-friendly
#          metric operations (e.g. metrics.turns_total.labels(state="done").inc()).
# INPUTS: All collector instances as constructor arguments (from make_metrics).
# OUTPUTS: Metrics instance with immutable attribute references to collectors.
# KEYWORDS: [PATTERN(9): DataclassBundle; CONCEPT(8): TypeSafeMetrics]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
@dataclass
class Metrics:
    """
    Typed bundle of all brainstorm Prometheus collectors.

    Attributes mirror the metric names in build_registry() — each attribute holds the
    collector object, enabling handler code like:
        metrics.turns_total.labels(state="done").inc()
        metrics.turn_duration_seconds.observe(elapsed)
        metrics.active_sessions.inc()

    This dataclass carries no business logic — it is a pure named container.
    Constructed by make_metrics(registry) after build_registry() creates the registry.
    """

    turns_total: Counter
    turn_duration_seconds: Histogram
    llm_roundtrip_seconds: Histogram
    active_sessions: Gauge
    done_total: Counter
    token_verify_failures_total: Counter
    idempotent_hits_total: Counter
    sweeper_runs_total: Counter
    sweeper_deleted_total: Counter
    readyz_checks_total: Counter

# END_FUNCTION_Metrics


# START_FUNCTION_build_registry
# START_CONTRACT:
# PURPOSE: Build and return a fresh, isolated CollectorRegistry with all brainstorm_*
#          metric collectors registered on it. Each call returns a NEW registry —
#          not the prometheus_client default REGISTRY global. This design enables tests
#          to create isolated metrics without cross-test pollution.
# INPUTS: None.
# OUTPUTS:
#   - CollectorRegistry: fresh registry with all brainstorm_* metrics registered.
# SIDE_EFFECTS: Logs at IMP:5 on completion.
# KEYWORDS: [PATTERN(9): FreshRegistry; TECH(10): prometheus_client; CONCEPT(8): TestIsolation]
# LINKS: [USES_API(10): prometheus_client.CollectorRegistry;
#         USES_API(9): prometheus_client.Counter;
#         USES_API(9): prometheus_client.Histogram;
#         USES_API(9): prometheus_client.Gauge]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def build_registry() -> CollectorRegistry:
    """
    Create a fresh CollectorRegistry and register all brainstorm_* metric collectors.

    Metric definitions (per plan §7.5, §4.1 step 13, §9.1):

    Counters:
    - brainstorm_turns_total{state}: incremented per /turn response (state=running|done|error)
    - brainstorm_done_total: incremented per successful /done response
    - brainstorm_token_verify_failures_total{reason}: auth failures by reason enum
      (6 valid labels only: malformed, bad_version, bad_signature, expired, wrong_service,
      missing_session — per §1.1 forbidden list enforced here structurally by omission)
    - brainstorm_idempotent_hits_total{source}: cache hits by key source (header|internal)
    - brainstorm_sweeper_runs_total: sweeper tick completions
    - brainstorm_sweeper_deleted_total: total sessions deleted by sweeper
    - brainstorm_readyz_checks_total{result}: readiness probe outcomes

    Histograms:
    - brainstorm_turn_duration_seconds: wall time for entire /turn handler
    - brainstorm_llm_roundtrip_seconds: time from stream start to stream end

    Gauges:
    - brainstorm_active_sessions: current count of known active sessions

    Returns the registry WITHOUT registering on the default prometheus_client REGISTRY,
    ensuring test isolation and preventing "Duplicated timeseries" errors.
    """
    # START_BLOCK_CREATE_REGISTRY: [Instantiate fresh isolated registry]
    registry = CollectorRegistry()
    # END_BLOCK_CREATE_REGISTRY

    # START_BLOCK_REGISTER_COUNTERS: [Register all Counter metrics]
    Counter(
        "brainstorm_turns_total",
        "Total brainstorm turn requests by terminal state",
        labelnames=["state"],  # state ∈ {running, done, error}
        registry=registry,
    )
    Counter(
        "brainstorm_done_total",
        "Total successful POST /done requests",
        registry=registry,
    )
    Counter(
        "brainstorm_token_verify_failures_total",
        "Token verification failures by reason",
        labelnames=["reason"],  # reason ∈ {malformed, bad_version, bad_signature, expired, wrong_service, missing_session}
        registry=registry,
    )
    Counter(
        "brainstorm_idempotent_hits_total",
        "Idempotency cache hits by key source",
        labelnames=["source"],  # source ∈ {header, internal}
        registry=registry,
    )
    Counter(
        "brainstorm_sweeper_runs_total",
        "Total sweeper scan cycles completed",
        registry=registry,
    )
    Counter(
        "brainstorm_sweeper_deleted_total",
        "Total sessions deleted by sweeper",
        registry=registry,
    )
    Counter(
        "brainstorm_readyz_checks_total",
        "Readiness probe check outcomes",
        labelnames=["result"],  # result ∈ {ok, checkpointer_fail, llm_gateway_fail}
        registry=registry,
    )
    # END_BLOCK_REGISTER_COUNTERS

    # START_BLOCK_REGISTER_HISTOGRAMS: [Register Histogram metrics with extended buckets]
    Histogram(
        "brainstorm_turn_duration_seconds",
        "Wall-clock duration of entire /turn handler",
        buckets=_TURN_BUCKETS,
        registry=registry,
    )
    Histogram(
        "brainstorm_llm_roundtrip_seconds",
        "Duration of LLM graph stream from start to final sentinel",
        buckets=_TURN_BUCKETS,
        registry=registry,
    )
    # END_BLOCK_REGISTER_HISTOGRAMS

    # START_BLOCK_REGISTER_GAUGES: [Register Gauge metrics]
    Gauge(
        "brainstorm_active_sessions",
        "Current number of active (non-deleted) brainstorm sessions",
        registry=registry,
    )
    # END_BLOCK_REGISTER_GAUGES

    logger.info(
        "[BRAINSTORM][IMP:5][build_registry][Metrics][Init] "
        "Fresh Prometheus registry built with all brainstorm_* collectors [OK]"
    )
    return registry

# END_FUNCTION_build_registry


# START_FUNCTION_make_metrics
# START_CONTRACT:
# PURPOSE: Construct a Metrics dataclass by looking up all named collectors from
#          the provided registry. Provides a typed, attribute-accessible bundle
#          for use in handlers and tests. Must be called AFTER build_registry() has
#          populated the registry with all brainstorm_* collectors.
# INPUTS:
#   - CollectorRegistry with all brainstorm_* metrics registered => registry: CollectorRegistry
# OUTPUTS:
#   - Metrics: dataclass with all collectors as typed attributes.
# SIDE_EFFECTS: None (pure factory).
# KEYWORDS: [PATTERN(8): TypedFactory; CONCEPT(8): CollectorLookup]
# LINKS: [READS_DATA_FROM(9): build_registry output]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def make_metrics(registry: CollectorRegistry) -> Metrics:
    """
    Instantiate a Metrics dataclass from a registry built by build_registry().

    Looks up each collector by iterating registry._names_to_collectors (prometheus_client
    internal API). This is the standard approach for typed metric access without global
    variables. All collector names are known statically from build_registry().

    The returned Metrics instance is stored on app.state.metrics in the lifespan and
    injected into handlers via request.app.state.metrics.
    """
    # START_BLOCK_LOOKUP_COLLECTORS: [Extract unique collectors by base name from registry]
    # prometheus_client stores each collector under multiple keys in _names_to_collectors:
    # - base name (e.g. "brainstorm_turns")
    # - _total suffix (e.g. "brainstorm_turns_total") for Counters
    # - _created suffix for Counters
    # We deduplicate by collector identity (id) and key by describe()[0].name (base name).
    seen_ids: set = set()
    unique_by_base: dict = {}
    for collector in registry._names_to_collectors.values():
        if id(collector) not in seen_ids:
            seen_ids.add(id(collector))
            base_name = collector.describe()[0].name
            unique_by_base[base_name] = collector

    turns_total = unique_by_base["brainstorm_turns"]
    turn_duration_seconds = unique_by_base["brainstorm_turn_duration_seconds"]
    llm_roundtrip_seconds = unique_by_base["brainstorm_llm_roundtrip_seconds"]
    active_sessions = unique_by_base["brainstorm_active_sessions"]
    done_total = unique_by_base["brainstorm_done"]
    token_verify_failures_total = unique_by_base["brainstorm_token_verify_failures"]
    idempotent_hits_total = unique_by_base["brainstorm_idempotent_hits"]
    sweeper_runs_total = unique_by_base["brainstorm_sweeper_runs"]
    sweeper_deleted_total = unique_by_base["brainstorm_sweeper_deleted"]
    readyz_checks_total = unique_by_base["brainstorm_readyz_checks"]
    # END_BLOCK_LOOKUP_COLLECTORS

    return Metrics(
        turns_total=turns_total,
        turn_duration_seconds=turn_duration_seconds,
        llm_roundtrip_seconds=llm_roundtrip_seconds,
        active_sessions=active_sessions,
        done_total=done_total,
        token_verify_failures_total=token_verify_failures_total,
        idempotent_hits_total=idempotent_hits_total,
        sweeper_runs_total=sweeper_runs_total,
        sweeper_deleted_total=sweeper_deleted_total,
        readyz_checks_total=readyz_checks_total,
    )

# END_FUNCTION_make_metrics
