# FILE: src/features/decision_maker/__init__.py
# VERSION: 1.1.0
# START_MODULE_CONTRACT:
# PURPOSE: Public API re-export for the decision_maker feature slice.
# SCOPE: Re-exports start_session, resume_session (existing), and the new streaming parallel
#        API functions stream_session, stream_resume_session (additive, v1.1.0) so callers
#        import from the feature package directly without knowing internal graph module structure.
# INPUT: None.
# OUTPUT: start_session, resume_session, stream_session, stream_resume_session — the four
#         public session functions.
# KEYWORDS: [DOMAIN(7): PublicAPI; CONCEPT(6): Facade; PATTERN(5): ReExport;
#            PATTERN(8): ParallelPublicAPI]
# LINKS: [READS_DATA_FROM(8): src.features.decision_maker.graph]
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.1.0 - Additive: re-exports stream_session and stream_resume_session alongside
#              existing start_session and resume_session. __all__ extended accordingly.
# PREV_CHANGE_SUMMARY: v1.0.0 - Initial creation; re-exports start_session and resume_session.
# END_CHANGE_SUMMARY

from src.features.decision_maker.graph import (
    resume_session,
    start_session,
    stream_resume_session,
    stream_session,
)

__all__ = ["start_session", "resume_session", "stream_session", "stream_resume_session"]
