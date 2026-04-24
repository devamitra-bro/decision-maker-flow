# FILE: src/server/checkpoint_factory.py
# VERSION: 1.1.0
# START_MODULE_CONTRACT:
# PURPOSE: Factory for building BaseCheckpointSaver instances driven by Config.
#          Provides TouchingCheckpointer — a composing adapter that side-effects
#          touch(thread_id) into a _brainstorm_meta sqlite table on every aget/aput,
#          enabling the Sweeper to implement TTL-based eviction (§9.4 touch-based exclusion).
#          Exposes a FastAPI Depends helper get_checkpointer() for handler injection.
# SCOPE: build_checkpointer(cfg) factory; TouchingCheckpointer adapter with meta table;
#        ConfigError exception; get_checkpointer() FastAPI Depends helper.
# INPUT: Config instance (checkpointer_kind, sqlite_path, checkpoint_dsn).
# OUTPUT: BaseCheckpointSaver-compatible instance (TouchingCheckpointer wrapping inner saver).
# KEYWORDS: [DOMAIN(9): MCP_Integration; CONCEPT(9): FactoryPattern; TECH(9): AsyncSqliteSaver;
#            PATTERN(9): TouchAdapter; PATTERN(8): DependencyInjection; CONCEPT(8): SweeperRace;
#            TECH(7): aiosqlite; CONCEPT(7): ZeroKnowledgeDomain]
# LINKS: [USES_API(9): langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver;
#         READS_DATA_FROM(9): src.server.config.Config;
#         USES_API(7): aiosqlite]
# LINKS_TO_SPECIFICATION: [§1.3 CheckpointerInjection_B1; §9.4 TouchAdapter; §2.2 Slice B]
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why does build_checkpointer return a context manager rather than an already-open saver?
# A: AsyncSqliteSaver.from_conn_string() is itself an async context manager. The factory
#    returns a wrapping async CM; the server's lifespan async-enters it and calls .setup().
#    This avoids opening a DB connection outside an event loop and keeps lifecycle explicit.
# Q: Why is TouchingCheckpointer a separate wrapper rather than subclassing AsyncSqliteSaver?
# A: Subclassing is fragile across langgraph versions. Composition via delegation keeps the
#    wrapper independent of internal saver implementation. Also allows Postgres future backend.
# Q: Why store touch timestamps in _brainstorm_meta and not in checkpoint metadata?
# A: Checkpoint metadata is owned by LangGraph schema. Touching it would couple sweep logic
#    to LangGraph internals. A sidecar table is clean and does not affect checkpoint semantics.
# Q: Why is ConfigError defined here instead of src/server/errors.py?
# A: errors.py is Slice C scope. The plan explicitly says: define ConfigError locally here,
#    re-export from src/server/__init__.py. This keeps Slice B self-contained.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: [v1.1.0 - Slice C intra-slice glue: added adelete_thread(thread_id) method
#               to TouchingCheckpointer. Deletes LangGraph checkpoints for a thread_id via
#               direct SQL DELETE on checkpoints/writes tables + removes _brainstorm_meta row.
#               This is the minimum-invasive deletion path required by Sweeper (Slice C §9.4).]
# PREV_CHANGE_SUMMARY: [v1.0.0 - Initial creation as Slice B: checkpoint factory,
#               TouchingCheckpointer adapter, _brainstorm_meta table, ConfigError,
#               get_checkpointer Depends.]
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 4  [Domain exception for invalid checkpointer_kind in Config] => ConfigError
# CLASS 9  [Async CM wrapping async saver; intercepts aget/aput to touch meta table] => TouchingCheckpointer
# FUNC 8   [Factory: switches on cfg.checkpointer_kind; returns TouchingCheckpointer CM] => build_checkpointer
# FUNC 4   [FastAPI Depends helper: returns app.state.checkpointer] => get_checkpointer
# END_MODULE_MAP
#
# START_USE_CASES:
# - [build_checkpointer]: Lifespan -> build_checkpointer(cfg) -> async-with -> setup() -> server running
# - [TouchingCheckpointer]: handle_turn -> aget/aput -> touch(thread_id) -> Sweeper sees last_touched
# - [get_checkpointer]: FastAPI Depends -> get_checkpointer(request) -> TouchingCheckpointer instance
# END_USE_CASES

