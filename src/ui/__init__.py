# FILE: src/ui/__init__.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Public API marker for the src/ui package. Re-exports build_ui() factory
#          from app.py for use by scripts/run_brainstorm_ui.py and any external callers.
# SCOPE: Package initialiser. Only build_ui is part of the public surface.
#        Internal controllers and presenter are implementation detail — not exported.
# INPUT: None.
# OUTPUT: build_ui — factory returning gr.Blocks instance.
# KEYWORDS: [DOMAIN(7): PublicAPI; CONCEPT(6): Facade; PATTERN(5): ReExport;
#            TECH(8): Gradio5]
# LINKS: [READS_DATA_FROM(8): src.ui.app]
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial creation; re-exports build_ui from src.ui.app.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# (Format: TYPE [Weight 1-10] [Entity description in English] => [entity_name_latin])
# FUNC 7 [Re-export of build_ui factory from src.ui.app — public surface of this package] => build_ui
# END_MODULE_MAP

from src.ui.app import build_ui

__all__ = ["build_ui"]
