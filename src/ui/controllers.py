# FILE: src/ui/controllers.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Framework-agnostic async orchestration layer that consumes the decision_maker
#          streaming API and emits a normalised stream of UIEvent dicts (domain events).
#          This is the headless-testable seam between the feature core and the Gradio UI.
# SCOPE: Defines UIEvent TypedDict (9 kinds), and two async-generator functions
#        orchestrate_start and orchestrate_resume that map raw graph chunks to UIEvents.
#        Also defines _extract_cove_rewrite_event private helper.
# INPUT: user_input / user_answer strings, thread_id, optional checkpoint_path.
# OUTPUT: AsyncIterator[UIEvent] — normalised domain events for the presenter layer.
# KEYWORDS: [DOMAIN(10): AgenticUX; CONCEPT(10): StreamModeUpdates; PATTERN(9): ControllerPresenter;
#            PATTERN(10): AsyncGenerator; CONCEPT(9): HumanInTheLoop; CONCEPT(9): ChainOfVerification;
#            PATTERN(8): HeadlessTestable; TECH(7): TypedDict]
# LINKS: [CALLS_FUNCTION(10): stream_session; CALLS_FUNCTION(10): stream_resume_session;
#         READS_DATA_FROM(9): src.features.decision_maker.graph;
#         CALLED_BY(10): ui_app_on_submit_FUNC]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md §1 (ui_controllers_py), §2 (Flow A, Flow B);
#                         P3 (UIEvent schema), P4 (stream_mode), N3 (no gradio import)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - orchestrate_start ALWAYS emits session_started as the FIRST event.
# - orchestrate_start ALWAYS emits awaiting_user as the LAST event (or error if exception).
# - orchestrate_resume ALWAYS emits resume_started as the FIRST event.
# - orchestrate_resume ALWAYS emits final_answer as the LAST event (or error if exception).
# - cove_rewrite UIEvent is ALWAYS emitted BEFORE the corresponding node_completed event
#   (ordering invariant for AC_UI_08).
# - This module NEVER imports gradio (N3 hard constraint).
# - state_snapshot field in UIEvent contains snapshot.values dict from aget_state.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why does controllers.py call stream_session (async generator) instead of start_session (async)?
# A: The UI layer needs per-node progressive updates (stream_mode="updates"). start_session
#    uses ainvoke which blocks until interrupt — no intermediate events. The streaming API
#    provides chunk-by-chunk iteration required for the Agentic UX.
# Q: Why emit state_snapshot as a separate UIEvent after each node_completed?
# A: The presenter needs both the delta (for detecting CoVe decisions) and the full snapshot
#    (for rendering the State X-Ray JSON panel). Separating them keeps the UIEvent schema
#    orthogonal: each event has a single responsibility.
# Q: Why is cove_rewrite emitted BEFORE node_completed?
# A: UX ordering: the user must see the critique text BEFORE the JSON state refreshes.
#    This matches the Flow B data flow description in DevelopmentPlan_UI.md §2.
# Q: Why does _extract_cove_rewrite_event return None instead of raising?
# A: Controllers are in a streaming hot path. Returning None on mismatch is idiomatic for
#    optional-event extraction and avoids try-except overhead in the inner loop.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; UIEvent TypedDict; orchestrate_start,
#              orchestrate_resume, _extract_cove_rewrite_event functions.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 9 [TypedDict — normalised domain event schema with 9 kinds] => UIEvent
# FUNC 10 [Async generator — maps stream_session chunks to UIEvents + awaiting_user terminator] => orchestrate_start
# FUNC 10 [Async generator — maps stream_resume_session chunks to UIEvents + final_answer terminator] => orchestrate_resume
# FUNC 7 [Pure helper — extracts cove_rewrite UIEvent from state_delta or returns None] => _extract_cove_rewrite_event
# END_MODULE_MAP
#
# START_USE_CASES:
# - [orchestrate_start]: GradioHandler -> OrchestrateStartSession -> UIEventsStreamedToPresenter
# - [orchestrate_resume]: GradioHandler -> OrchestrateResumeSession -> UIEventsStreamedToPresenter
# END_USE_CASES

