# FILE: src/core/logger.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: LDD (Log Driven Development) logger factory for the Decision Maker feature.
# SCOPE: Creates and configures a named logger with dual output (file + stdout).
# INPUT: Optional LDD_LOG_LEVEL environment variable.
# OUTPUT: logging.Logger instance named "decision_maker" with FileHandler + StreamHandler.
# KEYWORDS: [DOMAIN(8): Observability; CONCEPT(9): LDD; TECH(7): PythonLogging; PATTERN(6): Singleton]
# LINKS: [USES_API(7): logging; READS_DATA_FROM(6): os.environ]
# END_MODULE_CONTRACT
#
# START_RATIONALE:
# Q: Why guard with hasHandlers() instead of creating a new logger each call?
# A: Idempotency is critical in test environments where setup_ldd_logger() may be
#    called multiple times (once per module import). Without the guard, duplicate
#    handlers accumulate causing duplicate log lines in tests.
# Q: Why use %(message)s only in the formatter?
#    A: Node/function code is responsible for the full [CLASSIFIER][IMP:N][FN][BLOCK][OP] msg [STATUS]
#    format. The formatter adding its own fields would create redundant double-stamping.
# Q: Why FileHandler at a specific absolute path derived from __file__?
#    A: The log sink must be at brainstorm/decision_maker.log (AC10 hard invariant).
#    Using __file__ keeps this portable without hardcoding a literal absolute path.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial implementation; dual-handler LDD logger with env-driven level.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# FUNC 10 [Factory: creates/returns idempotent named LDD logger] => setup_ldd_logger
# END_MODULE_MAP
#
# START_USE_CASES:
# - [setup_ldd_logger]: NodeFunction -> InitializeLogger -> LDDTelemetryEmittedToDiskAndConsole
# END_USE_CASES

import logging
import os
import sys
from pathlib import Path

# START_FUNCTION_setup_ldd_logger
# START_CONTRACT:
# PURPOSE: Build and return the "decision_maker" named logger with FileHandler pointing to
#          brainstorm/decision_maker.log and StreamHandler pointing to stdout.
# INPUTS: None
# OUTPUTS:
# - logging.Logger — configured and ready-to-use LDD logger
# SIDE_EFFECTS: Creates decision_maker.log file under brainstorm root; adds handlers to
#               the "decision_maker" logger object in the logging module registry.
# KEYWORDS: [PATTERN(6): Singleton; CONCEPT(8): LDD; TECH(7): logging]
# LINKS: [USES_API(7): logging.FileHandler; USES_API(7): logging.StreamHandler]
# COMPLEXITY_SCORE: 4
# END_CONTRACT
def setup_ldd_logger() -> logging.Logger:
    """
    Creates (or retrieves) the canonical LDD logger named "decision_maker".

    The function is idempotent: on the first call it attaches two handlers —
    a FileHandler writing to <brainstorm_root>/decision_maker.log and a
    StreamHandler writing to sys.stdout. On subsequent calls it returns the
    same logger without adding duplicate handlers (guarded by hasHandlers()).

    Log level defaults to INFO but can be overridden at runtime via the
    LDD_LOG_LEVEL environment variable (e.g. LDD_LOG_LEVEL=DEBUG). The
    formatter emits only %(message)s so that the full LDD format string
    [CLASSIFIER][IMP:N][FN][BLOCK][OP] msg [STATUS] is owned entirely by
    the calling node functions, not duplicated by the formatter.

    Log file location: resolved as the grandparent of src/core/ — which is
    the brainstorm root — joined with "decision_maker.log", satisfying AC10.
    """

    logger = logging.getLogger("decision_maker")

    if logger.hasHandlers():
        return logger

    # START_BLOCK_CONFIGURE_LEVEL: [Resolve log level from env or use INFO default]
    level_name = os.environ.get("LDD_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    # END_BLOCK_CONFIGURE_LEVEL

    # START_BLOCK_RESOLVE_LOG_PATH: [Derive brainstorm root from this file's location]
    # __file__ = brainstorm/src/core/logger.py  =>  parent.parent.parent = brainstorm/
    brainstorm_root = Path(__file__).resolve().parent.parent.parent
    log_path = brainstorm_root / "decision_maker.log"
    # END_BLOCK_RESOLVE_LOG_PATH

    # START_BLOCK_ATTACH_HANDLERS: [Build and attach FileHandler + StreamHandler]
    formatter = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    # END_BLOCK_ATTACH_HANDLERS

    logger.info(
        f"[Setup][IMP:6][setup_ldd_logger][BLOCK_ATTACH_HANDLERS][Configure] "
        f"LDD logger initialized. log_path={log_path} level={level_name} [SUCCESS]"
    )

    return logger
# END_FUNCTION_setup_ldd_logger
