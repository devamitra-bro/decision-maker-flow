# FILE: src/ui/app.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Gradio composition root — the only layer that imports gradio. Exposes build_ui()
#          factory returning a gr.Blocks instance. Wires two-column Agentic UX layout,
#          defines on_submit and on_new_session handlers, connects components to event callbacks.
# SCOPE: build_ui() — gr.Blocks factory; on_submit async generator handler (dispatches to
#        orchestrate_start OR orchestrate_resume); on_new_session reset handler.
# INPUT: None (build_ui takes no arguments).
# OUTPUT: gr.Blocks instance ready for .launch().
# KEYWORDS: [DOMAIN(10): AgenticUX; TECH(10): Gradio5; PATTERN(9): ControllerPresenter;
#            PATTERN(10): AsyncGenerator; CONCEPT(9): HumanInTheLoop; CONCEPT(8): SessionState]
# LINKS: [CALLS_FUNCTION(10): orchestrate_start; CALLS_FUNCTION(10): orchestrate_resume;
#         CALLS_FUNCTION(10): render; USES_API(10): gradio.Blocks;
#         USES_API(9): gradio.Chatbot; USES_API(8): gradio.State]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md §1 (ui_app_py), §2 (Flow A, Flow B);
#                         P1 (gr.Chatbot type="messages"), N3 (gradio ONLY here), N4 (no launch in tests)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - build_ui() ALWAYS returns a gr.Blocks instance (never None, never raises on import).
# - on_submit NEVER calls build_ui() or demo.launch() inside tests (N4 constraint).
# - chat_history gr.State always uses Gradio 5 messages format: list[dict] with role/content.
# - on_new_session ALWAYS generates a new uuid4 thread_id and resets all state components.
# - The mode gr.State toggles between "awaiting_submit" and "awaiting_user_answer" ONLY.
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why does on_submit dispatch based on mode state instead of having two separate buttons?
# A: UC1/UC2 share the same text input and submit button. Mode state tracks whether the
#    user is submitting a NEW dilemma (orchestrate_start) or answering the calibration
#    question (orchestrate_resume). This minimises UI surface while maintaining HITL continuity.
# Q: Why is thread_id generated in on_new_session (not on_submit)?
# A: Thread ID must be stable across start → resume leg. on_submit receives the thread_id
#    from gr.State — it does NOT generate a new one. Only on_new_session resets thread_id,
#    ensuring the same SQLite checkpoint is used for both legs of a session.
# Q: Why use gr.Chatbot(type="messages") explicitly?
# A: Gradio 5 uses the "messages" format by default but the explicit parameter makes the
#    contract clear for future agents reading this file (P1 constraint).
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.1 - Bug fix: chat_history is now read directly from the chatbot component
#              instead of a shadow gr.State. Previously state_chat_history was wired to
#              inputs but never to outputs, so on HITL-resume the handler received an empty
#              history list and the visible conversation was wiped on the next yield.
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial creation; build_ui(), on_submit async generator,
#              on_new_session reset handler; two-column Blocks layout.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 10 [gr.Blocks factory — wires two-column Agentic UX with handlers] => build_ui
# FUNC 10 [Async generator handler — dispatches to orchestrate_start or orchestrate_resume] => on_submit
# FUNC 8 [Sync reset handler — generates new thread_id, clears all state] => on_new_session
# END_MODULE_MAP
#
# START_USE_CASES:
# - [build_ui]: Operator -> LaunchGradioServer -> AgenticUXRenderedInBrowser
# - [on_submit]: User -> SubmitDilemmaOrAnswer -> StreamingUIEventsRendered
# - [on_new_session]: User -> ClickNewSession -> FreshThreadIdGenerated
# END_USE_CASES

import uuid

import gradio as gr

from src.core.logger import setup_ldd_logger
from src.ui.controllers import orchestrate_resume, orchestrate_start
from src.ui.presenter import render

logger = setup_ldd_logger()