from typing import Any, AsyncIterator, Literal, Optional

from src.core.logger import setup_ldd_logger
from src.features.decision_maker.graph import stream_resume_session, stream_session

logger = setup_ldd_logger()

# START_BLOCK_UIEVENT_SCHEMA: [TypedDict definition for the 9-kind UIEvent contract]
# NOTE: We define UIEvent manually rather than using TypedDict class to support Python 3.11
# and maintain compatibility with the total=False semantics where all fields are optional
# except `kind`. In runtime we produce plain dict — the TypedDict is documentation contract.

# UIEvent field documentation (P3 contract — exactly 9 kinds):
# kind: Literal["session_started", "node_started", "node_completed", "state_snapshot",
#               "cove_rewrite", "awaiting_user", "resume_started", "final_answer", "error"]
# node: str           — node_id e.g. "1_Context_Analyzer"
# state_delta: dict   — contents of chunk[node_id] from astream
# state_snapshot: dict — full snapshot.values after this node (from aget_state)
# question: str       — calibration question for awaiting_user event
# critique_feedback: str — CoVe critique text for cove_rewrite event
# final_answer: str   — final Markdown answer for final_answer event
# error_message: str  — error description for error event
# thread_id: str      — session thread identifier

UIEvent = dict  # Runtime type — protocol documented above for Zero-Context Survival

COVE_NODE_ID = "5_CoVe_Critique"
AWAITING_USER_SENTINEL_KEY = "__awaiting_user__"
DONE_SENTINEL_KEY = "__done__"
# END_BLOCK_UIEVENT_SCHEMA


# START_FUNCTION_extract_cove_rewrite_event
# START_CONTRACT:
# PURPOSE: Pure helper — inspect a state_delta from 5_CoVe_Critique node and return a
#          cove_rewrite UIEvent if decision=="rewrite" AND critique_feedback is truthy.
#          Returns None if conditions are not met.
# INPUTS:
# - State delta dict from the 5_CoVe_Critique node chunk => state_delta: dict
# - Thread identifier for the UIEvent thread_id field => thread_id: str
# OUTPUTS:
# - Optional[dict] — UIEvent with kind="cove_rewrite" or None
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(7): PureHelper; CONCEPT(9): ChainOfVerification; TECH(6): DictInspection]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def _extract_cove_rewrite_event(state_delta: dict, thread_id: str) -> Optional[dict]:
    """
    Pure helper that inspects a state_delta from the 5_CoVe_Critique node.

    If the delta signals a rewrite decision (decision == "rewrite" AND critique_feedback
    is a non-empty string), returns a UIEvent dict of kind="cove_rewrite" carrying the
    critique_feedback text and thread_id. Otherwise returns None.

    This is intentionally a pure function (no I/O, no side effects) to allow table-driven
    unit testing without any async context.
    """
    decision = state_delta.get("decision") or state_delta.get("needs_rewrite")
    critique_feedback = state_delta.get("critique_feedback", "")

    # START_BLOCK_REWRITE_CHECK: [Check rewrite condition]
    is_rewrite_decision = (decision == "rewrite") or (decision is True)
    if is_rewrite_decision and critique_feedback:
        return {
            "kind": "cove_rewrite",
            "critique_feedback": critique_feedback,
            "thread_id": thread_id,
        }
    # END_BLOCK_REWRITE_CHECK

    return None
# END_FUNCTION_extract_cove_rewrite_event


