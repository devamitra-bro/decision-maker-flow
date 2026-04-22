# FILE: scripts/smoke_run.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: End-to-end smoke test for the async decision_maker infrastructure against REAL
#          external services (OpenRouter LLM + Tavily search). Exercises start_session and
#          resume_session, prints the returned dicts, and summarises LDD telemetry.
# SCOPE: One-shot CLI; not part of pytest suite. Uses a temp SQLite checkpoint so the
#        production DB (brainstorm/checkpoints.sqlite) stays untouched.
# INPUT: None (reads .env from brainstorm/).
# OUTPUT: stdout log + decision_maker.log on disk.
# KEYWORDS: [DOMAIN(7): SmokeTest; CONCEPT(9): AsyncIO; TECH(9): OpenRouter; TECH(9): Tavily;
#            PATTERN(8): EndToEnd; PATTERN(7): IntegrationTest]
# LINKS: [USES_API(9): src.features.decision_maker.start_session;
#         USES_API(9): src.features.decision_maker.resume_session;
#         USES_API(8): python-dotenv]
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial smoke runner for real-network verification of async migration.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Async end-to-end: start -> canned answer -> resume -> print] => run_smoke
# FUNC 7 [Read tail of decision_maker.log and print IMP:7-10 lines] => dump_ldd_tail
# END_MODULE_MAP

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

# Ensure the brainstorm/ project root is on sys.path so `src.features...` imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Load .env BEFORE importing any module that reads env at import time
_ENV_PATH = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_PATH, override=True)

# Sanity-check the two required keys before we touch the graph
_REQUIRED_KEYS = ["OPENROUTER_API_KEY", "OPENROUTER_MODEL", "TAVILY_API_KEY"]
for _k in _REQUIRED_KEYS:
    _v = os.getenv(_k, "")
    if not _v or _v.endswith("...") or _v.endswith("tvly-") or _v.endswith("sk-or-v1-"):
        print(f"[SMOKE][FATAL] Env var {_k} missing or looks like a placeholder — refusing to run.")
        sys.exit(2)
    _preview = _v[:6] + "...<REDACTED>" if len(_v) > 6 else "<REDACTED>"
    print(f"[SMOKE][ENV] {_k}={_preview}")

from src.features.decision_maker.graph import start_session, resume_session  # noqa: E402


# START_FUNCTION_dump_ldd_tail
# START_CONTRACT:
# PURPOSE: Extract and print the last N lines of decision_maker.log with IMP:7-10 markers.
# INPUTS:
# - Path to log file => log_path: Path
# - Number of recent lines to scan => tail_lines: int (default 200)
# OUTPUTS: None (prints filtered lines to stdout).
# SIDE_EFFECTS: Reads log file from disk.
# KEYWORDS: [PATTERN(7): LDDFilter; TECH(6): FileRead]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def dump_ldd_tail(log_path: Path, tail_lines: int = 400) -> None:
    """
    Print the trailing LDD-relevant lines (IMP:7-10) from the smoke run so the operator
    can visually verify per-query IMP:7/IMP:8 pairing, Anti-Loop triggers, and state writes.
    """
    if not log_path.exists():
        print(f"[SMOKE][LDD] Log file {log_path} not found — nothing to dump.")
        return

    lines = log_path.read_text(encoding="utf-8").splitlines()
    tail = lines[-tail_lines:]
    relevant = [ln for ln in tail if any(m in ln for m in ("[IMP:7]", "[IMP:8]", "[IMP:9]", "[IMP:10]"))]

    print("\n============== LDD tail (IMP:7-10 markers from this run) ==============")
    for ln in relevant:
        print(ln)
    print(f"============== ({len(relevant)} lines printed) ==============\n")
# END_FUNCTION_dump_ldd_tail


