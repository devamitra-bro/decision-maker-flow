# FILE: tests/ui/test_ui_handlers.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: UI Headless Tests — verify on_submit handler behaviour WITHOUT launching Gradio.
#          Uses monkeypatch to replace orchestrate_start with a fake async generator yielding
#          scripted UIEvents. Asserts: (a) handler is async generator; (b) yields correct
#          tuple shapes; (c) transitions mode correctly; (d) preserves thread_id (AC_UI_17);
#          (e) IMP:7-10 logs non-empty (Anti-Illusion). No Gradio server started.
# SCOPE: AC_UI_09, AC_UI_11, AC_UI_17 coverage; headless on_submit testing via monkeypatch.
# INPUT: Scripted UIEvent stubs injected via monkeypatch.
# OUTPUT: Assertions on collected yield tuples from on_submit async generator.
# KEYWORDS: [DOMAIN(8): TestInfra; CONCEPT(8): HeadlessUI; PATTERN(9): Monkeypatch;
#            CONCEPT(9): AsyncGenerator; CONCEPT(8): SessionState; CONCEPT(10): AsyncIO]
# LINKS: [USES_API(10): src.ui.app.on_submit; USES_API(8): src.ui.controllers.orchestrate_start]
# LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md AC_UI_09, AC_UI_11, AC_UI_17; §2 Flow C §3.1
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - on_submit MUST be an async generator function (inspect.isasyncgenfunction).
# - Each yield from on_submit MUST produce exactly a 6-element tuple.
# - thread_id in each yield MUST equal the thread_id passed in (AC_UI_17).
# - No gradio server started during any test (N4 invariant — verified by import check).
# - After awaiting_user event, new_mode in yield tuple MUST be "awaiting_user_answer" (AC_UI_09).
# END_INVARIANTS
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; on_submit headless tests; mode transition tests;
#              thread_id continuity test (AC_UI_17); no-gradio-import assertion (AC_UI_04).
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# (Format: TYPE [Weight 1-10] [Entity description in English] => [entity_name_latin])
# TEST 5 [Verifies on_submit is an async generator function satisfying Gradio streaming contract] => test_on_submit_is_async_generator
# TEST 8 [AC_UI_09 — on_submit with awaiting_user event yields tuple with mode=awaiting_user_answer] => test_on_submit_transitions_to_awaiting_user_answer
# TEST 7 [AC_UI_17 — thread_id in every yield tuple equals the thread_id passed in to on_submit] => test_on_submit_thread_id_continuity
# TEST 7 [Verifies mode=awaiting_user_answer dispatches to orchestrate_resume and returns mode=awaiting_submit] => test_on_submit_resume_mode_dispatches_to_orchestrate_resume
# TEST 6 [AC_UI_04/AC_UI_11 — gradio not imported in controllers/presenter; no .launch() calls in test files] => test_no_gradio_import_in_controllers_or_presenter
# TEST 6 [Verifies every yield from on_submit produces exactly a 6-element tuple] => test_on_submit_each_tuple_has_six_elements
# END_MODULE_MAP

import inspect
import sys
from pathlib import Path

import pytest

_BRAINSTORM_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_BRAINSTORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAINSTORM_ROOT))

import src.ui.controllers as controllers_module
import src.ui.presenter as presenter_module
from src.ui.app import on_submit


# START_FUNCTION_test_on_submit_is_async_generator
def test_on_submit_is_async_generator():
    """
    Verify on_submit is an async generator function.
    This satisfies the Gradio streaming handler contract.
    """
    assert inspect.isasyncgenfunction(on_submit), (
        "on_submit must be an async generator function (uses 'yield' inside async def)"
    )
# END_FUNCTION_test_on_submit_is_async_generator