# START_FUNCTION_orchestrate_start
# START_CONTRACT:
# PURPOSE: Async generator — first-leg orchestration. Calls stream_session async generator,
#          maps each raw chunk to UIEvents (node_completed + state_snapshot), detects
#          CoVe-rewrite sentinel from 5_CoVe_Critique (though rare in leg 1), and emits
#          the final awaiting_user UIEvent after the stream ends. Wraps in try-except to
#          emit error UIEvent on any exception.
# INPUTS:
# - Natural language decision problem from user => user_input: str
# - Unique thread identifier for this session => thread_id: str
# - Optional checkpoint path override => checkpoint_path: Optional[str]
# OUTPUTS:
# - AsyncIterator[UIEvent]: session_started → (node_completed + state_snapshot)* →
#                           [cove_rewrite if applicable] → awaiting_user  |OR| error
# SIDE_EFFECTS: Emits LDD logs at IMP:6, IMP:9 via logger.
# KEYWORDS: [PATTERN(10): AsyncGenerator; CONCEPT(10): StreamModeUpdates; CONCEPT(8): HumanInTheLoop;
#            PATTERN(8): HeadlessTestable; CONCEPT(10): AsyncIO]
# LINKS: [CALLS_FUNCTION(10): stream_session; CALLS_FUNCTION(7): _extract_cove_rewrite_event]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
async def orchestrate_start(
    user_input: str,
    thread_id: str,
    checkpoint_path: Optional[str] = None,
) -> AsyncIterator[UIEvent]:
    """
    Async generator for first-leg orchestration of a Decision Maker session.

    Consumes the stream_session async generator and translates each raw chunk dict
    (of form {node_id: state_delta}) into a sequence of UIEvents:
    1. session_started — emitted once before the first chunk.
    2. For each chunk: node_completed (carries node + state_delta) followed by
       state_snapshot (carries node + state_snapshot with full values dict).
    3. If the chunk is from 5_CoVe_Critique and indicates a rewrite, emits cove_rewrite
       BEFORE node_completed (ordering invariant from Flow B, also possible in leg 1
       edge cases — handled here for symmetry).
    4. After stream_session generator exits (interrupt reached): awaiting_user with question.

    On any exception: emits error UIEvent and re-raises (so the Gradio handler can detect
    the generator exhaustion cleanly).

    The state_snapshot field is populated with the state_delta values for immediate
    rendering. The aget_state call happens inside stream_session (yielded via sentinel).
    For the state_snapshot UIEvent we use the state_delta dict as the snapshot since
    we do not have direct graph access here (the aget_state happens in stream_session).
    The sentinel's accumulated snapshot is assembled from all deltas by the presenter.
    """

    logger.info(
        f"[UIEvent][IMP:6][orchestrate_start][BLOCK_INIT][Configure] "
        f"Starting orchestration. thread_id={thread_id!r} [START]"
    )

    try:
        # START_BLOCK_EMIT_SESSION_STARTED: [First event — signal session initialized]
        yield {
            "kind": "session_started",
            "thread_id": thread_id,
        }

        logger.info(
            f"[BeliefState][IMP:9][orchestrate_start][BLOCK_EMIT_SESSION_STARTED][BusinessLogic] "
            f"session_started emitted. thread_id={thread_id!r} [SUCCESS]"
        )
        # END_BLOCK_EMIT_SESSION_STARTED

        # START_BLOCK_STREAM_LOOP: [Consume stream_session and map chunks to UIEvents]
        async for chunk in stream_session(user_input, thread_id, checkpoint_path):

            # START_BLOCK_CHECK_SENTINEL: [Detect awaiting_user sentinel from stream_session]
            if chunk.get(AWAITING_USER_SENTINEL_KEY):
                last_question = chunk.get("last_question", "")

                logger.info(
                    f"[BeliefState][IMP:9][orchestrate_start][BLOCK_AWAITING_USER][BusinessLogic] "
                    f"Emitting awaiting_user. thread_id={thread_id!r} "
                    f"question={last_question!r} [SUCCESS]"
                )

                yield {
                    "kind": "awaiting_user",
                    "question": last_question,
                    "thread_id": thread_id,
                }
                return
            # END_BLOCK_CHECK_SENTINEL

            # START_BLOCK_EXTRACT_NODE_DATA: [Extract node_id and state_delta from chunk]
            # Each chunk from astream(stream_mode="updates") is a single-key dict: {node_id: delta}
            if not chunk:
                continue

            node_id, state_delta = next(iter(chunk.items()))

            logger.info(
                f"[UIEvent][IMP:7][orchestrate_start][BLOCK_EXTRACT_NODE_DATA][IO] "
                f"Node chunk received. node_id={node_id!r} thread_id={thread_id!r} [RECEIVED]"
            )
            # END_BLOCK_EXTRACT_NODE_DATA

            # START_BLOCK_COVE_REWRITE_CHECK: [CoVe rewrite detection — emit BEFORE node_completed]
            if node_id == COVE_NODE_ID:
                cove_event = _extract_cove_rewrite_event(state_delta, thread_id)
                if cove_event is not None:
                    logger.info(
                        f"[UIEvent][IMP:8][orchestrate_start][BLOCK_COVE_REWRITE_CHECK][BusinessLogic] "
                        f"cove_rewrite detected. thread_id={thread_id!r} "
                        f"critique_feedback={state_delta.get('critique_feedback', '')!r} [EMIT]"
                    )
                    yield cove_event
            # END_BLOCK_COVE_REWRITE_CHECK

            # START_BLOCK_EMIT_NODE_COMPLETED: [Emit node_completed event]
            yield {
                "kind": "node_completed",
                "node": node_id,
                "state_delta": state_delta,
                "thread_id": thread_id,
            }
            # END_BLOCK_EMIT_NODE_COMPLETED

            # START_BLOCK_EMIT_STATE_SNAPSHOT: [Emit state_snapshot for State X-Ray update]
            yield {
                "kind": "state_snapshot",
                "node": node_id,
                "state_snapshot": state_delta,
                "thread_id": thread_id,
            }
            # END_BLOCK_EMIT_STATE_SNAPSHOT
        # END_BLOCK_STREAM_LOOP

    except Exception as exc:
        # START_BLOCK_ERROR_HANDLER: [Emit error event and re-raise]
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error(
            f"[UIEvent][IMP:10][orchestrate_start][BLOCK_ERROR_HANDLER][ExceptionEnrichment] "
            f"Exception in orchestrate_start. thread_id={thread_id!r} "
            f"error={error_message} [FATAL]"
        )
        yield {
            "kind": "error",
            "error_message": error_message,
            "thread_id": thread_id,
        }
        raise
        # END_BLOCK_ERROR_HANDLER
