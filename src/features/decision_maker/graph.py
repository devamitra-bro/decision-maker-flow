# FILE: src/features/decision_maker/graph.py
# VERSION: 3.0.0
# START_MODULE_CONTRACT:
# PURPOSE: LangGraph StateGraph assembly and public async session API for Decision Maker.
# SCOPE: build_graph(checkpointer) — sync factory accepting pre-built checkpointer via DI.
#        start_session / resume_session — async public API using AsyncSqliteSaver lifecycle.
#        No internal SqliteSaver construction in production paths.
# INPUT: Optional checkpoint_path for test isolation via tmp_path; defaults to
#        brainstorm/checkpoints.sqlite relative to module location.
#        Checkpointer is accepted as DI parameter in build_graph() for test isolation.
# OUTPUT: CompiledStateGraph; async session dicts from start_session and resume_session.
# KEYWORDS: [DOMAIN(10): Orchestration; CONCEPT(9): LangGraph; TECH(9): AsyncSqliteSaver;
#            PATTERN(8): HumanInTheLoop; PATTERN(7): SessionAPI; CONCEPT(10): AsyncIO;
#            PATTERN(8): DependencyInjection]
# LINKS: [USES_API(10): langgraph.graph.StateGraph;
#         USES_API(9): langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver;
#         USES_API(8): src.features.decision_maker.nodes;
#         USES_API(7): src.features.decision_maker.state]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §3.1 (graph_py), §3.2 Data Flow; AC5, AC8, AC9
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - build_graph(checkpointer) ALWAYS returns a CompiledStateGraph with the provided checkpointer.
# - interrupt_after ALWAYS includes "3_Weight_Questioner" (human-in-the-loop gate).
# - Node IDs in add_node() EXACTLY match scenario_1_flow.xml Graph_Topology Nodes.
# - start_session() ALWAYS returns dict with "status" == "awaiting_user".
# - resume_session() ALWAYS returns dict with "status" == "done" or re-raises on graph error.
# - No sqlite3.connect() and no synchronous SqliteSaver in any production path (AC9).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why does build_graph accept checkpointer via DI instead of constructing it internally?
# A: Concept A (per-call checkpointer context pattern) — start_session and resume_session
#    each open `async with AsyncSqliteSaver.from_conn_string(path)` and pass the live
#    checkpointer into build_graph. Tests pass MemorySaver() directly. This eliminates
#    lifecycle coupling between graph compilation and SQLite connection management.
# Q: Why open AsyncSqliteSaver per-call (not module-level)?
# A: aiosqlite connections should not be shared across event-loop boundaries. Per-call
#    context managers ensure clean open/close on every session invocation. This matches
#    the LangGraph recommended pattern for async checkpointers.
# Q: Why does build_graph remain synchronous?
# A: Graph compilation (StateGraph.compile()) is CPU-bound and has no I/O. Only the
#    session calls that invoke/await graph operations need to be async.
# Q: Why accept checkpoint_path as a parameter in start_session / resume_session?
# A: AC16 prohibits hardcoded absolute paths in source. Tests use tmp_path for isolation.
#    The override parameter provides a consistent API surface for test control.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v3.0.0 — additive streaming parallel API; stream_session and stream_resume_session
#              async-generator functions appended to expose astream(stream_mode="updates") for
#              the Gradio UI layer. No changes to build_graph, start_session, resume_session.
# PREV_CHANGE_SUMMARY: v2.0.0 — async migration + Tavily search adapter; build_graph now accepts
#              checkpointer via DI (no internal SqliteSaver); start_session and resume_session
#              become async def using AsyncSqliteSaver; all sqlite3.connect refs removed.
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial implementation; StateGraph assembly + SqliteSaver + session API.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 10 [Sync factory — assembles and compiles StateGraph; accepts checkpointer via DI] => build_graph
# FUNC 9 [Async public API — seeds state, starts graph, returns awaiting_user response] => start_session
# FUNC 9 [Async public API — resumes after human-in-the-loop, returns final answer] => resume_session
# FUNC 10 [Async generator — streaming parallel API; yields chunk dicts + awaiting_user terminator] => stream_session
# FUNC 10 [Async generator — streaming parallel API; yields chunk dicts + final_answer terminator] => stream_resume_session
# END_MODULE_MAP
#
# START_USE_CASES:
# - [build_graph]: System -> CompileGraph -> CompiledStateGraphReady
# - [start_session]: ExternalAsyncCaller -> SeedAndInvokeGraph -> UserQuestionReturned
# - [resume_session]: ExternalAsyncCaller -> ResumeAfterInterrupt -> FinalAnswerReturned
# END_USE_CASES

