# Test Guide — Decision Maker Agentic UI (tests/ui/)

**Prepared for:** QA Subagent (mode-qa)
**Feature:** Gradio-based Agentic UX — `src/ui/` package
**Version:** 1.0.0
**Date:** 2026-04-21

---

## 1. Test Runner Commands

### New UI tests (primary):
```bash
cd /Users/a1111/Dev/CrabLink/flows/brainstorm
python3.12 -m pytest tests/ui/ -s -v
```

### Backend regression tests (AC_UI_14):
```bash
cd /Users/a1111/Dev/CrabLink/flows/brainstorm
python3.12 -m pytest src/features/decision_maker/tests/ -s -v
```

Expected results:
- `tests/ui/` → **19 passed**
- `src/features/decision_maker/tests/` → **40 passed** (same as pre-UI baseline)

---

## 2. Expected Test Output

### tests/ui/ (19 tests)

| Module | Test | AC |
|--------|------|----|
| test_presenter.py | test_render_returns_correct_tuple_shape | AC_UI_10 |
| test_presenter.py | test_session_started_resets_snapshot | AC_UI_10 |
| test_presenter.py | test_awaiting_user_appends_question | AC_UI_10 |
| test_presenter.py | test_cove_rewrite_appends_critique_to_chat | AC_UI_10 |
| test_presenter.py | test_final_answer_appends_answer | AC_UI_10 |
| test_presenter.py | test_node_completed_merges_delta_into_snapshot | AC_UI_10 |
| test_presenter.py | test_render_does_not_mutate_inputs | AC_UI_10 |
| test_presenter.py | test_filter_state_truncates_long_strings | AC_UI_10 |
| test_presenter.py | test_unknown_event_kind_passes_through | AC_UI_10 |
| test_state_streaming.py | test_three_updates_produce_three_uievents | AC_UI_07 |
| test_state_streaming.py | test_cove_rewrite_emits_dedicated_event | AC_UI_08 |
| test_state_streaming.py | test_render_pipeline_produces_chatbot_updates | AC_UI_07 |
| test_state_streaming.py | test_orchestrate_resume_produces_final_answer_event | AC_UI_07 |
| test_ui_handlers.py | test_on_submit_is_async_generator | AC_UI_01 |
| test_ui_handlers.py | test_on_submit_transitions_to_awaiting_user_answer | AC_UI_09 |
| test_ui_handlers.py | test_on_submit_thread_id_continuity | AC_UI_17 |
| test_ui_handlers.py | test_on_submit_resume_mode_dispatches_to_orchestrate_resume | AC_UI_05 |
| test_ui_handlers.py | test_no_gradio_import_in_controllers_or_presenter | AC_UI_04, AC_UI_11 |
| test_ui_handlers.py | test_on_submit_each_tuple_has_six_elements | AC_UI_09 |

---

## 3. Critical LDD Log Markers to Verify

After running tests, grep the captured output for these markers:

```
[BeliefState][IMP:9][orchestrate_start][BLOCK_EMIT_SESSION_STARTED]
[BeliefState][IMP:9][orchestrate_start][BLOCK_AWAITING_USER]
[BeliefState][IMP:9][orchestrate_resume][BLOCK_EMIT_RESUME_STARTED]
[BeliefState][IMP:9][orchestrate_resume][BLOCK_FINAL_ANSWER]
[UIEvent][IMP:8][orchestrate_start][BLOCK_COVE_REWRITE_CHECK]
[UIEvent][IMP:7][on_submit][BLOCK_DISPATCH_AND_STREAM]
[BeliefState][IMP:9][on_submit][BLOCK_DISPATCH_AND_STREAM]
```

These logs indicate the algorithm trajectory is working. If any IMP:9 marker is absent
from a test run, the Anti-Illusion assertion in the test will fail before business asserts.

---

## 4. Acceptance Criteria Verification Checklist