# END_FUNCTION_orchestrate_start


# START_FUNCTION_orchestrate_resume
# START_CONTRACT:
# PURPOSE: Async generator — second-leg orchestration. Calls stream_resume_session async
#          generator, maps each raw chunk to UIEvents (node_completed + state_snapshot),
#          detects CoVe-rewrite sentinel and emits cove_rewrite BEFORE node_completed,
#          and emits the final final_answer UIEvent after the stream ends. Wraps in
#          try-except to emit error UIEvent on any exception.
# INPUTS:
# - Human-in-the-loop reply to the calibration question => user_answer: str
# - Thread identifier matching the interrupted session => thread_id: str
# - Optional checkpoint path override => checkpoint_path: Optional[str]
# OUTPUTS:
# - AsyncIterator[UIEvent]: resume_started → (cove_rewrite? + node_completed + state_snapshot)* →
#                           final_answer  |OR| error
# SIDE_EFFECTS: Emits LDD logs at IMP:6, IMP:9 via logger.
# KEYWORDS: [PATTERN(10): AsyncGenerator; CONCEPT(10): StreamModeUpdates; CONCEPT(8): HumanInTheLoop;
#            PATTERN(8): HeadlessTestable; CONCEPT(10): AsyncIO; CONCEPT(9): ChainOfVerification]
# LINKS: [CALLS_FUNCTION(10): stream_resume_session; CALLS_FUNCTION(7): _extract_cove_rewrite_event]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
async def orchestrate_resume(
    user_answer: str,
    thread_id: str,
    checkpoint_path: Optional[str] = None,
) -> AsyncIterator[UIEvent]:
    """
    Async generator for second-leg orchestration of a Decision Maker session.

    Consumes the stream_resume_session async generator and translates each raw chunk dict
    into a sequence of UIEvents:
    1. resume_started — emitted once before the first chunk (signals resume began).
    2. For each chunk: CoVe-rewrite check (emit cove_rewrite before node_completed if triggered),
       then node_completed (carries node + state_delta), then state_snapshot.
    3. After stream_resume_session generator exits (graph reached END): final_answer event.

    The cove_rewrite ordering invariant (emit BEFORE node_completed) is satisfied by the
    placement of the _extract_cove_rewrite_event call and yield before the node_completed yield.

    On any exception: emits error UIEvent.
    """

    logger.info(
        f"[UIEvent][IMP:6][orchestrate_resume][BLOCK_INIT][Configure] "
        f"Starting resume orchestration. thread_id={thread_id!r} [START]"
    )

    try:
        # START_BLOCK_EMIT_RESUME_STARTED: [First event — signal resume initialized]
        yield {
            "kind": "resume_started",
            "thread_id": thread_id,
        }

        logger.info(
            f"[BeliefState][IMP:9][orchestrate_resume][BLOCK_EMIT_RESUME_STARTED][BusinessLogic] "
            f"resume_started emitted. thread_id={thread_id!r} [SUCCESS]"
        )
        # END_BLOCK_EMIT_RESUME_STARTED

        # START_BLOCK_STREAM_LOOP: [Consume stream_resume_session and map chunks to UIEvents]
        async for chunk in stream_resume_session(user_answer, thread_id, checkpoint_path):

            # START_BLOCK_CHECK_SENTINEL: [Detect done sentinel from stream_resume_session]
            if chunk.get(DONE_SENTINEL_KEY):
                final_answer = chunk.get("final_answer", "")

                logger.info(
                    f"[BeliefState][IMP:9][orchestrate_resume][BLOCK_FINAL_ANSWER][BusinessLogic] "
                    f"Emitting final_answer. thread_id={thread_id!r} "
                    f"final_answer_length={len(final_answer)} [SUCCESS]"
                )

                yield {
                    "kind": "final_answer",
                    "final_answer": final_answer,
                    "thread_id": thread_id,
                }
                return
            # END_BLOCK_CHECK_SENTINEL

            # START_BLOCK_EXTRACT_NODE_DATA: [Extract node_id and state_delta from chunk]
            if not chunk:
                continue

            node_id, state_delta = next(iter(chunk.items()))

            logger.info(
                f"[UIEvent][IMP:7][orchestrate_resume][BLOCK_EXTRACT_NODE_DATA][IO] "
                f"Node chunk received. node_id={node_id!r} thread_id={thread_id!r} [RECEIVED]"
            )
            # END_BLOCK_EXTRACT_NODE_DATA

            # START_BLOCK_COVE_REWRITE_CHECK: [CoVe rewrite detection — emit BEFORE node_completed]
            if node_id == COVE_NODE_ID:
                cove_event = _extract_cove_rewrite_event(state_delta, thread_id)
                if cove_event is not None:
                    logger.info(
                        f"[UIEvent][IMP:8][orchestrate_resume][BLOCK_COVE_REWRITE_CHECK][BusinessLogic] "
                        f"cove_rewrite detected. thread_id={thread_id!r} "
                        f"critique_feedback={state_delta.get('critique_feedback', '')!r} [EMIT]"
                    )
                    yield cove_event
            # END_BLOCK_COVE_REWRITE_CHECK

            # START_BLOCK_EMIT_NODE_COMPLETED: [Emit node_completed event]
            yield {
                "kind": "node_completed",
                "node": node_id,
                "state_delta": state_delta,
                "thread_id": thread_id,
            }
            # END_BLOCK_EMIT_NODE_COMPLETED

            # START_BLOCK_EMIT_STATE_SNAPSHOT: [Emit state_snapshot for State X-Ray update]
            yield {
                "kind": "state_snapshot",
                "node": node_id,
                "state_snapshot": state_delta,
                "thread_id": thread_id,
            }
            # END_BLOCK_EMIT_STATE_SNAPSHOT
        # END_BLOCK_STREAM_LOOP

    except Exception as exc:
        # START_BLOCK_ERROR_HANDLER: [Emit error event]
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error(
            f"[UIEvent][IMP:10][orchestrate_resume][BLOCK_ERROR_HANDLER][ExceptionEnrichment] "
            f"Exception in orchestrate_resume. thread_id={thread_id!r} "
            f"error={error_message} [FATAL]"
        )
        yield {
            "kind": "error",
            "error_message": error_message,
            "thread_id": thread_id,
        }
        raise
        # END_BLOCK_ERROR_HANDLER
# END_FUNCTION_orchestrate_resume