from pathlib import Path
from typing import Any, Optional

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from src.core.logger import setup_ldd_logger
from src.features.decision_maker.nodes import (
    context_analyzer,
    cove_critique,
    draft_generator,
    final_synthesizer,
    route_from_context,
    route_from_critique,
    tool_node,
    weight_parser,
    weight_questioner,
)
from src.features.decision_maker.state import DecisionMakerState

logger = setup_ldd_logger()

# Default checkpoint path: brainstorm_root/checkpoints.sqlite
# Resolved relative to this file: graph.py -> decision_maker/ -> features/ -> src/ -> brainstorm/
_DEFAULT_CHECKPOINT_PATH = str(
    Path(__file__).resolve().parent.parent.parent.parent / "checkpoints.sqlite"
)


# START_FUNCTION_build_graph
# START_CONTRACT:
# PURPOSE: Synchronous factory — assemble, compile, and return the Decision Maker StateGraph
#          with all 7 nodes, edges per scenario_1_flow.xml, interrupt_after=["3_Weight_Questioner"],
#          and the caller-supplied checkpointer (DI pattern).
# INPUTS:
# - Pre-built checkpointer (MemorySaver in tests, AsyncSqliteSaver in production) => checkpointer: Any
# OUTPUTS:
# - CompiledStateGraph — ready for ainvoke() / aupdate_state() / aget_state() calls
# SIDE_EFFECTS: Emits LDD log at IMP:6, IMP:9.
# KEYWORDS: [PATTERN(9): GraphBuilder; TECH(9): AsyncSqliteSaver; CONCEPT(8): LangGraph;
#            PATTERN(8): DependencyInjection]
# LINKS: [USES_API(10): StateGraph; CALLS_FUNCTION(8): all node functions]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
def build_graph(checkpointer: Any):
    """
    Synchronous factory that assembles and compiles the Decision Maker LangGraph StateGraph.

    Accepts a pre-built checkpointer via Dependency Injection — this is required by the
    Concept A architecture (per-call async context manager in start_session/resume_session).
    Tests pass MemorySaver(); production passes AsyncSqliteSaver from an active context.

    Graph topology reproduces scenario_1_flow.xml Edges block exactly:
    - START -> 1_Context_Analyzer
    - 1_Context_Analyzer -> (conditional) 2_Tool_Node (needs_data) | 3_Weight_Questioner (ready)
    - 2_Tool_Node -> 1_Context_Analyzer  (loop back after data collection)
    - 3_Weight_Questioner -> 3.5_Weight_Parser  (after human answer injected post-interrupt)
    - 3.5_Weight_Parser -> 4_Draft_Generator
    - 4_Draft_Generator -> 5_CoVe_Critique
    - 5_CoVe_Critique -> (conditional) 4_Draft_Generator (rewrite) | 6_Final_Synthesizer (finalize)
    - 6_Final_Synthesizer -> END

    interrupt_after=["3_Weight_Questioner"] implements the human-in-the-loop gate.
    """

    # START_BLOCK_INIT_GRAPH: [Create StateGraph builder]
    logger.info(
        f"[Flow][IMP:6][build_graph][BLOCK_INIT_GRAPH][Configure] "
        f"Building Decision Maker StateGraph with DI checkpointer. "
        f"checkpointer_type={type(checkpointer).__name__} [START]"
    )

    graph = StateGraph(DecisionMakerState)
    # END_BLOCK_INIT_GRAPH

    # START_BLOCK_ADD_NODES: [Register all 7 nodes with exact IDs from scenario_1_flow.xml]
    graph.add_node("1_Context_Analyzer", context_analyzer)
    graph.add_node("2_Tool_Node", tool_node)
    graph.add_node("3_Weight_Questioner", weight_questioner)
    graph.add_node("3.5_Weight_Parser", weight_parser)
    graph.add_node("4_Draft_Generator", draft_generator)
    graph.add_node("5_CoVe_Critique", cove_critique)
    graph.add_node("6_Final_Synthesizer", final_synthesizer)
    # END_BLOCK_ADD_NODES

    # START_BLOCK_ADD_EDGES: [Wire edges per scenario_1_flow.xml Edges block]
    # Edge: START -> 1_Context_Analyzer
    graph.add_edge(START, "1_Context_Analyzer")

    # Conditional edge: 1_Context_Analyzer -> tool OR questioner
    graph.add_conditional_edges(
        "1_Context_Analyzer",
        route_from_context,
        {
            "tool": "2_Tool_Node",
            "questioner": "3_Weight_Questioner",
        },
    )

    # Edge: 2_Tool_Node -> 1_Context_Analyzer (loop back)
    graph.add_edge("2_Tool_Node", "1_Context_Analyzer")

    # Edge: 3_Weight_Questioner -> 3.5_Weight_Parser (after human answer injected post-interrupt)
    graph.add_edge("3_Weight_Questioner", "3.5_Weight_Parser")

    # Edge: 3.5_Weight_Parser -> 4_Draft_Generator
    graph.add_edge("3.5_Weight_Parser", "4_Draft_Generator")

    # Edge: 4_Draft_Generator -> 5_CoVe_Critique
    graph.add_edge("4_Draft_Generator", "5_CoVe_Critique")

    # Conditional edge: 5_CoVe_Critique -> rewrite OR finalize
    graph.add_conditional_edges(
        "5_CoVe_Critique",
        route_from_critique,
        {
            "rewrite": "4_Draft_Generator",
            "finalize": "6_Final_Synthesizer",
        },
    )

    # Edge: 6_Final_Synthesizer -> END
    graph.add_edge("6_Final_Synthesizer", END)
    # END_BLOCK_ADD_EDGES

    # START_BLOCK_COMPILE: [Compile graph with provided checkpointer]
    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_after=["3_Weight_Questioner"],
    )

    logger.info(
        f"[BeliefState][IMP:9][build_graph][BLOCK_COMPILE][BusinessLogic] "
        f"Graph compiled successfully. checkpointer={type(checkpointer).__name__} "
        f"interrupt_after=['3_Weight_Questioner'] [SUCCESS]"
    )
    # END_BLOCK_COMPILE

    return compiled