# START_FUNCTION_on_submit
# START_CONTRACT:
# PURPOSE: Async generator handler invoked by Gradio on button click. Dispatches to
#          orchestrate_start (mode=="awaiting_submit") or orchestrate_resume
#          (mode=="awaiting_user_answer"). For each UIEvent: runs render(), yields a
#          6-element Gradio update tuple.
# INPUTS:
# - User text input (dilemma or answer) => user_input: str
# - Thread identifier from gr.State => thread_id: str
# - Current mode from gr.State => mode: str ("awaiting_submit" | "awaiting_user_answer")
# - Current chat history from gr.State => chat_history: list[dict]
# - Current state snapshot from gr.State => state_snapshot: dict
# OUTPUTS:
# - AsyncIterator[tuple]: each yield is (chat_history, state_json, status_md, mode, thread_id, textbox_val)
# SIDE_EFFECTS: Emits LDD logs at IMP:6, IMP:9.
# KEYWORDS: [PATTERN(10): AsyncGenerator; CONCEPT(9): HumanInTheLoop; TECH(8): GradioMessages]
# LINKS: [CALLS_FUNCTION(10): orchestrate_start; CALLS_FUNCTION(10): orchestrate_resume;
#         CALLS_FUNCTION(10): render]
# COMPLEXITY_SCORE: 8
# END_CONTRACT
async def on_submit(
    user_input: str,
    thread_id: str,
    mode: str,
    chat_history: list,
    state_snapshot: dict,
):
    """
    Async generator handler wired to the Gradio submit button.

    Receives the user's text input, the current session thread_id, the current mode,
    and the current UI state. Dispatches to the appropriate orchestration function
    based on mode:
    - mode == "awaiting_submit" → orchestrate_start (new session, leg 1)
    - mode == "awaiting_user_answer" → orchestrate_resume (resume after HITL, leg 2)

    For each UIEvent emitted by the controller:
    1. Appends the user's input as a user turn to chat_history (FIRST yield only, for the
       initial dilemma submission).
    2. Calls render(event, current_chat, current_snapshot) to get the updated tuple.
    3. Determines the new_mode from the event kind:
       - awaiting_user → "awaiting_user_answer"
       - final_answer → "awaiting_submit" (session complete, next submit is a new dilemma)
       - otherwise → unchanged
    4. Yields a 6-element tuple: (chat_history, state_snapshot_dict, status_md, new_mode,
       thread_id, "") where "" clears the textbox.

    Thread_id is never regenerated here — it persists from gr.State throughout the session.
    """

    logger.info(
        f"[UIEvent][IMP:6][on_submit][BLOCK_INIT][Configure] "
        f"on_submit called. mode={mode!r} thread_id={thread_id!r} "
        f"user_input_len={len(user_input)} [START]"
    )

    # START_BLOCK_APPEND_USER_TURN: [Append user message to chat for first submission]
    if user_input.strip():
        chat_history = list(chat_history) + [
            {"role": "user", "content": user_input}
        ]
    # END_BLOCK_APPEND_USER_TURN

    current_chat = chat_history
    current_snapshot = dict(state_snapshot) if state_snapshot else {}
    current_mode = mode

    # START_BLOCK_DISPATCH_AND_STREAM: [Select orchestration function and stream events]
    try:
        if mode == "awaiting_submit":
            generator = orchestrate_start(user_input, thread_id)
        else:
            generator = orchestrate_resume(user_input, thread_id)

        async for event in generator:
            kind = event.get("kind", "")

            new_chat, new_snapshot, status_md = render(event, current_chat, current_snapshot)

            # Determine new_mode from event kind
            if kind == "awaiting_user":
                new_mode = "awaiting_user_answer"
            elif kind == "final_answer":
                new_mode = "awaiting_submit"
            else:
                new_mode = current_mode

            logger.info(
                f"[UIEvent][IMP:7][on_submit][BLOCK_DISPATCH_AND_STREAM][IO] "
                f"Event processed. kind={kind!r} new_mode={new_mode!r} "
                f"thread_id={thread_id!r} [YIELD]"
            )

            current_chat = new_chat
            current_snapshot = new_snapshot
            current_mode = new_mode

            yield (
                new_chat,
                new_snapshot,
                status_md,
                new_mode,
                thread_id,
                "",
            )

    except Exception as exc:
        logger.error(
            f"[UIEvent][IMP:10][on_submit][BLOCK_DISPATCH_AND_STREAM][ExceptionEnrichment] "
            f"on_submit failed. thread_id={thread_id!r} error={exc} [FATAL]"
        )
        error_chat = list(current_chat) + [
            {"role": "assistant", "content": f"❌ Ошибка обработки: {exc}"}
        ]
        yield (error_chat, current_snapshot, "❌ Критическая ошибка", current_mode, thread_id, "")
    # END_BLOCK_DISPATCH_AND_STREAM

    logger.info(
        f"[BeliefState][IMP:9][on_submit][BLOCK_DISPATCH_AND_STREAM][BusinessLogic] "
        f"on_submit generator complete. thread_id={thread_id!r} final_mode={current_mode!r} [SUCCESS]"
    )
