# FILE: src/features/decision_maker/tools.py
# VERSION: 2.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Pluggable async search tool abstraction for the Decision Maker feature.
#          Provides a Tavily-backed async adapter when TAVILY_API_KEY is present,
#          or a deterministic stub_search_async for offline/test use.
# SCOPE: Public entry point search_async(query) auto-selects the adapter at call-time.
#        Private _build_tavily_adapter() constructs the AsyncTavilyClient factory.
#        stub_search_async() always available as fallback.
# INPUT: A search query string (str).
# OUTPUT: List[Dict[str, Any]] with keys "query", "result", "source" per item.
# KEYWORDS: [DOMAIN(6): Tools; CONCEPT(8): PluggableAdapter; PATTERN(9): AsyncIO;
#            TECH(9): TavilyAsyncClient; CONCEPT(7): Stub; PATTERN(7): DependencyInjection]
# LINKS: [USES_API(9): tavily.AsyncTavilyClient; USES_API(8): asyncio; USES_API(7): os.environ]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §3.1 (tools_py); §5 Negative Constraints (no real network in tests)
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why return a list instead of a single dict?
# A: Node 2 (tool_node) iterates state.search_queries and appends results per query.
#    The list shape matches tool_facts: List in the state schema and simplifies accumulation.
# Q: Why does search_async resolve the adapter at call-time rather than module level?
# A: TAVILY_API_KEY may not be loaded at import time (dotenv is loaded at session start).
#    Per-call resolution ensures the env var is read after dotenv setup, keeping the
#    module stateless and compatible with test isolation (tests never set TAVILY_API_KEY).
# Q: Why a private _build_tavily_adapter() rather than inline construction?
# A: Encapsulates the AsyncTavilyClient construction and response normalization.
#    Allows future swapping of search backends without touching search_async.
# Q: Why emit IMP:7/IMP:8 inside search_async (not stub_search_async)?
# A: The LDD contract requires per-query telemetry from the public surface. Tests assert
#    IMP:7 PENDING and IMP:8 SUCCESS for each query via caplog (AC13).
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v2.0.0 — async migration + Tavily search adapter; adds search_async,
#              stub_search_async, _build_tavily_adapter; removes sync stub_search.
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial stub implementation; deterministic fixed-shape output.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Public async entry — delegates to Tavily or stub_search_async] => search_async
# FUNC 6 [Deterministic async stub returning fixed-shape search result list] => stub_search_async
# FUNC 8 [Private factory — constructs AsyncTavilyClient-backed async callable] => _build_tavily_adapter
# END_MODULE_MAP
#
# START_USE_CASES:
# - [search_async]: Node2_ToolNode -> ExecuteSearchQueryAsync -> ToolFactsAccumulated
# - [stub_search_async]: TestFixture -> OfflineSearch -> DeterministicResultReturned
# - [_build_tavily_adapter]: search_async -> BuildTavilyClient -> TavilyAdapterReady
# END_USE_CASES

import asyncio
import os
from typing import Any, Callable, Dict, List

from src.core.logger import setup_ldd_logger

logger = setup_ldd_logger()


# START_FUNCTION_stub_search_async
# START_CONTRACT:
# PURPOSE: Deterministic async stub search — returns a fixed-shape single-item list
#          without network calls. Selected automatically when TAVILY_API_KEY is absent.
# INPUTS:
# - Search query string from state.search_queries => query: str
# OUTPUTS:
# - List[Dict[str, Any]] — one-element list with keys "query", "result", "source"
# SIDE_EFFECTS: None. No network calls. Yields control to event loop via asyncio.sleep(0).
# KEYWORDS: [PATTERN(8): Stub; CONCEPT(7): AsyncIO; CONCEPT(6): PluggableAdapter]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
async def stub_search_async(query: str) -> List[Dict[str, Any]]:
    """
    Deterministic async stub implementation of the search tool.

    Yields control to the event loop via asyncio.sleep(0) to behave correctly
    inside asyncio.gather() calls — this ensures cooperative scheduling even in
    the stub path. Returns a single-element list containing a dict with:
    - "query": the input query string (echoed for traceability)
    - "result": a fixed placeholder fact string
    - "source": "stub" marker identifying this as non-real data

    This function intentionally makes no network calls and always returns the
    same shape. It is the offline/test fallback for search_async when
    TAVILY_API_KEY is not present in the environment.
    """
    await asyncio.sleep(0)
    return [
        {
            "query": query,
            "result": "<stubbed-fact>",
            "source": "stub",
        }
    ]
# END_FUNCTION_stub_search_async