# START_FUNCTION_test_on_submit_transitions_to_awaiting_user_answer
# START_CONTRACT:
# PURPOSE: AC_UI_09 — after awaiting_user event, on_submit yields final tuple with
#          new_mode=="awaiting_user_answer" and chat history last message is an assistant
#          turn containing the mocked question string.
# INPUTS: monkeypatch, caplog, ldd_capture fixtures.
# KEYWORDS: [PATTERN(9): Monkeypatch; CONCEPT(9): AsyncGenerator; CONCEPT(8): ModeTransition]
# COMPLEXITY_SCORE: 7
# END_CONTRACT
@pytest.mark.asyncio
async def test_on_submit_transitions_to_awaiting_user_answer(monkeypatch, caplog, ldd_capture):
    """
    AC_UI_09: Verify that when on_submit processes an awaiting_user UIEvent, it yields
    a tuple with new_mode=="awaiting_user_answer" and the last assistant message contains
    the question text.

    Monkeypatches src.ui.app.orchestrate_start to yield a scripted 3-event sequence:
    session_started → node_completed → awaiting_user.
    """
    caplog.set_level("INFO")

    test_question = "Which factor is most important: cost, flexibility, or security?"
    test_thread_id = "test_tid_ac09"

    # START_BLOCK_FAKE_ORCHESTRATE: [Define fake orchestrate_start]
    async def fake_orchestrate_start(user_input, thread_id, checkpoint_path=None):
        yield {"kind": "session_started", "thread_id": thread_id}
        yield {
            "kind": "node_completed",
            "node": "1_Context_Analyzer",
            "state_delta": {"dilemma": "buy or rent"},
            "thread_id": thread_id,
        }
        yield {
            "kind": "awaiting_user",
            "question": test_question,
            "thread_id": thread_id,
        }

    monkeypatch.setattr("src.ui.app.orchestrate_start", fake_orchestrate_start)
    # END_BLOCK_FAKE_ORCHESTRATE

    # START_BLOCK_COLLECT_TUPLES: [Collect all yield tuples from on_submit]
    tuples = []
    async for t in on_submit(
        user_input="buy or rent?",
        thread_id=test_thread_id,
        mode="awaiting_submit",
        chat_history=[],
        state_snapshot={},
    ):
        tuples.append(t)
    # END_BLOCK_COLLECT_TUPLES

    # START_BLOCK_LDD_TELEMETRY: [Print trajectory BEFORE asserts — Anti-Illusion]
    high_imp = ldd_capture()
    print(f"\n[AC_UI_09] Total yield tuples from on_submit: {len(tuples)}")
    for i, t in enumerate(tuples):
        print(f"  yield[{i}]: new_mode={t[3]!r} thread_id={t[4]!r} tuple_len={len(t)}")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_ASSERTIONS: [Shape, mode transition, thread_id, question content checks]
    assert len(tuples) >= 1, "on_submit must yield at least 1 tuple"

    # Each tuple must have exactly 6 elements: (chat_history, state_json, status_md, mode, thread_id, textbox)
    for i, t in enumerate(tuples):
        assert len(t) == 6, (
            f"Yield tuple[{i}] must have exactly 6 elements, got {len(t)}"
        )

    # The LAST tuple must have new_mode == "awaiting_user_answer"
    last_tuple = tuples[-1]
    new_mode = last_tuple[3]
    assert new_mode == "awaiting_user_answer", (
        f"AC_UI_09: Last yield must have mode='awaiting_user_answer', got {new_mode!r}"
    )

    # The last tuple's chat_history must have an assistant turn with the question
    last_chat_history = last_tuple[0]
    assistant_msgs = [m for m in last_chat_history if m.get("role") == "assistant"]
    assert len(assistant_msgs) >= 1, (
        "Chat history must contain at least 1 assistant message after awaiting_user"
    )
    last_assistant = assistant_msgs[-1]
    assert test_question in last_assistant["content"], (
        f"Question must be in last assistant message: {last_assistant['content']!r}"
    )
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_on_submit_transitions_to_awaiting_user_answer