# START_FUNCTION_run_smoke
# START_CONTRACT:
# PURPOSE: Execute a full async session against real OpenRouter + real Tavily.
# INPUTS: None.
# OUTPUTS: None (prints results; returns via sys.exit).
# SIDE_EFFECTS: Creates /tmp smoke checkpoint DB; invokes real LLM + real Tavily; appends to decision_maker.log.
# KEYWORDS: [PATTERN(9): EndToEnd; CONCEPT(10): AsyncIO; CONCEPT(8): HumanInTheLoop]
# COMPLEXITY_SCORE: 6
# END_CONTRACT
async def run_smoke() -> None:
    """
    1) Call start_session with a realistic dilemma — the graph will call LLM and (likely)
       issue parallel Tavily searches before interrupting after Node 3 with a question.
    2) Feed a canned user_answer back into resume_session — the graph will parse weights,
       draft analysis, run CoVe critique (with Anti-Loop cap), and synthesize the final answer.
    3) Print the full returned dicts and dump IMP:7-10 lines from decision_maker.log.
    """

    # Use a temp checkpoint DB so we don't pollute the production checkpoints.sqlite
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="dm_smoke_"))
    ckpt_path = str(tmp_dir / "smoke_checkpoints.sqlite")
    thread_id = f"smoke_{uuid.uuid4().hex[:8]}"

    print(f"[SMOKE][BOOT] thread_id={thread_id!r}")
    print(f"[SMOKE][BOOT] checkpoint_path={ckpt_path}")
    print(f"[SMOKE][BOOT] OPENROUTER_MODEL={os.getenv('OPENROUTER_MODEL')}")
    print("")

    dilemma = (
        "Я программист, 32 года, Москва. Стоит ли мне в 2026 году покупать квартиру в ипотеку "
        "или продолжать снимать и инвестировать разницу в индексные фонды? "
        "Есть первоначальный взнос ~4 млн руб, зарплата 450к/мес."
    )

    print("============== START_SESSION ==============")
    _t_start_begin = time.perf_counter()
    start_result = await start_session(
        user_input=dilemma,
        thread_id=thread_id,
        checkpoint_path=ckpt_path,
    )
    _t_start_elapsed = time.perf_counter() - _t_start_begin
    print(f"status       = {start_result['status']!r}")
    print(f"thread_id    = {start_result['thread_id']!r}")
    print(f"question     = {start_result['question']}")
    print(f"[TIMING] start_session wall-clock = {_t_start_elapsed:.2f}s")
    print("")

    # Canned human answer — a realistic response to the typical calibration question
    canned_answer = (
        "Финансовая безопасность — 40%, гибкость (возможность переезда/смены работы) — 30%, "
        "доходность капитала — 20%, психологический комфорт — 10%."
    )

    print("============== RESUME_SESSION ==============")
    print(f"Canned user_answer: {canned_answer}")
    print("")
    _t_resume_begin = time.perf_counter()
    resume_result = await resume_session(
        user_answer=canned_answer,
        thread_id=thread_id,
        checkpoint_path=ckpt_path,
    )
    _t_resume_elapsed = time.perf_counter() - _t_resume_begin
    print(f"status       = {resume_result['status']!r}")
    print(f"thread_id    = {resume_result['thread_id']!r}")
    print(f"final_answer (first 800 chars):\n{resume_result['final_answer'][:800]}")
    print(f"[TIMING] resume_session wall-clock = {_t_resume_elapsed:.2f}s")
    print(f"[TIMING] TOTAL (start+resume)      = {_t_start_elapsed + _t_resume_elapsed:.2f}s")
    print("")

    log_path = _PROJECT_ROOT / "decision_maker.log"
    dump_ldd_tail(log_path, tail_lines=600)

    print("[SMOKE][DONE] End-to-end smoke run completed successfully.")
# END_FUNCTION_run_smoke


if __name__ == "__main__":
    try:
        asyncio.run(run_smoke())
    except Exception as exc:
        print(f"\n[SMOKE][FATAL] Unhandled exception: {type(exc).__name__}: {exc}")
        # Still dump the log tail so operator sees where we failed
        try:
            dump_ldd_tail(_PROJECT_ROOT / "decision_maker.log", tail_lines=300)
        except Exception:
            pass
        raise