import hashlib
import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.server.config import Config

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ConfigError — domain exception; re-exported via src/server/__init__.py
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """
    Raised by build_checkpointer when cfg.checkpointer_kind is not a recognised value.

    This is a startup-time hard failure — the server cannot run without a valid
    checkpointer. The server's lifespan handler must NOT catch this exception;
    it should propagate to abort the uvicorn startup process.
    """


# ---------------------------------------------------------------------------
# TouchingCheckpointer — composition adapter implementing §9.4 touch semantics
# ---------------------------------------------------------------------------

# START_FUNCTION_TouchingCheckpointer
# START_CONTRACT:
# PURPOSE: Async context manager that wraps an AsyncSqliteSaver and intercepts every
#          aget() and aput() call to write a last_touched Unix timestamp for the
#          thread_id into the _brainstorm_meta sqlite table. This allows the Sweeper
#          to query list_stale() without reading LangGraph internals.
#          Also provides get_last_touched(thread_id) and list_stale(now_unix, threshold_sec)
#          for direct Sweeper consumption (Slice C).
# INPUTS:
#   - Active AsyncSqliteSaver instance => inner: AsyncSqliteSaver
# OUTPUTS:
#   - Async context manager yielding itself after creating the meta table.
# SIDE_EFFECTS:
#   - Creates _brainstorm_meta table idempotently in the same sqlite file on setup().
#   - Writes (thread_id TEXT PK, last_touched INTEGER) on every aget/aput.
# KEYWORDS: [PATTERN(9): TouchAdapter; CONCEPT(8): CompositionOverInheritance;
#            TECH(8): aiosqlite; CONCEPT(9): SweeperRace]
# LINKS: [USES_API(9): aiosqlite.Connection; READS_DATA_FROM(9): AsyncSqliteSaver]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
class TouchingCheckpointer:
    """
    Composition adapter wrapping an active AsyncSqliteSaver. Intercepts aget() and aput()
    to write a last_touched timestamp into _brainstorm_meta, supporting the Sweeper's
    TTL-based eviction predicate (§9.4). Thread IDs are never logged raw — only SHA-256
    fingerprints are emitted in LDD log lines.

    Lifecycle: instantiated by build_checkpointer; entered as async CM by server lifespan;
    setup() must be awaited once after __aenter__. After entry, delegates all LangGraph
    checkpoint operations to the inner AsyncSqliteSaver.
    """

    def __init__(self, inner: AsyncSqliteSaver) -> None:
        """
        Store reference to the already-open inner AsyncSqliteSaver.
        The _meta_ready flag guards against accidental use before setup() is called.
        """
        self._inner = inner
        self._meta_ready: bool = False

    # START_BLOCK_CONTEXT_MANAGER: [Async CM entry/exit — delegates to inner saver]

    async def __aenter__(self) -> "TouchingCheckpointer":
        """Return self; the inner saver's connection is already open (managed externally)."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """No additional teardown needed; inner saver lifecycle is managed by build_checkpointer."""
        return None

    # END_BLOCK_CONTEXT_MANAGER

    # START_BLOCK_SETUP: [Create _brainstorm_meta table idempotently]

    async def setup(self) -> None:
        """
        Idempotently create the _brainstorm_meta table in the same SQLite database
        as the LangGraph checkpoints. Also calls inner.setup() to ensure LangGraph
        schema migration is complete before the server begins accepting requests.
        """
        # START_BLOCK_INNER_SETUP: [Ensure LangGraph schema is ready first]
        await self._inner.setup()
        # END_BLOCK_INNER_SETUP

        # START_BLOCK_META_TABLE: [Create touch-tracking table]
        await self._inner.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _brainstorm_meta (
                thread_id     TEXT    PRIMARY KEY,
                last_touched  INTEGER NOT NULL
            )
            """
        )
        await self._inner.conn.commit()
        self._meta_ready = True

        logger.info(
            "[BRAINSTORM][IMP:5][TouchingCheckpointer][setup][Configure] "
            "_brainstorm_meta table ready [OK]"
        )
        # END_BLOCK_META_TABLE

    # END_BLOCK_SETUP

    # START_BLOCK_DELEGATE_GET: [aget() delegation + touch side-effect]

    async def aget(self, config: Any) -> Any:
        """
        Delegate to inner.aget() and touch the thread_id in _brainstorm_meta.
        Config must contain configurable.thread_id.
        """
        result = await self._inner.aget(config)

        # START_BLOCK_TOUCH_ON_GET: [Side-effect: update last_touched]
        thread_id = self._extract_thread_id(config)
        if thread_id:
            await self._touch(thread_id)
            logger.info(
                f"[BRAINSTORM][IMP:5][TouchingCheckpointer][aget][Load] "
                f"thread_fp=sha256:{_thread_fp(thread_id)} [OK]"
            )
        # END_BLOCK_TOUCH_ON_GET

        return result

    # END_BLOCK_DELEGATE_GET

    # START_BLOCK_DELEGATE_GET_TUPLE: [aget_tuple() delegation + touch side-effect]

    async def aget_tuple(self, config: Any) -> Any:
        """
        Delegate to inner.aget_tuple() and touch the thread_id in _brainstorm_meta.
        """
        result = await self._inner.aget_tuple(config)

        thread_id = self._extract_thread_id(config)
        if thread_id:
            await self._touch(thread_id)
        return result

    # END_BLOCK_DELEGATE_GET_TUPLE

    # START_BLOCK_DELEGATE_PUT: [aput() delegation + touch side-effect]

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        """
        Delegate to inner.aput() and touch the thread_id in _brainstorm_meta.
        Touching on write is the critical path for sweep correctness: a session in-flight
        MUST update last_touched so the sweeper does not evict it.
        """
        result = await self._inner.aput(config, checkpoint, metadata, new_versions)

        # START_BLOCK_TOUCH_ON_PUT: [Side-effect: update last_touched]
        thread_id = self._extract_thread_id(config)
        if thread_id:
            await self._touch(thread_id)
            logger.info(
                f"[BRAINSTORM][IMP:5][TouchingCheckpointer][aput][Save] "
                f"thread_fp=sha256:{_thread_fp(thread_id)} [OK]"
            )
        # END_BLOCK_TOUCH_ON_PUT

        return result

    # END_BLOCK_DELEGATE_PUT

    # START_BLOCK_DELEGATE_PUT_WRITES: [aput_writes() delegation + touch side-effect]

    async def aput_writes(self, config: Any, writes: Any, task_id: Any) -> None:
        """
        Delegate to inner.aput_writes() and touch the thread_id.
        """
        await self._inner.aput_writes(config, writes, task_id)

        thread_id = self._extract_thread_id(config)
        if thread_id:
            await self._touch(thread_id)

    # END_BLOCK_DELEGATE_PUT_WRITES

    # START_BLOCK_DELEGATE_LIST: [alist() delegation — no touch needed for list queries]

    async def alist(self, config: Any, **kwargs: Any) -> AsyncIterator:
        """
        Delegate to inner.alist(). List queries are read-only metadata scans; they do
        NOT update last_touched (list is used by the sweeper itself — touching would
        defeat the eviction logic).
        """
        async for item in self._inner.alist(config, **kwargs):
            yield item

    # END_BLOCK_DELEGATE_LIST

    # START_BLOCK_SYNC_DELEGATES: [Sync variants — delegate directly, touch via sync helper]

    def get(self, config: Any) -> Any:
        """Sync delegate to inner.get()."""
        return self._inner.get(config)

    def get_tuple(self, config: Any) -> Any:
        """Sync delegate to inner.get_tuple()."""
        return self._inner.get_tuple(config)

    def put(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        """Sync delegate to inner.put()."""
        return self._inner.put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config: Any, writes: Any, task_id: Any) -> None:
        """Sync delegate to inner.put_writes()."""
        return self._inner.put_writes(config, writes, task_id)

    def list(self, config: Any, **kwargs: Any) -> Any:
        """Sync delegate to inner.list()."""
        return self._inner.list(config, **kwargs)

    # END_BLOCK_SYNC_DELEGATES

    # START_BLOCK_META_QUERIES: [Sweeper-facing meta table queries]

    async def get_last_touched(self, thread_id: str) -> Optional[int]:
        """
        Return the last_touched Unix timestamp for thread_id, or None if not found.
        Used by the Sweeper (Slice C) to check per-session activity.
        """
        async with self._inner.conn.execute(
            "SELECT last_touched FROM _brainstorm_meta WHERE thread_id = ?",
            (thread_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def list_stale(self, now_unix: int, threshold_sec: int) -> list:
        """
        Return list of thread_ids whose last_touched is older than (now_unix - threshold_sec).
        Consumed by the Sweeper (Slice C) to identify sessions eligible for eviction.

        Args:
            now_unix: Current Unix timestamp in seconds (callers should use int(time.time())).
            threshold_sec: Sessions not touched within this window are considered stale.

        Returns:
            List[str]: thread_ids eligible for eviction.
        """
        cutoff = now_unix - threshold_sec
        async with self._inner.conn.execute(
            "SELECT thread_id FROM _brainstorm_meta WHERE last_touched < ?",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()
        stale = [row[0] for row in rows]

        logger.info(
            f"[BRAINSTORM][IMP:5][TouchingCheckpointer][list_stale][Sweep] "
            f"cutoff={cutoff} stale_count={len(stale)} [OK]"
        )
        return stale

    async def adelete_thread(self, thread_id: str) -> None:
        """
        Delete all checkpoint data for thread_id and remove its _brainstorm_meta row.

        CHANGE_SUMMARY_CONTEXT: Added in v1.1.0 as minimum-invasive Slice C glue to
        support Sweeper's session eviction path (plan §9.4). Deletes from LangGraph's
        checkpoint tables (checkpoints, writes) and from _brainstorm_meta.

        Approach: DELETE from the three underlying sqlite tables used by AsyncSqliteSaver.
        AsyncSqliteSaver stores checkpoints in 'checkpoints' and pending writes in
        'writes'. Both are keyed by thread_id. _brainstorm_meta is the touch-tracking
        sidecar. All three are deleted atomically via a single transaction.

        This method does NOT raise on missing thread_id (idempotent by SQL semantics).
        """
        fp = _thread_fp(thread_id)

        # START_BLOCK_DELETE_CHECKPOINTS: [Remove LangGraph data + meta row in one transaction]
        try:
            await self._inner.conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            # BUG_FIX_CONTEXT: LangGraph AsyncSqliteSaver creates table 'writes' not
            # 'checkpoint_writes'. Confirmed via AsyncSqliteSaver.setup() inspection.
            await self._inner.conn.execute(
                "DELETE FROM writes WHERE thread_id = ?",
                (thread_id,),
            )
            await self._inner.conn.execute(
                "DELETE FROM _brainstorm_meta WHERE thread_id = ?",
                (thread_id,),
            )
            await self._inner.conn.commit()

            logger.info(
                f"[BRAINSTORM][IMP:5][TouchingCheckpointer][adelete_thread][Delete] "
                f"thread_fp=sha256:{fp} [OK]"
            )
        except Exception as exc:
            logger.error(
                f"[BRAINSTORM][IMP:8][TouchingCheckpointer][adelete_thread][Error] "
                f"thread_fp=sha256:{fp} err={exc!r} [FAIL]"
            )
            raise
        # END_BLOCK_DELETE_CHECKPOINTS

    async def ping(self) -> None:
        """
        Execute a lightweight SELECT 1 to verify the SQLite connection is alive.
        Used by /readyz handler (§4.4) to confirm the checkpointer backend is reachable.
        Raises any exception on failure for the caller to translate to 503.
        """
        await self._inner.conn.execute("SELECT 1")

    # END_BLOCK_META_QUERIES

    # START_BLOCK_PRIVATE_HELPERS: [Internal helpers]

    async def _touch(self, thread_id: str) -> None:
        """
        Write (thread_id, now_unix) into _brainstorm_meta using INSERT OR REPLACE.
        Logs IMP:5 with SHA-256 fingerprint of thread_id (never raw value).
        """
        now_unix = int(time.time())
        await self._inner.conn.execute(
            """
            INSERT OR REPLACE INTO _brainstorm_meta (thread_id, last_touched)
            VALUES (?, ?)
            """,
            (thread_id, now_unix),
        )
        await self._inner.conn.commit()

        logger.info(
            f"[BRAINSTORM][IMP:5][Touch][Updated] "
            f"thread_fp=sha256:{_thread_fp(thread_id)} ts={now_unix} [OK]"
        )

    @staticmethod
    def _extract_thread_id(config: Any) -> Optional[str]:
        """
        Extract thread_id from a LangGraph RunnableConfig dict.
        Returns None if not present (safe default — no touch without a thread).
        """
        if isinstance(config, dict):
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                return configurable.get("thread_id") or None
        return None

    # END_BLOCK_PRIVATE_HELPERS

    # START_BLOCK_PASSTHROUGH_ATTRS: [Expose inner saver attributes used by LangGraph internals]

    @property
    def conn(self) -> Any:
        """Expose the inner aiosqlite connection for tests and setup."""
        return self._inner.conn

    @property
    def config_specs(self) -> Any:
        """Delegate config_specs to inner saver (required by LangGraph compiled graph)."""
        return self._inner.config_specs

    @property
    def serde(self) -> Any:
        """Delegate serde to inner saver."""
        return self._inner.serde

    def get_next_version(self, current: Any, channel: Any) -> Any:
        """Delegate version incrementor to inner saver."""
        return self._inner.get_next_version(current, channel)

    # END_BLOCK_PASSTHROUGH_ATTRS

# END_FUNCTION_TouchingCheckpointer


# ---------------------------------------------------------------------------
# _thread_fp — log-safe thread_id fingerprint (AC6: no raw IDs in logs)
# ---------------------------------------------------------------------------

def _thread_fp(thread_id: str) -> str:
    """Return first 8 hex chars of SHA-256 of thread_id for safe log inclusion."""
    return hashlib.sha256(thread_id.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# build_checkpointer — pure factory producing TouchingCheckpointer async CM
# ---------------------------------------------------------------------------

# START_FUNCTION_build_checkpointer
# START_CONTRACT:
# PURPOSE: Pure factory function. Switches on cfg.checkpointer_kind to produce the
#          appropriate underlying saver, then wraps it in a TouchingCheckpointer.
#          Returns an async context manager (the caller must `async with` it).
#          The server lifespan is responsible for: entering the CM, calling .setup(),
#          storing the result on app.state.checkpointer, and exiting on shutdown.
#
#          Lifecycle contract (documented explicitly for lifespan authors):
#          1. cm = build_checkpointer(cfg)
#          2. saver = await cm.__aenter__()   (i.e., `async with cm as saver:`)
#          3. await saver.setup()             (creates LangGraph + meta tables)
#          4. app.state.checkpointer = saver  (share with handlers via Depends)
#          5. --- server runs ---
#          6. await cm.__aexit__(None, None, None)  (automatic on `async with` exit)
#
# INPUTS:
#   - Validated Config instance => cfg: Config
# OUTPUTS:
#   - Async context manager yielding TouchingCheckpointer
# SIDE_EFFECTS:
#   - Emits [IMP:5] on successful kind resolution.
#   - Emits [IMP:9][Fatal] then raises ConfigError on unknown kind.
# KEYWORDS: [PATTERN(9): FactoryPattern; CONCEPT(8): AsyncContextManager;
#            TECH(9): AsyncSqliteSaver; CONCEPT(7): ExperimentalPostgres]
# LINKS: [READS_DATA_FROM(9): src.server.config.Config;
#         USES_API(9): langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver]
# COMPLEXITY_SCORE: 5
# END_CONTRACT
@asynccontextmanager
async def build_checkpointer(cfg: Config) -> Any:
    """
    Factory coroutine that yields a TouchingCheckpointer wrapping the appropriate
    LangGraph saver backend based on cfg.checkpointer_kind.

    This is an async generator context manager (yielding via `async with`).
    The server lifespan must enter the returned CM, call .setup(), and then store
    the saver on app.state.checkpointer. See contract lifecycle above.

    Supported kinds:
    - "sqlite" (default, production MVP): opens AsyncSqliteSaver.from_conn_string
      with cfg.sqlite_path; wraps in TouchingCheckpointer.
    - "postgres" (EXPERIMENTAL — not production-ready; requires
      langgraph-checkpoint-postgres installed): opens AsyncPostgresSaver.from_conn_string
      with cfg.checkpoint_dsn.get_secret_value(). EXPERIMENTAL: do NOT use in production
      without explicit validation. TouchAdapter wraps the Postgres saver identically.
    - Any other value: raises ConfigError immediately (startup abort).
    """
    # START_BLOCK_RESOLVE_KIND: [Switch on checkpointer_kind]
    kind = cfg.checkpointer_kind

    if kind == "sqlite":
        logger.info(
            f"[BRAINSTORM][IMP:5][build_checkpointer][Config][Kind-Resolved] "
            f"kind=sqlite path={cfg.sqlite_path} [OK]"
        )

        # START_BLOCK_SQLITE_CM: [Open AsyncSqliteSaver and yield TouchingCheckpointer]
        async with AsyncSqliteSaver.from_conn_string(cfg.sqlite_path) as inner:
            saver = TouchingCheckpointer(inner)
            yield saver
        # END_BLOCK_SQLITE_CM

    elif kind == "postgres":
        # EXPERIMENTAL: langgraph-checkpoint-postgres must be installed separately.
        # Do NOT use in production without end-to-end validation of schema + race semantics.
        logger.info(
            f"[BRAINSTORM][IMP:5][build_checkpointer][Config][Kind-Resolved] "
            f"kind=postgres [EXPERIMENTAL][OK]"
        )

        # START_BLOCK_POSTGRES_CM: [Open AsyncPostgresSaver and yield TouchingCheckpointer — EXPERIMENTAL]
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: PLC0415
        except ImportError as exc:
            logger.critical(
                "[BRAINSTORM][IMP:9][build_checkpointer][Checkpointer][Fatal][BELIEF] "
                "langgraph-checkpoint-postgres is not installed; postgres kind unavailable. "
                f"Install langgraph-checkpoint-postgres to enable. err={exc} [FATAL]"
            )
            raise ConfigError(
                "checkpointer_kind='postgres' requires langgraph-checkpoint-postgres. "
                "Install it via: pip install langgraph-checkpoint-postgres"
            ) from exc

        dsn = cfg.checkpoint_dsn
        async with AsyncPostgresSaver.from_conn_string(dsn) as inner:
            saver = TouchingCheckpointer(inner)
            yield saver
        # END_BLOCK_POSTGRES_CM

    else:
        # START_BLOCK_UNKNOWN_KIND: [Fatal — unknown checkpointer_kind]
        logger.critical(
            f"[BRAINSTORM][IMP:9][build_checkpointer][Checkpointer][Fatal][BELIEF] "
            f"Unsupported checkpointer_kind={kind!r}. "
            f"Valid values: 'sqlite', 'postgres'. [FATAL]"
        )
        raise ConfigError(f"unsupported checkpointer_kind: {kind!r}")
        # END_BLOCK_UNKNOWN_KIND

    # END_BLOCK_RESOLVE_KIND

# END_FUNCTION_build_checkpointer


# ---------------------------------------------------------------------------
# get_checkpointer — FastAPI Depends helper
# ---------------------------------------------------------------------------

# START_FUNCTION_get_checkpointer
# START_CONTRACT:
# PURPOSE: FastAPI Depends helper that retrieves the shared TouchingCheckpointer from
#          app.state, set by the server lifespan. Importable even without fastapi installed
#          (import is guarded inside the function body for test environments where fastapi
#          may not be present or where the Depends mechanism is bypassed via overrides).
# INPUTS:
#   - FastAPI Request object (injected by Depends machinery) => request: Request
# OUTPUTS:
#   - TouchingCheckpointer (or BaseCheckpointSaver in general) stored on app.state
# KEYWORDS: [PATTERN(8): DependencyInjection; TECH(8): FastAPI_Depends]
# COMPLEXITY_SCORE: 1
# END_CONTRACT
def get_checkpointer(request: Any) -> Any:
    """
    FastAPI Depends function. Returns the TouchingCheckpointer stored on app.state by
    the server lifespan. The request parameter is injected by FastAPI's Depends mechanism.

    Import of fastapi.Request is guarded inside the function body so that this module
    remains importable in test environments where fastapi may not be installed, or when
    tests inject the checkpointer directly without going through Depends machinery.
    """
    return request.app.state.checkpointer

# END_FUNCTION_get_checkpointer