| ID | Description | How to Verify | Status |
|----|-------------|---------------|--------|
| AC_UI_01 | Directory layout | `ls tests/ui/` and `ls src/ui/` | PASS |
| AC_UI_02 | Core immutability | `git diff` on prompts/state/nodes/tools (zero diff) | PASS |
| AC_UI_03 | Parallel API | `from src.features.decision_maker import stream_session, stream_resume_session; inspect.isasyncgenfunction(...)` | PASS |
| AC_UI_04 | No gradio leak | `grep -r "import gradio" src/features/ src/ui/controllers.py src/ui/presenter.py` → empty | PASS |
| AC_UI_05 | Semantic exoskeleton | Read any new .py file — must have MODULE_CONTRACT, MODULE_MAP, CHANGE_SUMMARY | PASS |
| AC_UI_06 | LDD logging | Run tests with `-s` and grep for `[IMP:9]` in output | PASS |
| AC_UI_07 | Stream fidelity | `test_three_updates_produce_three_uievents` PASS | PASS |
| AC_UI_08 | CoVe visibility | `test_cove_rewrite_emits_dedicated_event` PASS — cove_rewrite before node_completed | PASS |
| AC_UI_09 | HITL gate | `test_on_submit_transitions_to_awaiting_user_answer` PASS | PASS |
| AC_UI_10 | Presenter purity | All test_presenter.py tests PASS | PASS |
| AC_UI_11 | Headless constraint | `test_no_gradio_import_in_controllers_or_presenter` PASS — no .launch() in test files | PASS |
| AC_UI_12 | Dependency hygiene | `grep "gradio" requirements.txt` → single line `gradio==5.9.1` | PASS |
| AC_UI_13 | Anti-Loop Protocol | `cat tests/ui/.test_counter.json` → `{"failures": 0}` after successful run | PASS |
| AC_UI_14 | Existing suite unaffected | `python3.12 -m pytest src/features/decision_maker/tests/ -s -v` → 40 passed | PASS |
| AC_UI_15 | Launcher smoke | `python3.12 -c "import sys; sys.path.insert(0,'.'); from src.ui.app import build_ui; print('OK')"` | PASS |
| AC_UI_16 | Centralised log | `ls decision_maker.log` exists — no new .log files | PASS |
| AC_UI_17 | thread_id continuity | `test_on_submit_thread_id_continuity` PASS | PASS |

---

## 5. Negative Constraints Verification

- **N1:** `prompts.py`, `state.py`, `nodes.py`, `tools.py` — unchanged (verify via `git diff` or direct compare).
- **N2:** `graph.py` — only appended two new functions at end + header bumps. No lines removed from original 372 lines.
- **N3:** `grep -r "import gradio\|from gradio" src/features/ src/ui/controllers.py src/ui/presenter.py` → empty.
- **N4:** No `.launch(` calls in any test file (enforced by `test_no_gradio_import_in_controllers_or_presenter`).
- **N6:** `AppGraph.xml` — NOT modified.
- **N7:** No new `.log` files created — only `decision_maker.log` used.

---

## 6. Files Created / Modified

### New files:
- `src/ui/__init__.py` (v1.0.0)
- `src/ui/controllers.py` (v1.0.0)
- `src/ui/presenter.py` (v1.0.0)
- `src/ui/app.py` (v1.0.0)
- `tests/__init__.py` (v1.0.0)
- `tests/ui/__init__.py` (v1.0.0)
- `tests/ui/conftest.py` (v1.0.0)
- `tests/ui/test_presenter.py` (v1.0.0)
- `tests/ui/test_state_streaming.py` (v1.0.0)
- `tests/ui/test_ui_handlers.py` (v1.0.0)
- `tests/ui/.test_counter.json`
- `tests/ui/test_guide.md` (this file)
- `scripts/run_brainstorm_ui.py` (v1.0.0)

### Modified files (additive only):
- `src/features/decision_maker/graph.py` — VERSION 2.0.0 → 3.0.0; appended `stream_session` and `stream_resume_session`
- `src/features/decision_maker/__init__.py` — VERSION 1.0.0 → 1.1.0; `__all__` extended with streaming functions
- `requirements.txt` — appended `gradio==5.9.1` under v3.0.0 section
