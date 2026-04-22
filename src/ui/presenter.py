# FILE: src/ui/presenter.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Pure renderer layer — converts a UIEvent dict into a Gradio-renderable output
#          tuple (new_chat_history, new_state_snapshot, status_text). This is a pure
#          function with no I/O, no async, and no gradio import — fully headless-testable.
# SCOPE: render() — main dispatch function mapping all 9 UIEvent kinds to UI updates.
#        _filter_state_for_display() — drops verbose fields, truncates long values.
#        _format_status() — maps (event_kind, node_name) to emoji-prefixed Russian strings.
# INPUT: UIEvent dict, current chat_history list[dict], current state_snapshot dict.
# OUTPUT: tuple[list[dict], dict, str] — (new_chat_history, new_state_snapshot, status_text).
# KEYWORDS: [DOMAIN(9): AgenticUX; PATTERN(9): ControllerPresenter; CONCEPT(8): PureFunction;
#            PATTERN(8): HeadlessTestable; TECH(8): GradioMessages; CONCEPT(7): ZeroContextSurvival]
# LINKS: [CALLED_BY(10): ui_app_on_submit_FUNC; CALLED_BY(9): tests_ui_test_presenter_py]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md §1 (ui_presenter_py), §2 (presenter.render);
#                         P1 (messages format), P3 (UIEvent kinds), N3 (no gradio import)
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - render() ALWAYS returns a tuple of exactly (list, dict, str).
# - chat_history uses Gradio 5 messages format: list of {"role": "user"|"assistant", "content": str}.
# - render() NEVER mutates the input chat_history or state_snapshot — it creates new objects.
# - Unknown UIEvent kinds return the input unchanged (defensive fallback).
# - _filter_state_for_display() ALWAYS returns a dict (never None, never raises).
# - This module NEVER imports gradio (N3 hard constraint).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why is render() a pure function rather than a class method with state?
# A: Pure functions are trivially testable (table-driven input → output), have no side
#    effects, and can be called from any context (sync tests, async handlers, jupyter).
#    State is owned entirely by the Gradio gr.State components in app.py.
# Q: Why does presenter NOT import gradio?
# A: Architectural boundary (N3). presenter.py is in the headless-testable seam.
#    Importing gradio would couple every test to a live gradio import, increasing test
#    cold-start time and creating a dependency on gradio's side effects at module load.
# Q: Why truncate state fields to 500 chars?
# A: The JSON panel has finite width. Long strings (tool_facts, final_answer excerpts)
#    degrade readability. The 500 char limit is a conservative UI budget.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; render(), _filter_state_for_display(),
#              _format_status() pure functions; covers all 9 UIEvent kinds.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 10 [Pure dispatch — maps UIEvent to (chat_history, state_snapshot, status_text)] => render
# FUNC 7 [Pure helper — filters and truncates state dict for X-Ray display] => _filter_state_for_display
# FUNC 7 [Pure helper — maps (event_kind, node_name) to emoji-prefixed Russian status string] => _format_status
# END_MODULE_MAP
#
# START_USE_CASES:
# - [render]: GradioHandler -> RenderUIEvent -> GradioComponentTupleReturned
# END_USE_CASES

from typing import Optional

# START_BLOCK_NODE_NAME_MAP: [Human-readable Russian names for graph node IDs]
_NODE_DISPLAY_NAMES = {
    "1_Context_Analyzer": "Анализ контекста",
    "2_Tool_Node": "Поиск данных",
    "3_Weight_Questioner": "Формирование вопроса",
    "3.5_Weight_Parser": "Разбор приоритетов",
    "4_Draft_Generator": "Генерация черновика",
    "5_CoVe_Critique": "Самокритика (CoVe)",
    "6_Final_Synthesizer": "Финальный синтез",
}

# Display-only field allowlist for State X-Ray (verbose fields excluded)
_STATE_DISPLAY_FIELDS = {
    "dilemma",
    "search_queries",
    "tool_facts",
    "weights",
    "rewrite_count",
    "critique_feedback",
    "decision",
    "final_answer",
    "last_question",
    "needs_rewrite",
}

_MAX_VALUE_LENGTH = 500
# END_BLOCK_NODE_NAME_MAP