# END_FUNCTION_build_graph


# START_FUNCTION_start_session
# START_CONTRACT:
# PURPOSE: Async public API. Seed graph state with user_input, invoke the graph (runs
#          until interrupt), and return the calibration question to the async caller.
#          Opens AsyncSqliteSaver per-call as an async context manager.
# INPUTS:
# - Natural language decision problem from user => user_input: str
# - Unique thread identifier for this session => thread_id: str
# - Optional checkpoint path override (default: brainstorm/checkpoints.sqlite) => checkpoint_path: str | None
# OUTPUTS:
# - dict: {"status": "awaiting_user", "question": str, "thread_id": str}
# SIDE_EFFECTS: Writes async checkpoint to SQLite; emits LDD log at IMP:6, IMP:9.
# KEYWORDS: [PATTERN(9): SessionAPI; CONCEPT(8): HumanInTheLoop; TECH(7): AsyncLangGraphInvoke;
#            TECH(9): AsyncSqliteSaver; CONCEPT(10): AsyncIO]
# LINKS: [CALLS_FUNCTION(9): build_graph; USES_API(9): AsyncSqliteSaver.from_conn_string;
#         USES_API(8): graph.ainvoke; USES_API(7): graph.aget_state]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
async def start_session(
    user_input: str,
    thread_id: str,
    checkpoint_path: Optional[str] = None,
) -> dict:
    """
    Async entry point for a new Decision Maker session.

    Opens AsyncSqliteSaver.from_conn_string(path) as an async context manager (per-call
    pattern, Concept A). Inside the context: builds the graph via DI, seeds initial state
    (user_input, empty tool_facts, rewrite_count=0), then awaits graph.ainvoke(). The graph
    runs from START through Node 1 (and optionally Node 2) until it reaches the interrupt
    after Node 3 (3_Weight_Questioner). At that point the graph suspends and this function
    reads the checkpointed state via await graph.aget_state() to extract last_question.

    Returns a dict ready for the API layer with:
    - status: "awaiting_user" (always on successful first leg)
    - question: the calibration question from Node 3
    - thread_id: echoed back for correlation

    The AsyncSqliteSaver context is closed on exit — the DB file persists for resume_session.
    """

    # START_BLOCK_RESOLVE_PATH: [Determine checkpoint path]
    path = str(checkpoint_path) if checkpoint_path else _DEFAULT_CHECKPOINT_PATH

    logger.info(
        f"[Flow][IMP:6][start_session][BLOCK_RESOLVE_PATH][Configure] "
        f"Starting session. thread_id={thread_id!r} checkpoint_path={path} [START]"
    )
    # END_BLOCK_RESOLVE_PATH

    # START_BLOCK_OPEN_CHECKPOINTER: [Open AsyncSqliteSaver as async context manager]
    async with AsyncSqliteSaver.from_conn_string(path) as cp:
        graph = build_graph(cp)
        config = {"configurable": {"thread_id": thread_id}}

        # START_BLOCK_INVOKE: [Seed initial state and invoke graph]
        initial_state = {
            "user_input": user_input,
            "tool_facts": [],
            "rewrite_count": 0,
        }
        await graph.ainvoke(initial_state, config)
        # END_BLOCK_INVOKE

        # START_BLOCK_READ_STATE: [Read checkpointed state to extract question]
        snapshot = await graph.aget_state(config)
        last_question = snapshot.values.get("last_question") or ""

        logger.info(
            f"[BeliefState][IMP:9][start_session][BLOCK_READ_STATE][BusinessLogic] "
            f"Session started. thread_id={thread_id!r} "
            f"last_question={last_question!r} [VALUE]"
        )
        # END_BLOCK_READ_STATE
    # END_BLOCK_OPEN_CHECKPOINTER

    return {
        "status": "awaiting_user",
        "question": last_question,
        "thread_id": thread_id,
    }
