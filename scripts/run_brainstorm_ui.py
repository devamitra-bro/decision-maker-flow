# FILE: scripts/run_brainstorm_ui.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Operator entry-point for the Decision Maker Agentic UI. Mirrors smoke_run.py
#          topology exactly: project-root sys.path injection, load_dotenv, env-var sanity check,
#          lazy import of gradio and build_ui, launch on 127.0.0.1 with inbrowser=True,
#          wrapped in try/except KeyboardInterrupt.
# SCOPE: One-shot CLI; not part of pytest suite. Launches a local Gradio web server.
# INPUT: None (reads .env from brainstorm/).
# OUTPUT: Gradio web server on http://127.0.0.1:7860 (default port); opens browser tab.
# KEYWORDS: [DOMAIN(7): CLI; TECH(10): Gradio5; PATTERN(8): LazyImport; CONCEPT(8): EnvVarSanity;
#            PATTERN(7): MirrorSmokeRun]
# LINKS: [USES_API(10): src.ui.build_ui; USES_API(8): python-dotenv;
#         LINKS_TO_SPECIFICATION: DevelopmentPlan_UI.md §1 (scripts_run_brainstorm_ui_py); P2]
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; mirrors smoke_run.py structure; lazy gradio import.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 9 [Main: env-check + lazy import + gradio launch] => main
# END_MODULE_MAP
#
# START_USE_CASES:
# - [main]: Operator -> RunBrainstormUI -> AgenticUXLaunchedInBrowser
# END_USE_CASES

import os
import sys
from pathlib import Path

# Ensure the brainstorm/ project root is on sys.path so `src.features...` imports work
# Mirror of smoke_run.py: _PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Load .env BEFORE importing any module that reads env at import time
_ENV_PATH = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_PATH, override=True)

# Sanity-check the three required env vars before touching the graph (same pattern as smoke_run.py)
_REQUIRED_KEYS = ["OPENROUTER_API_KEY", "OPENROUTER_MODEL", "TAVILY_API_KEY"]
for _k in _REQUIRED_KEYS:
    _v = os.getenv(_k, "")
    if not _v or _v.endswith("...") or _v.endswith("tvly-") or _v.endswith("sk-or-v1-"):
        print(f"[UI][FATAL] Env var {_k} missing or looks like a placeholder — refusing to launch.")
        sys.exit(2)
    _preview = _v[:6] + "...<REDACTED>" if len(_v) > 6 else "<REDACTED>"
    print(f"[UI][ENV] {_k}={_preview}")


# START_FUNCTION_main
# START_CONTRACT:
# PURPOSE: Lazy-import gradio and build_ui, then launch the Gradio server on 127.0.0.1.
#          Lazy import ensures env sanity check runs BEFORE any heavy module-load side effects.
# INPUTS: None.
# OUTPUTS: None (blocks until server shuts down or KeyboardInterrupt).
# SIDE_EFFECTS: Launches Gradio web server; opens browser tab; writes to decision_maker.log.
# KEYWORDS: [PATTERN(8): LazyImport; TECH(10): Gradio5; CONCEPT(7): BrowserLaunch]
# COMPLEXITY_SCORE: 3
# END_CONTRACT
def main() -> None:
    """
    Entry-point function for the Brainstorm Agentic UI.

    Uses lazy imports for gradio and build_ui so that the env-var sanity check (above)
    runs before any Gradio or feature-core code is loaded. This prevents cryptic import
    errors when env vars are missing.

    Launches the Gradio server on 127.0.0.1 with inbrowser=True so the browser opens
    automatically. server_name="127.0.0.1" restricts to localhost (operator safety).
    """

    # START_BLOCK_LAZY_IMPORT: [Lazy imports after env check]
    import gradio as gr  # noqa: F401 — imported to verify installation
    from src.ui.app import build_ui
    # END_BLOCK_LAZY_IMPORT

    # START_BLOCK_LAUNCH: [Build UI and launch server]
    print(f"[UI][BOOT] Building Decision Maker Agentic UI...")
    demo = build_ui()

    print(f"[UI][BOOT] Launching Gradio server on http://127.0.0.1:7860 ...")
    demo.launch(
        server_name="127.0.0.1",
        inbrowser=True,
    )
    # END_BLOCK_LAUNCH
# END_FUNCTION_main


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[UI][SHUTDOWN] KeyboardInterrupt received — shutting down gracefully.")
    except Exception as exc:
        print(f"\n[UI][FATAL] Unhandled exception: {type(exc).__name__}: {exc}")
        raise