# START_FUNCTION_filter_state_for_display
# START_CONTRACT:
# PURPOSE: Drop internal/verbose fields from a state_snapshot dict and truncate long
#          string values to _MAX_VALUE_LENGTH characters for X-Ray panel readability.
# INPUTS:
# - Raw state_snapshot dict from UIEvent or graph aget_state => raw_state: dict
# OUTPUTS:
# - dict — filtered and truncated state dict safe for JSON display
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(7): PureHelper; CONCEPT(6): DataFiltering]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def _filter_state_for_display(raw_state: dict) -> dict:
    """
    Pure helper that filters a state snapshot dict for display in the State X-Ray panel.

    Keeps only the human-readable subset defined in _STATE_DISPLAY_FIELDS and truncates
    any string value longer than _MAX_VALUE_LENGTH characters with a trailing ellipsis.
    Lists are kept but their string elements are truncated individually if present.
    Returns an empty dict safely if raw_state is None or empty.
    """
    if not raw_state:
        return {}

    # START_BLOCK_FILTER: [Keep allowlisted fields only, truncate long strings]
    result = {}
    for key, value in raw_state.items():
        if key not in _STATE_DISPLAY_FIELDS:
            continue
        if isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
            result[key] = value[:_MAX_VALUE_LENGTH] + "..."
        elif isinstance(value, list):
            truncated_list = []
            for item in value:
                if isinstance(item, str) and len(item) > _MAX_VALUE_LENGTH:
                    truncated_list.append(item[:_MAX_VALUE_LENGTH] + "...")
                elif isinstance(item, dict):
                    # Truncate string values inside nested dicts (e.g. tool_facts items)
                    truncated_item = {}
                    for k, v in item.items():
                        if isinstance(v, str) and len(v) > _MAX_VALUE_LENGTH:
                            truncated_item[k] = v[:_MAX_VALUE_LENGTH] + "..."
                        else:
                            truncated_item[k] = v
                    truncated_list.append(truncated_item)
                else:
                    truncated_list.append(item)
            result[key] = truncated_list
        else:
            result[key] = value
    # END_BLOCK_FILTER

    return result
# END_FUNCTION_filter_state_for_display


# START_FUNCTION_format_status
# START_CONTRACT:
# PURPOSE: Map (event_kind, optional node_name) to an emoji-prefixed Russian status string
#          per task spec §2.1. Provides human-readable progress indicator for the UI status bar.
# INPUTS:
# - UIEvent kind string => event_kind: str
# - Optional node ID from the UIEvent => node_name: Optional[str]
# - Optional rewrite count for cove_rewrite events => rewrite_count: Optional[int]
# OUTPUTS:
# - str — Russian status text with emoji prefix
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(7): PureHelper; CONCEPT(6): I18N; TECH(5): StringMapping]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def _format_status(
    event_kind: str,
    node_name: Optional[str] = None,
    rewrite_count: Optional[int] = None,
) -> str:
    """
    Pure helper that maps an event kind (and optional node name) to a Russian status string.

    Returns emoji-prefixed status strings that appear in the Gradio status Markdown component
    below the Chatbot. Node IDs are mapped to human-readable Russian names via _NODE_DISPLAY_NAMES.
    Falls back to the raw node_name if no mapping exists.
    """
    # START_BLOCK_STATUS_MAP: [Map event kind to status string]
    node_display = _NODE_DISPLAY_NAMES.get(node_name or "", node_name or "")

    status_map = {
        "session_started": "⏳ Сессия запущена, анализирую дилемму...",
        "node_started": f"⏳ Узел {node_display}: начало обработки..." if node_display else "⏳ Обработка...",
        "node_completed": f"✅ Узел {node_display}: завершено" if node_display else "✅ Узел завершён",
        "state_snapshot": f"📊 Состояние обновлено ({node_display})" if node_display else "📊 Состояние обновлено",
        "cove_rewrite": f"🚨 Критик нашёл ошибку, переписываю (попытка {rewrite_count}/2)" if rewrite_count else "🚨 Критик нашёл ошибку, переписываю...",
        "awaiting_user": "🤔 Нужен ваш ответ:",
        "resume_started": "▶ Продолжаю сессию с вашим ответом...",
        "final_answer": "✅ Готово",
        "error": "❌ Произошла ошибка",
    }
    # END_BLOCK_STATUS_MAP

    return status_map.get(event_kind, f"ℹ️ {event_kind}")
# END_FUNCTION_format_status


