# FILE: src/server/idempotency.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Idempotency cache for /turn endpoint per §9.1. Wraps a cachetools.TTLCache
#          (maxsize=10000, ttl=600s) with an asyncio.Lock for async-safe lookup+insert.
#          Provides two key strategies: header-based (from Idempotency-Key header) and
#          internal tuple-based (sha256 of session_id + message + turn_n). Falls back
#          to internal key when header is absent or does not match the allowed regex.
# SCOPE: IdempotencyCache class; TTLCache wrapping; async get/set operations;
#        key construction from header or tuple; log-safe fingerprinting for malformed keys.
# INPUT: Idempotency-Key header string (optional); session_id, message, turn_n for internal key.
# OUTPUT: Cached response dict or None (cache miss).
# KEYWORDS: [DOMAIN(9): Idempotency; TECH(9): cachetools_TTLCache; CONCEPT(9): AsyncLock;
#            PATTERN(8): CacheAsidePattern; CONCEPT(8): KeyDerivation; TECH(8): SHA256_hex]
# LINKS: [USES_API(10): cachetools.TTLCache; USES_API(9): asyncio.Lock]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_MCP.md §9.1 (IdempotencyCache), §2.3 (AC8)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - Lock guards every get+set pair — no concurrent insert race for same key.
# - TTLCache entries expire automatically after 600 seconds (no manual eviction needed).
# - Header key regex: ^[a-zA-Z0-9_\-]{8,128}$ — non-matching header falls back to internal.
# - Internal key: sha256(session_id + "\x00" + message + "\x00" + str(turn_n))[:32] hex.
# - Malformed header is NEVER logged raw — only sha256:<8hex> fingerprint at IMP:5.
# - make_key_from_header returns None on absent or malformed header (caller uses internal).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why asyncio.Lock instead of threading.Lock?
# A: FastAPI runs in an asyncio event loop. asyncio.Lock is the correct primitive for
#    async-safe critical sections. threading.Lock would block the event loop if another
#    coroutine holds it, causing latency spikes.
# Q: Why TTL 600 seconds (10 minutes)?
# A: The header Idempotency-Key spec (e.g. Stripe) uses 24h windows. We use 10 minutes
#    to limit memory footprint (maxsize=10000) while covering retry scenarios (most clients
#    retry within seconds to minutes, not hours). This aligns with plan §9.1.
# Q: Why separate make_key_from_header and make_key_from_tuple methods?
# A: Clean separation of concerns — handler logic selects which key strategy to use;
#    key construction details live in the cache. Testable in isolation.
# Q: Why "\x00" as separator in internal key?
# A: Null byte is guaranteed not to appear in session_id (UUID-v4), message, or turn_n
#    string. Using a separator prevents ambiguous hash collisions like:
#    session="ab" + message="cdef" vs session="abc" + message="def".
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.0.0 - Initial creation as Slice C: IdempotencyCache with TTLCache,
#               asyncio.Lock, header/internal key strategies per §9.1.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 9 [Async-safe TTL cache: get/set + two key derivation strategies] => IdempotencyCache
# END_MODULE_MAP
#
# START_USE_CASES:
# - [IdempotencyCache]: handle_turn -> make_key_from_header(header) or make_key_from_tuple(...)
#   -> get(key) -> cache hit: return cached; miss: run LLM -> set(key, reply)
# END_USE_CASES

import asyncio
import hashlib
import logging
import re
from typing import Any, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Compiled regex for valid Idempotency-Key header values per §9.1
_IDEMPOTENCY_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-]{8,128}$")


def _key_fp(raw: str) -> str:
    """Return sha256:<8hex> fingerprint of a key string for safe log inclusion."""
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