# END_FUNCTION_on_submit


# START_FUNCTION_on_new_session
# START_CONTRACT:
# PURPOSE: Sync reset handler — generates a fresh uuid4 thread_id and resets all UI state
#          components to their initial empty/default values. Bound to the "Новая сессия" button.
# INPUTS: None (no inputs from Gradio components needed — purely generative)
# OUTPUTS:
# - tuple[str, list, dict, str, str] —
#   (new_thread_id, [], {}, "awaiting_submit", "Готов принять дилемму.")
# SIDE_EFFECTS: Emits LDD log at IMP:6.
# KEYWORDS: [PATTERN(7): Reset; CONCEPT(8): SessionState; TECH(6): UUID]
# COMPLEXITY_SCORE: 2
# END_CONTRACT
def on_new_session():
    """
    Sync handler that resets the Gradio session to a clean initial state.

    Generates a new UUID4 thread_id to ensure the next start_session/stream_session
    call creates a completely isolated checkpoint in SQLite. Clears chat_history, resets
    state_snapshot to {}, mode to "awaiting_submit", and status to the ready string.

    This handler is bound to the gr.Button "Новая сессия" in build_ui().
    """

    # START_BLOCK_RESET: [Generate fresh thread_id and reset all state]
    new_thread_id = str(uuid.uuid4())

    logger.info(
        f"[UIEvent][IMP:6][on_new_session][BLOCK_RESET][Configure] "
        f"New session created. new_thread_id={new_thread_id!r} [SUCCESS]"
    )

    return (
        new_thread_id,
        [],
        {},
        "awaiting_submit",
        "Готов принять дилемму.",
    )
    # END_BLOCK_RESET
# END_FUNCTION_on_new_session