# END_FUNCTION_start_session


# START_FUNCTION_resume_session
# START_CONTRACT:
# PURPOSE: Async public API. Resume an interrupted session by injecting the user's answer,
#          continuing the graph to completion, and returning the final answer.
#          Opens AsyncSqliteSaver per-call as an async context manager.
# INPUTS:
# - Human-in-the-loop reply to the calibration question => user_answer: str
# - Thread identifier matching the interrupted session => thread_id: str
# - Optional checkpoint path override => checkpoint_path: str | None
# OUTPUTS:
# - dict: {"status": "done", "final_answer": str, "thread_id": str}
# SIDE_EFFECTS: Updates async checkpoint in SQLite; emits LDD log at IMP:6, IMP:9.
# KEYWORDS: [PATTERN(9): SessionAPI; CONCEPT(8): HumanInTheLoop; TECH(7): AsyncLangGraphUpdateState;
#            TECH(9): AsyncSqliteSaver; CONCEPT(10): AsyncIO]
# LINKS: [CALLS_FUNCTION(9): build_graph; USES_API(9): AsyncSqliteSaver.from_conn_string;
#         USES_API(8): graph.aupdate_state; USES_API(8): graph.ainvoke; USES_API(7): graph.aget_state]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
async def resume_session(
    user_answer: str,
    thread_id: str,
    checkpoint_path: Optional[str] = None,
) -> dict:
    """
    Async entry point for resuming a Decision Maker session after the interrupt.

    Opens AsyncSqliteSaver.from_conn_string(path) — SAME path as start_session — to reload
    the checkpointed state keyed by thread_id. Inside the context: rebuilds the graph (stateless
    factory), injects user_answer via await graph.aupdate_state(), then continues execution
    via await graph.ainvoke(None, config). The graph runs through Node 3.5, Node 4, Node 5
    (with possible rewrite loop), and Node 6 before reaching END.

    Returns a dict with:
    - status: "done" (always on successful completion)
    - final_answer: the Markdown answer from Node 6
    - thread_id: echoed back for correlation
    """

    # START_BLOCK_RESOLVE_PATH: [Determine checkpoint path]
    path = str(checkpoint_path) if checkpoint_path else _DEFAULT_CHECKPOINT_PATH

    logger.info(
        f"[Flow][IMP:6][resume_session][BLOCK_RESOLVE_PATH][Configure] "
        f"Resuming session. thread_id={thread_id!r} checkpoint_path={path} [START]"
    )
    # END_BLOCK_RESOLVE_PATH

    # START_BLOCK_OPEN_CHECKPOINTER: [Open AsyncSqliteSaver to reload checkpointed state]
    async with AsyncSqliteSaver.from_conn_string(path) as cp:
        graph = build_graph(cp)
        config = {"configurable": {"thread_id": thread_id}}

        # START_BLOCK_INJECT_ANSWER: [Inject user_answer into checkpointed state]
        await graph.aupdate_state(config, {"user_answer": user_answer})
        # END_BLOCK_INJECT_ANSWER

        # START_BLOCK_INVOKE: [Continue graph from interrupted point]
        await graph.ainvoke(None, config)
        # END_BLOCK_INVOKE

        # START_BLOCK_READ_STATE: [Read terminal state for final_answer]
        snapshot = await graph.aget_state(config)
        final_answer = snapshot.values.get("final_answer") or ""

        logger.info(
            f"[BeliefState][IMP:9][resume_session][BLOCK_READ_STATE][BusinessLogic] "
            f"Session completed. thread_id={thread_id!r} "
            f"final_answer_length={len(final_answer)} [VALUE]"
        )
        # END_BLOCK_READ_STATE
    # END_BLOCK_OPEN_CHECKPOINTER

    return {
        "status": "done",
        "final_answer": final_answer,
        "thread_id": thread_id,
    }