# START_FUNCTION_test_on_submit_thread_id_continuity
@pytest.mark.asyncio
async def test_on_submit_thread_id_continuity(monkeypatch, caplog, ldd_capture):
    """
    AC_UI_17: Verify that the thread_id in each yield tuple from on_submit equals
    the thread_id passed in. on_submit must NOT generate a new thread_id.
    """
    caplog.set_level("INFO")

    test_thread_id = "thread_continuity_test_001"

    # START_BLOCK_FAKE_ORCHESTRATE: [Minimal 2-event fake]
    async def fake_orchestrate(user_input, thread_id, checkpoint_path=None):
        yield {"kind": "session_started", "thread_id": thread_id}
        yield {"kind": "awaiting_user", "question": "Question?", "thread_id": thread_id}

    monkeypatch.setattr("src.ui.app.orchestrate_start", fake_orchestrate)
    # END_BLOCK_FAKE_ORCHESTRATE

    # START_BLOCK_COLLECT: [Collect tuples]
    tuples = []
    async for t in on_submit(
        user_input="test dilemma",
        thread_id=test_thread_id,
        mode="awaiting_submit",
        chat_history=[],
        state_snapshot={},
    ):
        tuples.append(t)
    # END_BLOCK_COLLECT

    # START_BLOCK_LDD_TELEMETRY: [Print IMP logs]
    ldd_capture()
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_ASSERTIONS: [Thread ID must equal input in all yields]
    for i, t in enumerate(tuples):
        yielded_thread_id = t[4]  # 5th element is thread_id
        assert yielded_thread_id == test_thread_id, (
            f"AC_UI_17: yield[{i}] thread_id={yielded_thread_id!r} != expected {test_thread_id!r}"
        )
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_on_submit_thread_id_continuity


# START_FUNCTION_test_on_submit_resume_mode_dispatches_to_orchestrate_resume
@pytest.mark.asyncio
async def test_on_submit_resume_mode_dispatches_to_orchestrate_resume(monkeypatch, caplog, ldd_capture):
    """
    Verify that when on_submit is called with mode=="awaiting_user_answer", it dispatches
    to orchestrate_resume (not orchestrate_start). The final mode in the yield tuple must
    transition to "awaiting_submit" after final_answer.
    """
    caplog.set_level("INFO")

    final_text = "## Final Decision\n\nRenting is recommended."
    test_thread_id = "thread_resume_dispatch_test"

    # START_BLOCK_FAKE_RESUME: [Fake orchestrate_resume]
    resume_called = []

    async def fake_orchestrate_resume(user_answer, thread_id, checkpoint_path=None):
        resume_called.append(True)
        yield {"kind": "resume_started", "thread_id": thread_id}
        yield {"kind": "final_answer", "final_answer": final_text, "thread_id": thread_id}

    monkeypatch.setattr("src.ui.app.orchestrate_resume", fake_orchestrate_resume)
    # END_BLOCK_FAKE_RESUME

    # START_BLOCK_COLLECT: [Collect tuples from resume mode]
    tuples = []
    async for t in on_submit(
        user_input="cost: 7, flexibility: 8",
        thread_id=test_thread_id,
        mode="awaiting_user_answer",
        chat_history=[{"role": "user", "content": "dilemma"}, {"role": "assistant", "content": "question"}],
        state_snapshot={},
    ):
        tuples.append(t)
    # END_BLOCK_COLLECT

    # START_BLOCK_LDD_TELEMETRY: [Print trajectory]
    ldd_capture()
    print(f"\n[resume_dispatch] Total yields: {len(tuples)}")
    # END_BLOCK_LDD_TELEMETRY

    # START_BLOCK_ASSERTIONS: [Verify resume was called and mode transitioned]
    assert len(resume_called) == 1, (
        "orchestrate_resume must be called exactly once when mode='awaiting_user_answer'"
    )
    assert len(tuples) >= 1, "on_submit must yield at least 1 tuple in resume mode"

    # Last tuple must have mode back to "awaiting_submit" (session done)
    last_tuple = tuples[-1]
    final_mode = last_tuple[3]
    assert final_mode == "awaiting_submit", (
        f"After final_answer, mode must return to 'awaiting_submit', got {final_mode!r}"
    )

    # Chat history must contain the final answer as assistant message
    last_chat = last_tuple[0]
    assistant_msgs = [m for m in last_chat if m.get("role") == "assistant"]
    assert any(final_text in m["content"] for m in assistant_msgs), (
        f"Final answer must appear in chat history. Assistant messages: {[m['content'] for m in assistant_msgs]}"
    )
    # END_BLOCK_ASSERTIONS
# END_FUNCTION_test_on_submit_resume_mode_dispatches_to_orchestrate_resume