# START_FUNCTION__build_tavily_adapter
# START_CONTRACT:
# PURPOSE: Private factory that constructs an AsyncTavilyClient and returns a bound
#          async callable normalized to the {query, result, source} shape expected by tool_node.
# INPUTS:
# - Tavily API key from environment => api_key: str
# OUTPUTS:
# - Callable[[str], Coroutine[Any, Any, List[Dict[str, Any]]]] — async adapter function
# SIDE_EFFECTS: Constructs AsyncTavilyClient; no I/O at construction time.
# KEYWORDS: [TECH(9): TavilyAsyncClient; PATTERN(8): Factory; CONCEPT(7): ResponseNormalization]
# LINKS: [USES_API(9): tavily.AsyncTavilyClient]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
def _build_tavily_adapter(api_key: str) -> Callable:
    """
    Private factory function that constructs an AsyncTavilyClient with the provided
    API key and returns a bound async adapter callable.

    The returned adapter calls AsyncTavilyClient.search(query) and normalizes each
    Tavily result to the canonical {query, result, source} shape used throughout the
    Decision Maker tool_facts accumulation. If no results are returned, a single
    placeholder item is substituted to maintain non-empty list guarantees.

    This factory pattern keeps AsyncTavilyClient construction separated from the
    public search_async entry point, enabling clean substitution in future refactors.
    """
    from tavily import AsyncTavilyClient

    # START_BLOCK_BUILD_CLIENT: [Construct AsyncTavilyClient with api_key]
    client = AsyncTavilyClient(api_key=api_key)
    # END_BLOCK_BUILD_CLIENT

    # START_BLOCK_DEFINE_ADAPTER: [Define normalized async adapter callable]
    async def adapter(query: str) -> List[Dict[str, Any]]:
        """Adapter that calls Tavily and normalizes results to {query, result, source}."""
        response = await client.search(query)
        raw_results = response.get("results", []) if isinstance(response, dict) else []

        if not raw_results:
            return [{"query": query, "result": "<no-tavily-result>", "source": "tavily"}]

        normalized = []
        for item in raw_results:
            normalized.append({
                "query": query,
                "result": item.get("content") or item.get("snippet") or "<empty>",
                "source": item.get("url") or "tavily",
            })
        return normalized
    # END_BLOCK_DEFINE_ADAPTER

    return adapter
# END_FUNCTION__build_tavily_adapter


# START_FUNCTION_search_async
# START_CONTRACT:
# PURPOSE: Public async entry point for search. Resolves the adapter at call-time:
#          uses Tavily when TAVILY_API_KEY is set, else stub_search_async.
#          Emits [IMP:7] PENDING before the outbound call and [IMP:8] SUCCESS after.
# INPUTS:
# - Search query string => query: str
# OUTPUTS:
# - List[Dict[str, Any]] — list with keys "query", "result", "source" per item
# SIDE_EFFECTS: May make network call to Tavily API if TAVILY_API_KEY set.
#               Emits LDD log at IMP:7 (PENDING) and IMP:8 (SUCCESS) per query.
# KEYWORDS: [PATTERN(9): PluggableAdapter; CONCEPT(8): AsyncIO; TECH(9): TavilyAsyncClient;
#            CONCEPT(7): LDDTelemetry]
# LINKS: [CALLS_FUNCTION(9): _build_tavily_adapter; CALLS_FUNCTION(8): stub_search_async;
#         USES_API(7): os.environ.get]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
async def search_async(query: str) -> List[Dict[str, Any]]:
    """
    Public async search adapter entry point for the Decision Maker tool_node.

    Resolves the search backend at call-time by reading TAVILY_API_KEY from the
    environment. If the key is present, delegates to a Tavily-backed async adapter
    constructed by _build_tavily_adapter(). If the key is absent, delegates to
    stub_search_async for offline/test use — this path never makes network calls.

    Per-query LDD telemetry is emitted at IMP:7 ([PENDING]) before the outbound
    call and IMP:8 ([SUCCESS]) after receiving the response. This is required by
    AC13 (LDD parallel integrity) so that test_parallel_search can assert one
    PENDING/SUCCESS pair per query.

    Return shape: List[Dict[{query, result, source}]] — matches tool_facts schema.
    """

    # START_BLOCK_RESOLVE_ADAPTER: [Select Tavily or stub based on env var]
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        adapter = _build_tavily_adapter(tavily_key)
    else:
        adapter = stub_search_async
    # END_BLOCK_RESOLVE_ADAPTER

    # START_BLOCK_EXECUTE_SEARCH: [Emit IMP:7, call adapter, emit IMP:8]
    logger.info(
        f"[API][IMP:7][search_async][BLOCK_EXECUTE_SEARCH][ExternalCall] "
        f"query={query!r} [PENDING]"
    )

    results = await adapter(query)

    logger.info(
        f"[API][IMP:8][search_async][BLOCK_EXECUTE_SEARCH][ResponseReceived] "
        f"query={query!r} items={len(results)} [SUCCESS]"
    )
    # END_BLOCK_EXECUTE_SEARCH

    return results
# END_FUNCTION_search_async