# END_FUNCTION_resume_session


# START_FUNCTION_stream_session
# START_CONTRACT:
# PURPOSE: Async generator — streaming parallel public API for the UI layer.
#          Opens AsyncSqliteSaver per-call, builds graph via DI, seeds initial state,
#          iterates graph.astream(initial_state, config, stream_mode="updates"),
#          yields one raw chunk dict per node completion.
#          After iteration completes (interrupt after 3_Weight_Questioner), reads
#          aget_state and yields a final sentinel dict with kind="awaiting_user".
#          Never raises on interrupt — interrupt is the expected terminal condition.
# INPUTS:
# - Natural language decision problem from user => user_input: str
# - Unique thread identifier for this session => thread_id: str
# - Optional checkpoint path override (default: brainstorm/checkpoints.sqlite) => checkpoint_path: str | None
# OUTPUTS:
# - AsyncIterator[dict]: each item is either:
#     {node_id: state_delta} (raw chunk from astream) — intermediate node events
#     {"__awaiting_user__": True, "last_question": str, "thread_id": str} — final sentinel
# SIDE_EFFECTS: Writes async checkpoint to SQLite; emits LDD log at IMP:6, IMP:9.
# KEYWORDS: [PATTERN(10): AsyncGenerator; CONCEPT(10): StreamModeUpdates; TECH(9): AsyncSqliteSaver;
#            PATTERN(8): ParallelPublicAPI; CONCEPT(8): HumanInTheLoop; CONCEPT(10): AsyncIO]
# LINKS: [CALLS_FUNCTION(9): build_graph; USES_API(9): AsyncSqliteSaver.from_conn_string;
#         USES_API(10): graph.astream; USES_API(8): graph.aget_state;
#         CALLED_BY(10): ui_controllers_orchestrate_start_FUNC]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
async def stream_session(
    user_input: str,
    thread_id: str,
    checkpoint_path: Optional[str] = None,
):
    """
    Async generator providing a streaming parallel public API for the Decision Maker UI layer.

    Opens AsyncSqliteSaver.from_conn_string(path) as an async context manager (per-call
    Concept A pattern — identical to start_session). Inside the context: builds the graph
    via DI, seeds initial state, then iterates graph.astream() with stream_mode="updates".

    Each iteration chunk from astream with stream_mode="updates" is a single-key dict
    of the form {node_id: state_delta}. This raw chunk is yielded directly to the caller
    (the UI controller), which is responsible for mapping chunk keys to UIEvent structures.

    After the astream loop exits (the graph reaches the interrupt_after point after
    3_Weight_Questioner), reads the current checkpoint state via aget_state to extract
    last_question. Then yields a final sentinel dict:
        {"__awaiting_user__": True, "last_question": last_question, "thread_id": thread_id}

    The caller must treat any dict containing "__awaiting_user__": True as the end-of-stream
    signal for the first leg (UC1_START_AND_AWAIT).

    The AsyncSqliteSaver context is closed on generator return — the DB persists for
    stream_resume_session.
    """

    # START_BLOCK_RESOLVE_PATH: [Determine checkpoint path — same pattern as start_session]
    path = str(checkpoint_path) if checkpoint_path else _DEFAULT_CHECKPOINT_PATH

    logger.info(
        f"[UIEvent][IMP:6][stream_session][BLOCK_RESOLVE_PATH][Configure] "
        f"Starting streaming session. thread_id={thread_id!r} checkpoint_path={path} [START]"
    )
    # END_BLOCK_RESOLVE_PATH

    # START_BLOCK_OPEN_CHECKPOINTER: [Open AsyncSqliteSaver and stream astream updates]
    async with AsyncSqliteSaver.from_conn_string(path) as cp:
        graph = build_graph(cp)
        config = {"configurable": {"thread_id": thread_id}}

        # START_BLOCK_SEED_STATE: [Build initial state for first leg]
        initial_state = {
            "user_input": user_input,
            "tool_facts": [],
            "rewrite_count": 0,
        }
        # END_BLOCK_SEED_STATE

        # START_BLOCK_STREAM_LOOP: [Iterate astream chunks — stream_mode="updates"]
        logger.info(
            f"[UIEvent][IMP:7][stream_session][BLOCK_STREAM_LOOP][IO] "
            f"Starting astream iteration. thread_id={thread_id!r} stream_mode=updates [PENDING]"
        )
        async for chunk in graph.astream(initial_state, config, stream_mode="updates"):
            yield chunk
        # END_BLOCK_STREAM_LOOP

        # START_BLOCK_AWAITING_USER: [Read final state and emit awaiting_user sentinel]
        snapshot = await graph.aget_state(config)
        last_question = snapshot.values.get("last_question") or ""

        logger.info(
            f"[BeliefState][IMP:9][stream_session][BLOCK_AWAITING_USER][BusinessLogic] "
            f"Stream leg-1 complete. thread_id={thread_id!r} "
            f"last_question={last_question!r} Emitting awaiting_user sentinel. [SUCCESS]"
        )

        yield {
            "__awaiting_user__": True,
            "last_question": last_question,
            "thread_id": thread_id,
        }
        # END_BLOCK_AWAITING_USER
    # END_BLOCK_OPEN_CHECKPOINTER
