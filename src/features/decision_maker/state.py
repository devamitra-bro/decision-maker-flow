# FILE: src/features/decision_maker/state.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT:
# PURPOSE: Defines the canonical LangGraph state TypedDict for the Decision Maker feature.
# SCOPE: Single source of truth for state shape; consumed by nodes, graph, and tests.
# INPUT: None (type definition module only).
# OUTPUT: DecisionMakerState TypedDict with exactly 13 fields per AC4.
# KEYWORDS: [DOMAIN(9): StateSchema; CONCEPT(8): TypedDict; TECH(7): LangGraph; PATTERN(6): DataContract]
# LINKS_TO_SPECIFICATION: DevelopmentPlan §3.1 (state_py), §5.2, AC4; scenario_1_flow.xml State_Schema
# END_MODULE_CONTRACT
#
# START_INVARIANTS:
# - DecisionMakerState has EXACTLY 9 task-mandated fields + 4 operational fields = 13 total.
# - rewrite_count is always int (0-based; incremented by cove_critique on rewrite decision).
# - tool_facts is always list (may be empty list, never None).
# - weights is always dict (may be empty dict, never None).
# END_INVARIANTS
#
# START_RATIONALE:
# Q: Why are all fields typed as Optional[...]?
# A: LangGraph initialises a new state dict with only the fields provided by the first
#    invoke() call. Unset fields would cause KeyError unless typed as Optional with a
#    None-capable default. Graph nodes defensively check for None where needed.
# Q: Why separate user_input (task-mandated) from user_answer (operational)?
# A: user_input is the initial problem statement (seeded once at start_session).
#    user_answer is the human-in-the-loop reply injected via resume_session after
#    the interrupt. They serve different state lifecycles and must not be conflated.
# END_RATIONALE
#
# START_CHANGE_SUMMARY:
# LAST_CHANGE: v1.0.0 - Initial definition; 9 task-mandated + 4 operational fields per AC4.
# END_CHANGE_SUMMARY
#
# START_MODULE_MAP:
# CLASS 10 [LangGraph TypedDict state for Decision Maker — 13 fields] => DecisionMakerState
# END_MODULE_MAP

from typing import Dict, List, Optional
from typing_extensions import TypedDict


class DecisionMakerState(TypedDict, total=False):
    """
    Canonical LangGraph state for the Decision Maker (Scenario 1) graph.

    Contains 9 task-mandated fields sourced from scenario_1_flow.xml State_Schema
    plus 4 operational fields required for graph mechanics (human-in-the-loop,
    tool routing, final output, and assumption transparency).

    All fields are Optional to allow partial state initialization at graph boot.
    """

    # --- 9 Task-Mandated Fields (from scenario_1_flow.xml State_Schema) ---

    # The raw user input string seeded at session start (never mutated after boot).
    user_input: Optional[str]

    # Concise formulation of the decision dilemma extracted by Node 1.
    dilemma: Optional[str]

    # Criteria-to-weight mapping parsed by Node 3.5 (e.g. {"stability": 8, "cost": 5}).
    weights: Optional[Dict[str, int]]

    # Accumulated facts from external tool calls made by Node 2.
    tool_facts: Optional[List]

    # Last calibration question emitted by Node 3 (persisted for Node 3.5 context).
    last_question: Optional[str]

    # Raw draft analysis text produced by Node 4.
    draft_analysis: Optional[str]

    # Critique feedback string from Node 5 (CoVe Auditor) for Node 4 rewrites.
    critique_feedback: Optional[str]

    # Counter of Node 4 rewrites; Anti-Loop cap is 2 (incremented only when rewrite).
    rewrite_count: Optional[int]

    # Boolean flag set by Node 1 indicating data sufficiency for routing decisions.
    is_data_sufficient: Optional[bool]

    # --- 4 Operational Fields (required for graph mechanics) ---

    # Human-in-the-loop answer injected by resume_session after Node 3 interrupt.
    user_answer: Optional[str]

    # List of search query strings identified by Node 1 (fed to Node 2).
    search_queries: Optional[List[str]]

    # Final polished Markdown answer packaged by Node 6 (output of the session).
    final_answer: Optional[str]

    # Transparent assumptions recorded when Node 3.5 forces a decision on user demand.
    assumptions: Optional[str]