# START_FUNCTION_build_ui
# START_CONTRACT:
# PURPOSE: Gradio Blocks factory — assembles the two-column Agentic UX layout and wires
#          all component event callbacks. Returns a gr.Blocks instance without launching it
#          (launch is delegated to the entry-point script or .launch() call by caller).
# INPUTS: None
# OUTPUTS:
# - gr.Blocks — configured Blocks instance with all components and event wiring
# SIDE_EFFECTS: None (does NOT call .launch()). Emits LDD log at IMP:9.
# KEYWORDS: [TECH(10): Gradio5; PATTERN(9): Factory; CONCEPT(8): AgenticUX; PATTERN(7): Blocks]
# LINKS: [USES_API(10): gradio.Blocks; USES_API(9): gradio.Chatbot; USES_API(9): gradio.JSON;
#         USES_API(8): gradio.State; CALLS_FUNCTION(10): on_submit; CALLS_FUNCTION(8): on_new_session]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
def build_ui() -> gr.Blocks:
    """
    Gradio Blocks factory for the Decision Maker Agentic UX.

    Assembles a two-column layout:
    - Left column: gr.Chatbot(type="messages"), gr.Textbox for dilemma/answer input,
      gr.Button "Отправить", gr.Markdown for status line, gr.Button "Новая сессия".
    - Right column: gr.JSON(label="State X-Ray") showing the filtered DecisionMakerState.

    Hidden gr.State components manage session state across yields:
    - thread_id: str — UUID4, reset by on_new_session
    - mode: str — "awaiting_submit" or "awaiting_user_answer"
    - chat_history: list[dict] — Gradio 5 messages format
    - state_snapshot: dict — accumulated State X-Ray values

    The on_submit handler is wired to both the button click and Enter key in the textbox.
    Returns gr.Blocks without calling .launch() — the operator script calls that.
    """

    # START_BLOCK_BUILD_COMPONENTS: [Assemble Gradio components and state]
    initial_thread_id = str(uuid.uuid4())

    with gr.Blocks(title="Decision Maker — Agentic UX") as demo:
        # START_BLOCK_STATE: [Hidden session state components]
        # BUG_FIX_CONTEXT: Removed state_chat_history gr.State. In Gradio 5 messages-mode
        # the gr.Chatbot component itself holds list[dict] — using a parallel gr.State that
        # was in inputs but not in outputs wiped visible history on HITL-resume submit.
        state_thread_id = gr.State(value=initial_thread_id)
        state_mode = gr.State(value="awaiting_submit")
        state_state_snapshot = gr.State(value={})
        # END_BLOCK_STATE

        gr.Markdown("# Decision Maker — Агентный анализ дилемм")
        gr.Markdown(
            "_Опишите вашу дилемму. Система проведёт мультиагентный анализ "
            "с поиском данных, взвешиванием критериев и самокритикой (CoVe)._"
        )

        # START_BLOCK_LAYOUT: [Two-column layout]
        with gr.Row():
            # Left column — Chat and Controls
            with gr.Column(scale=2):
                chatbot = gr.Chatbot(
                    type="messages",
                    label="Диалог",
                    height=500,
                    show_label=True,
                    show_copy_button=True,
                )
                textbox = gr.Textbox(
                    placeholder="Опиши дилемму (например: купить квартиру или арендовать?)...",
                    label="Ваш ввод",
                    lines=3,
                    show_label=True,
                )
                with gr.Row():
                    btn_submit = gr.Button("Отправить", variant="primary")
                    btn_new_session = gr.Button("Новая сессия", variant="secondary")
                status_md = gr.Markdown("Готов принять дилемму.")

            # Right column — State X-Ray
            with gr.Column(scale=1):
                state_xray = gr.JSON(
                    label="State X-Ray (внутреннее состояние агентов)",
                    value={},
                )
        # END_BLOCK_LAYOUT

        # START_BLOCK_WIRE_EVENTS: [Connect handlers to Gradio components]

        # on_submit output contract: (chatbot, state_xray, status_md, mode, thread_id, textbox)
        submit_outputs = [
            chatbot,
            state_xray,
            status_md,
            state_mode,
            state_thread_id,
            textbox,
        ]

        btn_submit.click(
            fn=on_submit,
            inputs=[textbox, state_thread_id, state_mode, chatbot, state_state_snapshot],
            outputs=submit_outputs,
        )

        textbox.submit(
            fn=on_submit,
            inputs=[textbox, state_thread_id, state_mode, chatbot, state_state_snapshot],
            outputs=submit_outputs,
        )

        # on_new_session output contract: (thread_id, chat_history, state_snapshot, mode, status_md)
        btn_new_session.click(
            fn=on_new_session,
            inputs=[],
            outputs=[state_thread_id, chatbot, state_xray, state_mode, status_md],
        )
        # END_BLOCK_WIRE_EVENTS

    logger.info(
        f"[BeliefState][IMP:9][build_ui][BLOCK_BUILD_COMPONENTS][BusinessLogic] "
        f"Gradio Blocks assembled. initial_thread_id={initial_thread_id!r} "
        f"components=chatbot,textbox,state_xray,status_md,state_thread_id [SUCCESS]"
    )

    return demo
    # END_BLOCK_BUILD_COMPONENTS
# END_FUNCTION_build_ui