# END_FUNCTION_stream_session


# START_FUNCTION_stream_resume_session
# START_CONTRACT:
# PURPOSE: Async generator — streaming parallel public API for the UI layer, leg 2 (resume).
#          Opens AsyncSqliteSaver per-call (same path as stream_session), rebuilds graph,
#          injects user_answer via aupdate_state, iterates graph.astream(None, config,
#          stream_mode="updates"), yields one raw chunk dict per node completion.
#          After iteration completes (graph reaches END), reads aget_state and yields a
#          final sentinel dict with kind="done" + final_answer text.
# INPUTS:
# - Human-in-the-loop reply to the calibration question => user_answer: str
# - Thread identifier matching the interrupted session => thread_id: str
# - Optional checkpoint path override => checkpoint_path: str | None
# OUTPUTS:
# - AsyncIterator[dict]: each item is either:
#     {node_id: state_delta} (raw chunk from astream) — intermediate node events
#     {"__done__": True, "final_answer": str, "thread_id": str} — final sentinel
# SIDE_EFFECTS: Updates async checkpoint in SQLite; emits LDD log at IMP:6, IMP:9.
# KEYWORDS: [PATTERN(10): AsyncGenerator; CONCEPT(10): StreamModeUpdates; TECH(9): AsyncSqliteSaver;
#            PATTERN(8): ParallelPublicAPI; CONCEPT(8): HumanInTheLoop; CONCEPT(10): AsyncIO]
# LINKS: [CALLS_FUNCTION(9): build_graph; USES_API(9): AsyncSqliteSaver.from_conn_string;
#         USES_API(8): graph.aupdate_state; USES_API(10): graph.astream; USES_API(8): graph.aget_state;
#         CALLED_BY(10): ui_controllers_orchestrate_resume_FUNC]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
async def stream_resume_session(
    user_answer: str,
    thread_id: str,
    checkpoint_path: Optional[str] = None,
):
    """
    Async generator providing a streaming parallel public API for Decision Maker UI layer, leg 2.

    Opens AsyncSqliteSaver.from_conn_string(path) — the SAME path as stream_session — to
    reload the checkpointed state keyed by thread_id (Concept A per-call pattern, identical
    to resume_session). Inside the context: rebuilds the graph (stateless factory), injects
    user_answer via await graph.aupdate_state(), then iterates graph.astream(None, config,
    stream_mode="updates").

    Each iteration chunk is a single-key dict {node_id: state_delta}, yielded directly to
    the caller. The caller (orchestrate_resume in controllers.py) is responsible for
    inspecting state_delta for CoVe-rewrite conditions and emitting the appropriate UIEvents.

    After the astream loop exits (graph reaches END after 6_Final_Synthesizer), reads
    the terminal checkpoint state via aget_state to extract final_answer. Then yields a
    final sentinel dict:
        {"__done__": True, "final_answer": final_answer, "thread_id": thread_id}

    The caller must treat any dict containing "__done__": True as the end-of-stream signal
    for the second leg (UC2_RESUME_TO_FINAL / UC3_COVE_REWRITE_VISIBLE).

    The AsyncSqliteSaver context is closed on generator return.
    """

    # START_BLOCK_RESOLVE_PATH: [Determine checkpoint path — same pattern as resume_session]
    path = str(checkpoint_path) if checkpoint_path else _DEFAULT_CHECKPOINT_PATH

    logger.info(
        f"[UIEvent][IMP:6][stream_resume_session][BLOCK_RESOLVE_PATH][Configure] "
        f"Starting streaming resume. thread_id={thread_id!r} checkpoint_path={path} [START]"
    )
    # END_BLOCK_RESOLVE_PATH

    # START_BLOCK_OPEN_CHECKPOINTER: [Open AsyncSqliteSaver, inject answer, stream astream updates]
    async with AsyncSqliteSaver.from_conn_string(path) as cp:
        graph = build_graph(cp)
        config = {"configurable": {"thread_id": thread_id}}

        # START_BLOCK_INJECT_ANSWER: [Inject user_answer into checkpointed state]
        await graph.aupdate_state(config, {"user_answer": user_answer})
        logger.info(
            f"[UIEvent][IMP:7][stream_resume_session][BLOCK_INJECT_ANSWER][IO] "
            f"user_answer injected. thread_id={thread_id!r} [SUCCESS]"
        )
        # END_BLOCK_INJECT_ANSWER

        # START_BLOCK_STREAM_LOOP: [Iterate astream chunks — stream_mode="updates"]
        async for chunk in graph.astream(None, config, stream_mode="updates"):
            yield chunk
        # END_BLOCK_STREAM_LOOP

        # START_BLOCK_FINAL_ANSWER: [Read terminal state and emit done sentinel]
        snapshot = await graph.aget_state(config)
        final_answer = snapshot.values.get("final_answer") or ""

        logger.info(
            f"[BeliefState][IMP:9][stream_resume_session][BLOCK_FINAL_ANSWER][BusinessLogic] "
            f"Stream leg-2 complete. thread_id={thread_id!r} "
            f"final_answer_length={len(final_answer)} Emitting done sentinel. [SUCCESS]"
        )

        yield {
            "__done__": True,
            "final_answer": final_answer,
            "thread_id": thread_id,
        }
        # END_BLOCK_FINAL_ANSWER
    # END_BLOCK_OPEN_CHECKPOINTER
# END_FUNCTION_stream_resume_session