# START_FUNCTION_IdempotencyCache
# START_CONTRACT:
# PURPOSE: Async-safe idempotency cache wrapping cachetools.TTLCache with asyncio.Lock.
#          Provides get() and set() for cache-aside pattern; make_key_from_header() and
#          make_key_from_tuple() for key derivation strategies. Thread-safe via Lock.
# INPUTS:
#   - maxsize: int — maximum number of entries (default 10000)
#   - ttl: int — entry lifetime in seconds (default 600)
# OUTPUTS: IdempotencyCache instance ready for async get/set/key operations.
# SIDE_EFFECTS: Logs at IMP:5 when make_key_from_header encounters a malformed header.
# KEYWORDS: [PATTERN(9): CacheAside; TECH(9): TTLCache; CONCEPT(9): AsyncLock;
#            PATTERN(8): KeyDerivation]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
class IdempotencyCache:
    """
    Async-safe idempotency cache for the /turn endpoint.

    Wraps cachetools.TTLCache(maxsize=10000, ttl=600) with an asyncio.Lock that guards
    every lookup+insert pair, preventing concurrent duplicate-turn races. Entries expire
    automatically after 600 seconds per §9.1.

    Two key derivation strategies are provided:
    1. Header-based: use Idempotency-Key header value if it matches the regex.
    2. Internal tuple-based: derive key from sha256(session_id + NUL + message + NUL + str(turn_n)).

    The cache stores arbitrary JSON-serializable values (turn reply dicts). Callers are
    responsible for serialization; the cache stores whatever is passed to set().
    """

    def __init__(self, maxsize: int = 10000, ttl: int = 600) -> None:
        """
        Initialize the idempotency cache with the given TTLCache parameters and a
        fresh asyncio.Lock. The maxsize and ttl match plan §9.1 defaults.
        """
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock: asyncio.Lock = asyncio.Lock()

    # START_BLOCK_ASYNC_GET: [Async cache lookup — Lock-guarded]

    async def get(self, key: str) -> Optional[Any]:
        """
        Look up key in the TTLCache under the asyncio.Lock. Returns the cached value
        or None on a miss (including expired entries, which TTLCache auto-evicts).

        The Lock ensures that a concurrent set() for the same key cannot race with
        this get() — preventing both duplicate LLM calls and torn reads.
        """
        async with self._lock:
            return self._cache.get(key)

    # END_BLOCK_ASYNC_GET

    # START_BLOCK_ASYNC_SET: [Async cache insertion — Lock-guarded]

    async def set(self, key: str, value: Any) -> None:
        """
        Insert or update key→value in the TTLCache under the asyncio.Lock.
        TTLCache automatically evicts the LRU entry when maxsize is exceeded.
        """
        async with self._lock:
            self._cache[key] = value

    # END_BLOCK_ASYNC_SET

    # START_BLOCK_KEY_FROM_HEADER: [Header-based key derivation per §9.1]

    def make_key_from_header(self, header_value: Optional[str]) -> Optional[str]:
        """
        Derive an idempotency key from the Idempotency-Key HTTP header.

        Returns None if:
        - header_value is None (header absent)
        - header_value does not match ^[a-zA-Z0-9_\\-]{8,128}$

        On malformed header: logs IMP:5 with sha256:<8hex> fingerprint; returns None
        (caller falls back to internal tuple key). Raw header value is never logged.

        On valid header: returns the header value unchanged for use as cache key.
        """
        if header_value is None:
            return None

        if _IDEMPOTENCY_KEY_RE.match(header_value):
            return header_value

        # Malformed header — log fingerprint only, never raw value
        fp = _key_fp(header_value)
        logger.warning(
            f"[BRAINSTORM][IMP:5][Idempotency][MalformedKey] "
            f"key_fp={fp} reason=regex_mismatch fallback=internal [WARN]"
        )
        return None

    # END_BLOCK_KEY_FROM_HEADER

    # START_BLOCK_KEY_FROM_TUPLE: [Internal tuple-based key derivation per §9.1]

    def make_key_from_tuple(self, session_id: str, message: str, turn_n: int) -> str:
        """
        Derive an idempotency key from the (session_id, message, turn_n) tuple.

        Key formula: sha256(session_id + "\\x00" + message + "\\x00" + str(turn_n))[:32]

        The null-byte separator prevents hash collisions between different splits of
        the same concatenated string. The 32-hex-char prefix provides 128 bits of
        entropy — sufficient for collision resistance at this scale.

        This key is deterministic: the same (session_id, message, turn_n) always
        produces the same key, enabling retry detection without client cooperation.
        """
        raw_input = session_id + "\x00" + message + "\x00" + str(turn_n)
        return hashlib.sha256(raw_input.encode("utf-8")).hexdigest()[:32]

    # END_BLOCK_KEY_FROM_TUPLE

# END_FUNCTION_IdempotencyCache