# START_FUNCTION_test_no_gradio_import_in_controllers_or_presenter
def test_no_gradio_import_in_controllers_or_presenter():
    """
    AC_UI_04 / AC_UI_11: Verify gradio is not imported in controllers.py or presenter.py.
    This is enforced by checking the module's imported names at test time.
    """
    # START_BLOCK_IMPORT_CHECK: [Inspect module namespaces for gradio references]
    controllers_vars = dir(controllers_module)
    presenter_vars = dir(presenter_module)

    # gradio must not appear as an attribute in these modules
    assert "gr" not in controllers_vars, (
        "AC_UI_04: 'gr' (gradio) must NOT be imported in controllers.py"
    )
    assert "gradio" not in controllers_vars, (
        "AC_UI_04: 'gradio' must NOT be imported in controllers.py"
    )
    assert "gr" not in presenter_vars, (
        "AC_UI_04: 'gr' (gradio) must NOT be imported in presenter.py"
    )
    assert "gradio" not in presenter_vars, (
        "AC_UI_04: 'gradio' must NOT be imported in presenter.py"
    )
    # END_BLOCK_IMPORT_CHECK

    # START_BLOCK_FILE_CHECK: [Source-level check: no demo.launch() / ui.launch() calls in test files]
    # We use a regex that matches actual method call patterns (identifier.launch() only),
    # not string literals containing the text '.launch('.
    import re as _re

    # Pattern: word_char(s) followed by '.launch(' — matches demo.launch( ui.launch( etc.
    # Excludes string literals and comments by scanning only code portions of each line.
    _LAUNCH_CALL_PATTERN = _re.compile(r'\w+\.launch\s*\(')

    tests_ui_dir = Path(__file__).parent
    # Exclude this file itself from the scan (it contains the pattern as a regex string)
    this_file_name = Path(__file__).name
    for test_file in tests_ui_dir.glob("test_*.py"):
        if test_file.name == this_file_name:
            continue  # Skip self-reference
        source_lines = test_file.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(source_lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # Skip pure comment lines
            code_part = line.split("#")[0]  # Only inspect the code portion before any comment
            if _LAUNCH_CALL_PATTERN.search(code_part):
                assert False, (
                    f"AC_UI_11: test file {test_file.name} line {lineno} must NOT call .launch(). "
                    f"Gradio must never be launched in tests. Line: {line.strip()!r}"
                )
    # END_BLOCK_FILE_CHECK
# END_FUNCTION_test_no_gradio_import_in_controllers_or_presenter


# START_FUNCTION_test_on_submit_each_tuple_has_six_elements
@pytest.mark.asyncio
async def test_on_submit_each_tuple_has_six_elements(monkeypatch, caplog, ldd_capture):
    """
    Verify that every single yield from on_submit produces exactly a 6-element tuple:
    (chat_history, state_snapshot, status_md, mode, thread_id, textbox_value).

    Uses a 3-event fake generator (session_started, node_completed, awaiting_user).
    """
    caplog.set_level("INFO")

    # START_BLOCK_FAKE: [Simple 3-event fake]
    async def fake_three_events(user_input, thread_id, checkpoint_path=None):
        yield {"kind": "session_started", "thread_id": thread_id}
        yield {
            "kind": "node_completed",
            "node": "1_Context_Analyzer",
            "state_delta": {"dilemma": "test"},
            "thread_id": thread_id,
        }
        yield {"kind": "awaiting_user", "question": "Test question?", "thread_id": thread_id}

    monkeypatch.setattr("src.ui.app.orchestrate_start", fake_three_events)
    # END_BLOCK_FAKE

    # START_BLOCK_COLLECT: [Collect and verify all tuples]
    tuples = []
    async for t in on_submit(
        user_input="test",
        thread_id="tid_six",
        mode="awaiting_submit",
        chat_history=[],
        state_snapshot={},
    ):
        tuples.append(t)
        # Assert IMMEDIATELY on each yield for precise failure attribution
        assert len(t) == 6, (
            f"Each yield must have exactly 6 elements, got {len(t)} at yield {len(tuples)-1}"
        )
    # END_BLOCK_COLLECT

    # START_BLOCK_LDD_TELEMETRY: [Print trajectory]
    ldd_capture()
    # END_BLOCK_LDD_TELEMETRY

    assert len(tuples) == 3, f"Expected 3 yield tuples (one per event), got {len(tuples)}"
# END_FUNCTION_test_on_submit_each_tuple_has_six_elements