# START_FUNCTION_render
# START_CONTRACT:
# PURPOSE: Pure dispatch function — maps a UIEvent dict to a Gradio-renderable output tuple.
#          Dispatches on event["kind"] and mutates chat_history and state_snapshot accordingly.
#          Returns a new tuple without mutating the input arguments.
# INPUTS:
# - Normalised domain event from controllers.py => event: dict (UIEvent)
# - Current chat history list in Gradio messages format => chat_history: list[dict]
# - Current state snapshot dict for the JSON X-Ray panel => state_snapshot: dict
# OUTPUTS:
# - tuple[list[dict], dict, str] — (new_chat_history, new_state_snapshot, status_markdown)
# SIDE_EFFECTS: None.
# KEYWORDS: [PATTERN(10): PureFunction; CONCEPT(9): Dispatch; TECH(8): GradioMessages;
#            CONCEPT(8): ChainOfVerification; PATTERN(8): HeadlessTestable]
# LINKS: [READS_DATA_FROM(9): UIEvent_schema; CALLED_BY(10): ui_app_on_submit_FUNC]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
def render(
    event: dict,
    chat_history: list,
    state_snapshot: dict,
) -> tuple:
    """
    Pure function that maps a UIEvent to a Gradio component update tuple.

    Receives the current (chat_history, state_snapshot) as context and returns a new
    tuple (new_chat_history, new_state_snapshot, status_text) reflecting the event.

    Never mutates the input lists/dicts — creates shallow copies where needed.

    Dispatch logic per event kind:
    - session_started:  reset state_snapshot to {}; keep chat_history; status = "Сессия запущена..."
    - node_started:     keep chat_history; status = "⏳ Узел N: начало..."; no state change
    - node_completed:   merge state_delta into state_snapshot; status = "✅ Узел N: завершено"
    - state_snapshot:   merge state_snapshot values into X-Ray display dict (filtered)
    - cove_rewrite:     append assistant message with critique_feedback; status = "🚨 Критик..."
    - awaiting_user:    append assistant message with question; status = "🤔 Нужен ваш ответ:"
    - resume_started:   keep chat_history; status = "▶ Продолжаю..."
    - final_answer:     append assistant message with final answer; status = "✅ Готово"
    - error:            append assistant message with error; status = "❌ Произошла ошибка"
    """

    # START_BLOCK_INIT: [Copy input state to avoid mutation]
    kind = event.get("kind", "")
    new_chat = list(chat_history)
    new_snapshot = dict(state_snapshot)
    # END_BLOCK_INIT

    # START_BLOCK_DISPATCH: [Dispatch on event kind]

    if kind == "session_started":
        new_snapshot = {}
        status = _format_status("session_started")

    elif kind == "node_started":
        node_name = event.get("node", "")
        status = _format_status("node_started", node_name)

    elif kind == "node_completed":
        node_name = event.get("node", "")
        state_delta = event.get("state_delta") or {}
        # Merge delta into snapshot for State X-Ray continuity
        filtered = _filter_state_for_display(state_delta)
        new_snapshot.update(filtered)
        status = _format_status("node_completed", node_name)

    elif kind == "state_snapshot":
        node_name = event.get("node", "")
        raw_snapshot = event.get("state_snapshot") or {}
        filtered = _filter_state_for_display(raw_snapshot)
        new_snapshot.update(filtered)
        status = _format_status("state_snapshot", node_name)

    elif kind == "cove_rewrite":
        critique_feedback = event.get("critique_feedback", "")
        rewrite_count = new_snapshot.get("rewrite_count")
        new_chat = new_chat + [
            {
                "role": "assistant",
                "content": f"⚠️ CoVe-критика:\n\n{critique_feedback}",
            }
        ]
        status = _format_status("cove_rewrite", rewrite_count=rewrite_count)

    elif kind == "awaiting_user":
        question = event.get("question", "")
        new_chat = new_chat + [
            {
                "role": "assistant",
                "content": question,
            }
        ]
        status = _format_status("awaiting_user")

    elif kind == "resume_started":
        status = _format_status("resume_started")

    elif kind == "final_answer":
        final_answer = event.get("final_answer", "")
        new_chat = new_chat + [
            {
                "role": "assistant",
                "content": final_answer,
            }
        ]
        status = _format_status("final_answer")

    elif kind == "error":
        error_message = event.get("error_message", "Неизвестная ошибка")
        new_chat = new_chat + [
            {
                "role": "assistant",
                "content": f"❌ Ошибка: {error_message}",
            }
        ]
        status = _format_status("error")

    else:
        # Defensive fallback — unknown event kind, pass through unchanged
        status = f"ℹ️ {kind}"

    # END_BLOCK_DISPATCH

    return new_chat, new_snapshot, status
# END_FUNCTION_render
